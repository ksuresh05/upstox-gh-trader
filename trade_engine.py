"""
Trade engine — core logic for evaluating your watchlist rules,
placing entries, managing GTT, and trailing stop-losses.
"""

import time
import logging
from instruments import lookup_token
import upstox_client as ux
import state_store as store

logger = logging.getLogger("trade_engine")

DEFAULT_SL_PCT = 0.01
DEFAULT_TGT_PCT = 0.02
TRAIL_THRESHOLD = 0.50


def calc_levels(side, entry_price, sl_pct=DEFAULT_SL_PCT, tgt_pct=DEFAULT_TGT_PCT):
    if side == "BUY":
        sl = round(entry_price * (1 - sl_pct), 2)
        target = round(entry_price * (1 + tgt_pct), 2)
    else:
        sl = round(entry_price * (1 + sl_pct), 2)
        target = round(entry_price * (1 - tgt_pct), 2)
    return sl, target


def enter_trade(symbol, side, qty, product="D", order_type="MARKET",
                 entry_price=None, sl_pct=None, tgt_pct=None,
                 sl_price=None, target_price=None):
    """
    Place an entry order + GTT. Returns a result dict.
    Used both for manual one-off entries and rule-triggered entries.
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

    sl = sl_price or calc_levels(side, ref_price, sl_pct or DEFAULT_SL_PCT, tgt_pct or DEFAULT_TGT_PCT)[0]
    target = target_price or calc_levels(side, ref_price, sl_pct or DEFAULT_SL_PCT, tgt_pct or DEFAULT_TGT_PCT)[1]

    exit_side = "SELL" if side == "BUY" else "BUY"
    trade_id = f"{symbol}-{int(time.time())}"
    gtt_id = None
    try:
        gtt_id = ux.place_gtt(instrument_token, exit_side, qty, product, target, sl)
    except ux.UpstoxAPIError as e:
        logger.error(f"GTT placement failed for {symbol}: {e}")

    store.add_trade({
        "id": trade_id,
        "symbol": symbol,
        "instrument_token": instrument_token,
        "side": side,
        "qty": qty,
        "entry": ref_price,
        "sl": sl,
        "target": target,
        "product": product,
        "order_id": order_id,
        "gtt_id": gtt_id,
        "status": "active",
        "trail_done": False,
        "opened_date": time.strftime("%Y-%m-%d"),
    })

    return {
        "ok": True,
        "message": f"Entered {side} {qty} {symbol} @ ~₹{ref_price} | SL ₹{sl} | Target ₹{target}",
        "trade_id": trade_id,
    }


def check_and_trail_all():
    """
    Called every loop iteration (every 60-90s during market hours).
    For each active trade: fetch LTP, check trailing condition, modify GTT if needed.
    Returns a list of event strings for logging.
    """
    events = []
    trades = store.get_active_trades()
    if not trades:
        return events

    for trade in trades:
        if trade.get("trail_done") or not trade.get("gtt_id"):
            continue
        try:
            ltp = ux.get_ltp(trade["instrument_token"])
        except ux.UpstoxAPIError as e:
            events.append(f"⚠️ LTP fetch failed for {trade['symbol']}: {e}")
            continue

        side = trade["side"]
        entry = trade["entry"]
        target = trade["target"]
        move = (ltp - entry) if side == "BUY" else (entry - ltp)
        full_move = (target - entry) if side == "BUY" else (entry - target)
        if full_move == 0:
            continue
        pct = move / full_move

        if pct >= TRAIL_THRESHOLD:
            new_sl = entry
            exit_side = "SELL" if side == "BUY" else "BUY"
            try:
                ux.modify_gtt(trade["gtt_id"], trade["instrument_token"], exit_side,
                              trade["qty"], trade["product"], target, new_sl)
                store.update_trade(trade["id"], sl=new_sl, trail_done=True)
                events.append(f"🎯 Trailed SL to breakeven for {trade['symbol']} (LTP ₹{ltp}, new SL ₹{new_sl})")
            except ux.UpstoxAPIError as e:
                events.append(f"⚠️ Failed to trail SL for {trade['symbol']}: {e}")

    return events


def check_gtt_fired():
    """
    Check if any active trade's GTT has fired (target or SL hit).
    Marks the trade as exited in state if so.
    """
    events = []
    trades = store.get_active_trades()
    for trade in trades:
        if not trade.get("gtt_id"):
            continue
        gtt = ux.get_gtt_status(trade["gtt_id"])
        if gtt and gtt.get("status") in ("TRIGGERED", "COMPLETE", "CANCELLED"):
            store.update_trade(trade["id"], status="exited", exit_status=gtt.get("status"))
            events.append(f"✅ {trade['symbol']} GTT {gtt.get('status')} — trade closed")
    return events
