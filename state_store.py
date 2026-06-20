"""
State management for active trades.
Reads/writes state/positions.json — this file is committed back to the
git repo at the end of each run, so it persists across days even though
each GitHub Actions run starts in a completely fresh container.
"""

import json
import os
import logging

logger = logging.getLogger("state_store")

STATE_PATH = os.path.join(os.path.dirname(__file__), "state", "positions.json")
INSTRUCTIONS_PATH = os.path.join(os.path.dirname(__file__), "state", "instructions.json")


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"trades": []}
    with open(STATE_PATH, "r") as f:
        return json.load(f)


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    logger.info(f"State saved: {len(state.get('trades', []))} trades")


def add_trade(trade):
    state = load_state()
    state["trades"].append(trade)
    save_state(state)


def update_trade(trade_id, **updates):
    state = load_state()
    for t in state["trades"]:
        if t["id"] == trade_id:
            t.update(updates)
            break
    save_state(state)


def get_active_trades():
    state = load_state()
    return [t for t in state["trades"] if t.get("status") == "active"]


def get_all_trades():
    state = load_state()
    return state["trades"]


def get_trade_by_symbol(symbol):
    state = load_state()
    matches = [t for t in state["trades"] if t.get("symbol", "").upper() == symbol.upper() and t.get("status") == "active"]
    return matches[-1] if matches else None


# ── Daily instructions (what you send before 9:30 AM) ──────────────────

def load_instructions():
    """
    Reads state/instructions.json — this is the file Claude writes when
    you share your daily stock list + entry/SL/exit logic before market open.
    """
    if not os.path.exists(INSTRUCTIONS_PATH):
        return {"date": None, "watchlist": [], "rules": ""}
    with open(INSTRUCTIONS_PATH, "r") as f:
        return json.load(f)


def save_instructions(instructions):
    os.makedirs(os.path.dirname(INSTRUCTIONS_PATH), exist_ok=True)
    with open(INSTRUCTIONS_PATH, "w") as f:
        json.dump(instructions, f, indent=2)
    logger.info("Instructions saved")
