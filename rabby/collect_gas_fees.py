#!/usr/bin/env python3
"""
collect_gas_fees.py — Poll Rabby wallet gas_market endpoint every 12 seconds
and store all fields + latest block number in a SQLite database.

Usage:
  python collect_gas_fees.py
  python collect_gas_fees.py --db /path/to/output.db
  python collect_gas_fees.py --rpc <ETH_RPC_URL> --interval 12

Response format from https://api.rabby.io/v2/wallet/gas_market (POST {"chain_id":"eth"}):
  [
    {"front_tx_count": 0, "estimated_seconds": 12,   "price": 453100000,  "priority_price": 100000000,  "level": "slow"},
    {"front_tx_count": 0, "estimated_seconds": 12,   "price": 1377000000, "priority_price": 1000000000, "level": "normal"},
    {"front_tx_count": 0, "estimated_seconds": 12,   "price": 2788000000, "priority_price": 2400000000, "level": "fast"},
    {"price": 0,          "estimated_seconds": 3600,                                                     "level": "custom"}
  ]

All prices are in wei. `price` = total gas price (base + tip), `priority_price` = tip.
The network base fee is derived as price - priority_price (same across tiers).
"""

import argparse
import json
import os
import sqlite3
import time

import requests

GAS_URL     = "https://api.rabby.io/v2/wallet/gas_market"
CHAIN_ID    = "eth"
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

    -- slow tier (wei, stored as TEXT to preserve precision)
    slow_price          TEXT,               -- total gas price (base + tip)
    slow_priority       TEXT,               -- priority fee (tip)
    slow_est_seconds    INTEGER,
    slow_front_tx       INTEGER,

    -- normal tier (default)
    normal_price        TEXT,
    normal_priority     TEXT,
    normal_est_seconds  INTEGER,
    normal_front_tx     INTEGER,

    -- fast tier
    fast_price          TEXT,
    fast_priority       TEXT,
    fast_est_seconds    INTEGER,
    fast_front_tx       INTEGER,

    -- custom tier (usually empty: price 0)
    custom_price        TEXT,
    custom_priority     TEXT,
    custom_est_seconds  INTEGER,
    custom_front_tx     INTEGER,

    -- computed convenience fields (fingerprint)
    base_fee            TEXT,               -- normal_price - normal_priority (network base fee)
    normal_base_mult    REAL,               -- normal_price / base_fee
    fast_base_mult      REAL,               -- fast_price   / base_fee
    slow_base_mult      REAL,               -- slow_price   / base_fee

    raw_json            TEXT                -- full response
);
"""

INSERT_ROW = """
INSERT INTO gas_fees (
    ts, block_number,
    slow_price, slow_priority, slow_est_seconds, slow_front_tx,
    normal_price, normal_priority, normal_est_seconds, normal_front_tx,
    fast_price, fast_priority, fast_est_seconds, fast_front_tx,
    custom_price, custom_priority, custom_est_seconds, custom_front_tx,
    base_fee, normal_base_mult, fast_base_mult, slow_base_mult,
    raw_json
) VALUES (
    :ts, :block_number,
    :slow_price, :slow_priority, :slow_est_seconds, :slow_front_tx,
    :normal_price, :normal_priority, :normal_est_seconds, :normal_front_tx,
    :fast_price, :fast_priority, :fast_est_seconds, :fast_front_tx,
    :custom_price, :custom_priority, :custom_est_seconds, :custom_front_tx,
    :base_fee, :normal_base_mult, :fast_base_mult, :slow_base_mult,
    :raw_json
);
"""

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


def fetch_gas_fees() -> list | None:
    try:
        r = requests.post(
            GAS_URL,
            json={"chain_id": CHAIN_ID},
            headers={"content-type": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [warn] gas fees fetch failed: {e}")
        return None

# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------
def parse_row(data: list, block_number: int | None, ts: int) -> dict:
    levels = {lvl.get("level"): lvl for lvl in data if isinstance(lvl, dict)}

    def field(level: str, key: str):
        lvl = levels.get(level)
        if not lvl:
            return None
        val = lvl.get(key)
        return val if val is not None else None

    def price_text(level: str, key: str):
        val = field(level, key)
        return str(val) if val is not None else None

    normal_price    = price_text("normal", "price")
    normal_priority = price_text("normal", "priority_price")
    fast_price      = price_text("fast", "price")
    slow_price      = price_text("slow", "price")

    base_fee = None
    normal_mult = fast_mult = slow_mult = None
    if normal_price and normal_priority:
        try:
            bf = int(normal_price) - int(normal_priority)
            base_fee = str(bf)
            if bf > 0:
                normal_mult = int(normal_price) / bf
                if fast_price:
                    fast_mult = int(fast_price) / bf
                if slow_price:
                    slow_mult = int(slow_price) / bf
        except (ValueError, TypeError):
            pass

    return {
        "ts":           ts,
        "block_number": block_number,

        "slow_price":       slow_price,
        "slow_priority":    price_text("slow", "priority_price"),
        "slow_est_seconds": field("slow", "estimated_seconds"),
        "slow_front_tx":    field("slow", "front_tx_count"),

        "normal_price":       normal_price,
        "normal_priority":    normal_priority,
        "normal_est_seconds": field("normal", "estimated_seconds"),
        "normal_front_tx":    field("normal", "front_tx_count"),

        "fast_price":       fast_price,
        "fast_priority":    price_text("fast", "priority_price"),
        "fast_est_seconds": field("fast", "estimated_seconds"),
        "fast_front_tx":    field("fast", "front_tx_count"),

        "custom_price":       price_text("custom", "price"),
        "custom_priority":    price_text("custom", "priority_price"),
        "custom_est_seconds": field("custom", "estimated_seconds"),
        "custom_front_tx":    field("custom", "front_tx_count"),

        "base_fee":         base_fee,
        "normal_base_mult": normal_mult,
        "fast_base_mult":   fast_mult,
        "slow_base_mult":   slow_mult,

        "raw_json": json.dumps(data),
    }

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Collect Rabby wallet gas fees every N seconds")
    p.add_argument("--db",       default=DEFAULT_DB,  help=f"SQLite database path (default: {DEFAULT_DB})")
    p.add_argument("--rpc",      default=DEFAULT_RPC, help="Ethereum JSON-RPC URL for block number")
    p.add_argument("--interval", type=float, default=12.0, help="Poll interval in seconds (default: 12)")
    args = p.parse_args()

    con = sqlite3.connect(args.db)
    con.execute(CREATE_TABLE)
    con.commit()

    print(f"DB      : {args.db}")
    print(f"RPC     : {args.rpc}")
    print(f"Interval: {args.interval}s")
    print(f"Polling : {GAS_URL} (chain_id={CHAIN_ID})\n")
    print(f"{'#':>6}  {'time':>10}  {'block':>9}  {'baseFee':>12}  {'×base':>6}  normal_prio")
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

            bf   = f"{int(row['base_fee']) / 1e9:.3f} Gw" if row["base_fee"] else "?"
            mult = f"{row['normal_base_mult']:.3f}"        if row["normal_base_mult"] else "N/A"
            pp   = f"{int(row['normal_priority']) / 1e9:.3f} Gw" if row["normal_priority"] else "?"

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
