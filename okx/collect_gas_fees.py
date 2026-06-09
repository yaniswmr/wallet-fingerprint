#!/usr/bin/env python3
"""
collect_gas_fees.py — Poll OKX gas-price endpoint every 12 seconds
and store all fields + latest block number in a SQLite database.

Usage:
  python collect_gas_fees.py
  python collect_gas_fees.py --db /path/to/output.db
  python collect_gas_fees.py --rpc <ETH_RPC_URL> --interval 12
"""

import argparse
import base64
import datetime
import hmac
import json
import os
import sqlite3
import time

import requests

# ---------------------------------------------------------------------------
# Auth — exporte ces variables avant de lancer
# ---------------------------------------------------------------------------
API_KEY    = os.environ.get("OKX_API_KEY",    "")
SECRET_KEY = os.environ.get("OKX_SECRET_KEY", "")
PASSPHRASE = os.environ.get("OKX_PASSPHRASE", "")
PROJECT_ID = os.environ.get("OKX_PROJECT_ID", "")

GAS_URL    = "https://www.okx.com/api/v6/dex/pre-transaction/gas-price"
CHAIN_ID   = "1"
DEFAULT_DB  = os.path.join(os.path.dirname(__file__), "gas_fees_collected.db")
DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS gas_fees (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  INTEGER NOT NULL,   -- unix timestamp (seconds)
    block_number        INTEGER,

    -- legacy tiers (non-EIP-1559)
    normal              TEXT,
    min                 TEXT,
    max                 TEXT,
    support_eip1559     INTEGER,            -- 0 or 1

    -- EIP-1559 fields
    base_fee            TEXT,
    suggest_base_fee    TEXT,               -- = base_fee * 1.25 (server-side)
    safe_priority_fee   TEXT,               -- slow tier
    propose_priority_fee TEXT,              -- average tier (default)
    fast_priority_fee   TEXT,               -- fast tier

    -- fingerprint ratios (computed locally for convenience)
    suggest_base_mult   REAL,               -- suggest_base_fee / base_fee
    max_fee_slow        TEXT,               -- suggest_base + safe_priority
    max_fee_average     TEXT,               -- suggest_base + propose_priority
    max_fee_fast        TEXT,               -- suggest_base + fast_priority

    raw_json            TEXT                -- full response
);
"""

INSERT_ROW = """
INSERT INTO gas_fees (
    ts, block_number,
    normal, min, max, support_eip1559,
    base_fee, suggest_base_fee,
    safe_priority_fee, propose_priority_fee, fast_priority_fee,
    suggest_base_mult,
    max_fee_slow, max_fee_average, max_fee_fast,
    raw_json
) VALUES (
    :ts, :block_number,
    :normal, :min, :max, :support_eip1559,
    :base_fee, :suggest_base_fee,
    :safe_priority_fee, :propose_priority_fee, :fast_priority_fee,
    :suggest_base_mult,
    :max_fee_slow, :max_fee_average, :max_fee_fast,
    :raw_json
);
"""

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _now_ts() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _sign(ts: str, method: str, path: str) -> str:
    pre = ts + method.upper() + path
    mac = hmac.new(bytes(SECRET_KEY, "utf-8"), bytes(pre, "utf-8"), digestmod="sha256")
    return base64.b64encode(mac.digest()).decode()


def _headers(path: str) -> dict:
    ts = _now_ts()
    h = {
        "OK-ACCESS-KEY":        API_KEY,
        "OK-ACCESS-SIGN":       _sign(ts, "GET", path),
        "OK-ACCESS-TIMESTAMP":  ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "User-Agent":           "Mozilla/5.0",
    }
    if PROJECT_ID:
        h["OK-ACCESS-PROJECT"] = PROJECT_ID
    return h

# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------
def get_block_number(rpc_url: str) -> int | None:
    try:
        r = requests.post(
            rpc_url,
            json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
            timeout=5,
        )
        return int(r.json()["result"], 16)
    except Exception as e:
        print(f"  [warn] block_number fetch failed: {e}")
        return None


def fetch_gas_fees() -> dict | None:
    path = f"/api/v6/dex/pre-transaction/gas-price?chainIndex={CHAIN_ID}"
    try:
        r = requests.get(
            GAS_URL,
            params={"chainIndex": CHAIN_ID},
            headers=_headers(path),
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "0":
            print(f"  [warn] API error code={data.get('code')} msg={data.get('msg')}")
            return None
        return data
    except Exception as e:
        print(f"  [warn] gas fees fetch failed: {e}")
        return None

# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------
def parse_row(data: dict, block_number: int | None, ts: int) -> dict:
    d   = data["data"][0]
    eip = d.get("eip1559Protocol") or d.get("eip1599Protocol") or {}

    base_fee     = eip.get("baseFee")
    suggest_base = eip.get("suggestBaseFee")
    safe_prio    = eip.get("safePriorityFee")
    propose_prio = eip.get("proposePriorityFee")
    fast_prio    = eip.get("fastPriorityFee")

    # Multiplicateur et maxFeePerGas par tier (calculés localement)
    mult = None
    max_slow = max_avg = max_fast = None
    if base_fee and suggest_base:
        try:
            mult     = int(suggest_base) / int(base_fee)
            max_slow = str(int(suggest_base) + int(safe_prio))    if safe_prio    else None
            max_avg  = str(int(suggest_base) + int(propose_prio)) if propose_prio else None
            max_fast = str(int(suggest_base) + int(fast_prio))    if fast_prio    else None
        except (ValueError, TypeError):
            pass

    return {
        "ts":           ts,
        "block_number": block_number,

        "normal":          d.get("normal"),
        "min":             d.get("min"),
        "max":             d.get("max"),
        "support_eip1559": 1 if d.get("supporteip1559") or d.get("supportEip1559") else 0,

        "base_fee":            base_fee,
        "suggest_base_fee":    suggest_base,
        "safe_priority_fee":   safe_prio,
        "propose_priority_fee": propose_prio,
        "fast_priority_fee":   fast_prio,

        "suggest_base_mult": mult,
        "max_fee_slow":      max_slow,
        "max_fee_average":   max_avg,
        "max_fee_fast":      max_fast,

        "raw_json": json.dumps(data),
    }

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Collect OKX gas fees every N seconds")
    p.add_argument("--db",       default=DEFAULT_DB,  help=f"SQLite database path (default: {DEFAULT_DB})")
    p.add_argument("--rpc",      default=DEFAULT_RPC, help="Ethereum JSON-RPC URL for block number")
    p.add_argument("--interval", type=float, default=12.0, help="Poll interval in seconds (default: 12)")
    args = p.parse_args()

    if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
        print("[ERROR] Missing credentials. Export OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE.")
        raise SystemExit(1)

    con = sqlite3.connect(args.db)
    con.execute(CREATE_TABLE)
    con.commit()

    print(f"DB      : {args.db}")
    print(f"RPC     : {args.rpc}")
    print(f"Interval: {args.interval}s")
    print(f"Polling : {GAS_URL}?chainIndex={CHAIN_ID}\n")
    print(f"{'#':>6}  {'time':>10}  {'block':>9}  {'baseFee':>12}  {'×base':>6}  propose_prio")
    print("-" * 72)

    count = 0
    while True:
        ts           = int(time.time())
        block_number = get_block_number(args.rpc)
        data         = fetch_gas_fees()

        if data is not None:
            row = parse_row(data, block_number, ts)
            con.execute(INSERT_ROW, row)
            con.commit()
            count += 1

            eip  = data["data"][0].get("eip1559Protocol") or data["data"][0].get("eip1599Protocol") or {}
            mult = f"{row['suggest_base_mult']:.3f}" if row["suggest_base_mult"] else "N/A"
            bf   = str(int(eip.get("baseFee", 0)) // 10**9) + " Gw" if eip.get("baseFee") else "?"
            pp   = str(round(int(eip.get("proposePriorityFee", 0)) / 10**9, 3)) + " Gw" if eip.get("proposePriorityFee") else "?"

            print(
                f"{count:>6}  {ts:>10}  {str(block_number or 'N/A'):>9}"
                f"  {bf:>12}  {mult:>6}  {pp}",
                flush=True,
            )
        else:
            print(f"{'ERR':>6}  {ts:>10}  {'N/A':>9}  (skipped)", flush=True)

        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
