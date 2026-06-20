"""
Main runner — this is THE script that runs as one long GitHub Actions job
during market hours (9:15 AM - 3:30 PM IST).

It:
  1. Loads your daily instructions (watchlist + entry/SL/exit rules)
  2. Loads any existing open positions from previous days
  3. Loops every LOOP_INTERVAL_SECONDS until market close:
       - Checks entry conditions for watchlist items not yet entered
       - Checks trailing SL conditions for active trades
       - Checks if any GTT has fired (trade closed)
       - Logs everything to logs/YYYY-MM-DD.log
  4. On exit (market close or max runtime), writes a final summary
     and the loop ends — GitHub Actions job completes, state is
     committed back to the repo by the workflow YAML.

Run with: python3 run_trading_day.py
"""

import time
import logging
import os
import sys
from datetime import datetime
import pytz

import state_store as store
import trade_engine

IST = pytz.timezone("Asia/Kolkata")
LOOP_INTERVAL_SECONDS = 75  # ~1.25 min between checks
MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN = 15, 30
HARD_STOP_HOUR, HARD_STOP_MIN = 15, 35  # small buffer past close to catch final GTT status

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
today_str = datetime.now(IST).strftime("%Y-%m-%d")
LOG_FILE = os.path.join(LOG_DIR, f"{today_str}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_trading_day")


def market_close_time():
    now = datetime.now(IST)
    return now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0, microsecond=0)


def hard_stop_time():
    now = datetime.now(IST)
    return now.replace(hour=HARD_STOP_HOUR, minute=HARD_STOP_MIN, second=0, microsecond=0)


def evaluate_watchlist_entries(instructions):
    """
    Check each watchlist item's entry condition against current price.
    This is intentionally simple (price-trigger based) — your specific
    rules/logic get added here once you share them.
    """
    events = []
    watchlist = instructions.get("watchlist", [])

    for item in watchlist:
        symbol = item.get("symbol")
        if not symbol:
            continue

        # Skip if already have an active trade on this symbol
        if store.get_trade_by_symbol(symbol):
            continue

        # Skip if already attempted today (avoid re-entry loops)
        if item.get("entered_today"):
            continue

        entry_trigger = item.get("entry_price")
        side = item.get("side", "BUY")
        qty = item.get("qty", 1)
        product = item.get("product", "D")

        if entry_trigger is None:
            continue  # no auto-entry condition, manual only

        try:
            from instruments import lookup_token
            import upstox_client as ux
            token = lookup_token(symbol)
            if not token:
                events.append(f"⚠️ No token for {symbol}, skipping")
                continue
            ltp = ux.get_ltp(token)
        except Exception as e:
            events.append(f"⚠️ LTP check failed for {symbol}: {e}")
            continue

        triggered = (side == "BUY" and ltp <= entry_trigger) or (side == "SELL" and ltp >= entry_trigger)
        if triggered:
            result = trade_engine.enter_trade(
                symbol=symbol, side=side, qty=qty, product=product,
                entry_price=ltp,
                sl_pct=item.get("sl_pct"), tgt_pct=item.get("tgt_pct"),
                sl_price=item.get("sl_price"), target_price=item.get("target_price"),
            )
            events.append(result["message"])
            item["entered_today"] = True

    return events


def run():
    logger.info("=" * 60)
    logger.info(f"Trading day started — {today_str}")
    logger.info("=" * 60)

    instructions = store.load_instructions()
    if instructions.get("date") != today_str:
        logger.warning(
            f"Instructions are from {instructions.get('date')}, not today ({today_str}). "
            "Proceeding with existing watchlist anyway — update state/instructions.json for fresh rules."
        )

    close_t = market_close_time()
    stop_t = hard_stop_time()
    loop_count = 0

    while True:
        now = datetime.now(IST)
        if now >= stop_t:
            logger.info("Reached hard stop time. Ending trading day loop.")
            break

        loop_count += 1
        logger.info(f"--- Loop {loop_count} @ {now.strftime('%H:%M:%S')} IST ---")

        try:
            entry_events = evaluate_watchlist_entries(instructions)
            for e in entry_events:
                logger.info(e)

            trail_events = trade_engine.check_and_trail_all()
            for e in trail_events:
                logger.info(e)

            closed_events = trade_engine.check_gtt_fired()
            for e in closed_events:
                logger.info(e)

        except Exception as e:
            logger.exception(f"Error in main loop: {e}")

        # Persist instructions (entered_today flags) and state after each loop
        store.save_instructions(instructions)

        if now >= close_t:
            logger.info("Market closed. Continuing brief monitoring until hard stop for final GTT status...")

        time.sleep(LOOP_INTERVAL_SECONDS)

    # End of day summary
    logger.info("=" * 60)
    logger.info("Trading day summary")
    all_trades = store.get_all_trades()
    today_trades = [t for t in all_trades if t.get("opened_date") == today_str]
    active = [t for t in all_trades if t.get("status") == "active"]
    logger.info(f"Trades opened today: {len(today_trades)}")
    logger.info(f"Still active (multi-day): {len(active)}")
    for t in active:
        logger.info(f"  -> {t['symbol']}: {t['side']} {t['qty']} @ ₹{t['entry']} | SL ₹{t['sl']} | Target ₹{t['target']}")
    logger.info("=" * 60)


if __name__ == "__main__":
    run()
