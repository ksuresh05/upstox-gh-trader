"""
Upstox API client.
Thin wrapper around Upstox's REST endpoints for:
  - Order placement
  - GTT (Good Till Triggered) place / modify / cancel
  - Portfolio / positions / funds / orders retrieval
  - LTP (last traded price) quotes
"""

import os
import requests
import logging

logger = logging.getLogger("upstox_client")

BASE_URL = "https://api.upstox.com"
HFT_URL  = "https://api-hft.upstox.com"


class UpstoxAPIError(Exception):
    pass


def _token():
    t = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
    if not t:
        raise UpstoxAPIError("UPSTOX_ACCESS_TOKEN not set in environment")
    return t


def _headers():
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _get(path, base=BASE_URL, params=None):
    resp = requests.get(f"{base}{path}", headers=_headers(), params=params, timeout=15)
    data = resp.json()
    if resp.status_code >= 400:
        raise UpstoxAPIError(data.get("errors", [{}])[0].get("message", str(data)))
    return data


def _post(path, body, base=HFT_URL):
    resp = requests.post(f"{base}{path}", headers=_headers(), json=body, timeout=15)
    data = resp.json()
    if resp.status_code >= 400:
        raise UpstoxAPIError(data.get("errors", [{}])[0].get("message", str(data)))
    return data


def _put(path, body, base=BASE_URL):
    resp = requests.put(f"{base}{path}", headers=_headers(), json=body, timeout=15)
    data = resp.json()
    if resp.status_code >= 400:
        raise UpstoxAPIError(data.get("errors", [{}])[0].get("message", str(data)))
    return data


# ── Orders ──────────────────────────────────────────────────────────────

def place_order(instrument_token, transaction_type, quantity, order_type="MARKET",
                 product="D", price=0, trigger_price=0, validity="DAY"):
    body = {
        "instrument_token": instrument_token,
        "transaction_type": transaction_type,
        "quantity": quantity,
        "order_type": order_type,
        "product": product,
        "price": price,
        "trigger_price": trigger_price,
        "validity": validity,
        "disclosed_quantity": 0,
        "is_amo": False,
        "slice": False,
    }
    res = _post("/v3/order/place", body)
    order_id = res.get("data", {}).get("order_id")
    logger.info(f"Order placed: {transaction_type} {quantity} {instrument_token} -> {order_id}")
    return order_id


def get_all_orders():
    res = _get("/v2/order/retrieve-all")
    return res.get("data", [])


# ── GTT ─────────────────────────────────────────────────────────────────

def place_gtt(instrument_token, exit_transaction_type, quantity, product, target_price, sl_price):
    """
    Place a MULTIPLE GTT with target and stop-loss legs.
    Per Upstox API: TARGET and STOPLOSS legs use trigger_type "IMMEDIATE"
    (not ABOVE/BELOW -- that's only for the ENTRY leg).
    """
    body = {
        "type": "MULTIPLE",
        "quantity": quantity,
        "product": product,
        "instrument_token": instrument_token,
        "transaction_type": exit_transaction_type,
        "rules": [
            {"strategy": "TARGET",   "trigger_type": "IMMEDIATE", "trigger_price": target_price},
            {"strategy": "STOPLOSS", "trigger_type": "IMMEDIATE", "trigger_price": sl_price},
        ],
    }
    res = _post("/v3/order/gtt/place", body, base=BASE_URL)
    gtt_id = res.get("data", {}).get("gtt_order_ids", [None])[0]
    logger.info(f"GTT placed for {instrument_token}: target={target_price} sl={sl_price} -> {gtt_id}")
    return gtt_id


def modify_gtt(gtt_order_id, instrument_token, exit_transaction_type, quantity, product, new_target, new_sl):
    """Modify GTT target/SL. Both legs use trigger_type 'IMMEDIATE' per Upstox API."""
    body = {
        "gtt_order_id": gtt_order_id,
        "quantity": quantity,
        "product": product,
        "instrument_token": instrument_token,
        "transaction_type": exit_transaction_type,
        "rules": [
            {"strategy": "TARGET",   "trigger_type": "IMMEDIATE", "trigger_price": new_target},
            {"strategy": "STOPLOSS", "trigger_type": "IMMEDIATE", "trigger_price": new_sl},
        ],
    }
    _put("/v3/order/gtt/modify", body)
    logger.info(f"GTT {gtt_order_id} modified: target={new_target} sl={new_sl}")


def cancel_gtt(gtt_order_id):
    _put(f"/v3/order/gtt/cancel?gtt_order_id={gtt_order_id}", {})
    logger.info(f"GTT {gtt_order_id} cancelled")


def get_gtt_status(gtt_order_id):
    """Returns the full GTT order object, including per-leg (rule) status."""
    res = _get("/v3/order/gtt/list")
    for gtt in res.get("data", []):
        if gtt.get("gtt_order_id") == gtt_order_id or gtt.get("id") == gtt_order_id:
            return gtt
    return None


def get_triggered_leg(gtt_order_id):
    """
    Inspects a GTT's rules to determine which leg actually fired.
    Returns ('TARGET', trigger_price) or ('STOPLOSS', trigger_price) or (None, None)
    if neither has triggered yet.

    Per Upstox: a leg with status TRIGGERED/COMPLETE has actually fired;
    the other leg auto-cancels (status CANCELLED) when its sibling fires.
    """
    gtt = get_gtt_status(gtt_order_id)
    if not gtt:
        return None, None
    for rule in gtt.get("rules", []):
        if rule.get("status") in ("TRIGGERED", "COMPLETE"):
            return rule.get("strategy"), rule.get("trigger_price")
    return None, None


# ── Portfolio ──────────────────────────────────────────────────────────

def get_positions():
    res = _get("/v2/portfolio/short-term-positions")
    return res.get("data", [])


def get_holdings():
    res = _get("/v2/portfolio/long-term-holdings")
    return res.get("data", [])


def get_funds():
    res = _get("/v3/user/get-funds-and-margin")
    return res.get("data", {})


# ── Market data ───────────────────────────────────────────────────────────

def get_ltp(instrument_token):
    res = _get("/v2/market-quote/ltp", params={"instrument_key": instrument_token})
    data = res.get("data", {})
    if not data:
        raise UpstoxAPIError(f"No LTP data for {instrument_token}")
    first_key = next(iter(data))
    return data[first_key].get("last_price")


def get_ltp_bulk(instrument_tokens):
    """Fetch LTP for multiple instruments in one call."""
    keys = ",".join(instrument_tokens)
    res = _get("/v2/market-quote/ltp", params={"instrument_key": keys})
    data = res.get("data", {})
    result = {}
    for token in instrument_tokens:
        for k, v in data.items():
            if v.get("instrument_token") == token or token.split("|")[-1] in k:
                result[token] = v.get("last_price")
                break
    return result
