"""
Trade engine — implements the ratcheting target strategy:

  1. Entry: you provide symbol, entry price, quantity, and first target price.
     NO real stop-loss initially -- a far placeholder SL is used instead
     (since Upstox's MULTIPLE GTT requires both legs together).

  2. When target hits (1st time):
       - Real SL is set for the first time: SL = hit_price * (1 - RATCHET_SL_PCT)
       - New target = hit_price * (1 + RATCHET_TARGET_PCT)

  3. Every subsequent target hit: same ratchet -- new SL tightens up,
     new target extends further, riding the trend.

  4. Trade only closes when SL is eventually hit (which can only happen
     after at least one target has already been achieved).
"""

import time
import logging
from instruments import lookup_token
import upstox_client as ux
import state_store as store

logger = logging.getLogger("trade_engine")

# Ratchet parameters (applied AFTER the first target hit, on every hit thereafter)
RATCHET_SL_PCT = 0.01       # new SL = hit_price - 1%
RATCHET_TARGET_PCT = 0.05   # new target = hit_price + 5%

# Placeholder "no real SL" used only during Phase 1 (before first target hit)
PLACEHOLDER_SL_PCT = 0.50   # 50% away -- functionally never triggers on a normal day


def enter_trade(symbol, side, qty, first_target_price, product="D", order_type="MARKET", entry_price=None):
    """
    Place an entry order + initial GTT (target only, functionally no SL).
    first_target_price: you specify this per stock -- the first real target level.
    """
    instrument_token = lookup_token(symbol)
    if not instrument_token:
        return {"ok": False, "message": f"No instrument token known for '{symbol}'"}

    ref_price = entry_price or ux.get_ltp(instrument_token)
    price = ref_price if order_type == "LIMIT" else 0

    order_id = ux.place_order(
        instrument_token=instrument_token,
        transaction_type=side,
        quantity=qty,
        order_type=order_type,
        product=product,
        price=price,
    )

    # Phase 1: placeholder SL far away, real target as specified
    placeholder_sl = (
        round(ref_price * (1 - PLACEHOLDER_SL_PCT), 2) if side == "BUY"
        else round(ref_price * (1 + PLACEHOLDER_SL_PCT), 2)
    )

    exit_side = "SELL" if side == "BUY" else "BUY"
    trade_id = f"{symbol}-{int(time.time())}"
    gtt_id = None
    try:
        gtt_id = ux.place_gtt(instrument_token, exit_side, qty, product, first_target_price, placeholder_sl)
    except ux.UpstoxAPIError as e:
        logger.error(f"GTT placement failed for {symbol}: {e}")

    store.add_trade({
        "id": trade_id,
        "symbol": symbol,
        "instrument_token": instrument_token,
        "side": side,
        "qty": qty,
        "entry": ref_price,
        "sl": placeholder_sl,
        "target": first_target_price,
        "product": product,
        "order_id": order_id,
        "gtt_id": gtt_id,
        "status": "active",
        "has_real_sl": False,       # Phase 1: no real SL yet
        "ratchet_count": 0,         # how many times target has been hit
        "opened_date": time.strftime("%Y-%m-%d"),
    })

    return {
        "ok": True,
        "message": (
            f"Entered {side} {qty} {symbol} @ ~₹{ref_price}\n"
            f"Phase 1: NO real stop-loss yet (placeholder SL ₹{placeholder_sl} -- not expected to trigger)\n"
            f"First target: ₹{first_target_price}"
        ),
        "trade_id": trade_id,
    }


def check_gtt_outcomes():
    """
    Called every loop iteration. For each active trade with a GTT:
      - Check if TARGET or STOPLOSS leg actually fired.
      - If TARGET fired: ratchet -- set real SL, raise target, re-place GTT, keep trade active.
      - If STOPLOSS fired: trade is closed for real, mark exited.
    Returns a list of event strings for logging.
    """
    events = []
    trades = store.get_active_trades()

    for trade in trades:
        if not trade.get("gtt_id"):
            continue

        try:
            leg, hit_price = ux.get_triggered_leg(trade["gtt_id"])
        except ux.UpstoxAPIError as e:
            events.append(f"⚠️ GTT status check failed for {trade['symbol']}: {e}")
            continue

        if leg is None:
            continue  # nothing fired yet, still waiting

        side = trade["side"]
        exit_side = "SELL" if side == "BUY" else "BUY"

        if leg == "STOPLOSS":
            # Real exit -- but only meaningful if we'd actually set a real SL.
            # (If this fires during Phase 1, it's the placeholder -- a genuine
            #  extreme move. Either way, the position is now closed on the exchange.)
            store.update_trade(trade["id"], status="exited", exit_reason="STOPLOSS", exit_price=hit_price)
            phase_note = "(this was the Phase-1 placeholder SL -- an extreme adverse move)" if not trade.get("has_real_sl") else ""
            events.append(f"🛑 {trade['symbol']} STOPLOSS hit @ ₹{hit_price} -- trade closed. {phase_note}")
            continue

        if leg == "TARGET":
            # Ratchet: new real SL, new higher target
            new_ratchet_count = trade.get("ratchet_count", 0) + 1
            if side == "BUY":
                new_sl = round(hit_price * (1 - RATCHET_SL_PCT), 2)
                new_target = round(hit_price * (1 + RATCHET_TARGET_PCT), 2)
            else:
                new_sl = round(hit_price * (1 + RATCHET_SL_PCT), 2)
                new_target = round(hit_price * (1 - RATCHET_TARGET_PCT), 2)

            try:
                # Place a fresh GTT (previous one is now consumed/cancelled by Upstox)
                new_gtt_id = ux.place_gtt(trade["instrument_token"], exit_side, trade["qty"],
                                          trade["product"], new_target, new_sl)
                store.update_trade(
                    trade["id"],
                    sl=new_sl, target=new_target, gtt_id=new_gtt_id,
                    has_real_sl=True, ratchet_count=new_ratchet_count,
                )
                events.append(
                    f"🎯 {trade['symbol']} TARGET #{new_ratchet_count} hit @ ₹{hit_price}! "
                    f"Ratcheted: new SL ₹{new_sl} (-{RATCHET_SL_PCT*100:.0f}%), new target ₹{new_target} (+{RATCHET_TARGET_PCT*100:.0f}%)"
                )
            except ux.UpstoxAPIError as e:
                events.append(f"⚠️ Failed to ratchet GTT for {trade['symbol']} after target hit: {e}")

    return events
