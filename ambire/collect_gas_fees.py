#!/usr/bin/env python3
"""
collect_gas_fees.py — Poll Ambire relayer gasPrice endpoint every 12 seconds
and store all fields + latest block number in a SQLite database.

Usage:
  python collect_gas_fees.py
  python collect_gas_fees.py --db /path/to/output.db
  python collect_gas_fees.py --rpc <ETH_RPC_URL> --interval 12

Response format from https://relayer.ambire.com/gasPrice/ethereum :
  {
    "gasPrice": {
      "slow":   <baseFee_ref in wei>,          -- ×1.000000 (reference)
      "medium": <baseFee_ref × 1.021723>,
      "fast":   <baseFee_ref × 1.065169>,
      "ape":    <baseFee_ref × 1.195507>,
      "maxPriorityFeePerGas": {
        "slow":   0,
        "medium": <tip_unit>,
        "fast":   <tip_unit × 2>,
        "ape":    <tip_unit × 3>
      },
      "updated": <timestamp ms>
    }
  }
"""

import argparse
import json
import os
import sqlite3
import time

import requests

GAS_URL     = "https://relayer.ambire.com/gasPrice/ethereum"
DEFAULT_DB  = os.path.join(os.path.dirname(__file__), "gas_fees_collected.db")
DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS gas_fees (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               INTEGER NOT NULL,   -- unix timestamp (seconds)
    block_number     INTEGER,

    -- base-fee equivalents per speed (wei, stored as TEXT to preserve precision)
    base_slow        TEXT,               -- reference baseFee (×1.0)
    base_medium      TEXT,               -- ×1.021723
    base_fast        TEXT,               -- ×1.065169
    base_ape         TEXT,               -- ×1.195507

    -- priority fees per speed (wei)
    prio_slow        TEXT,               -- always 0
    prio_medium      TEXT,               -- tip_unit
    prio_fast        TEXT,               -- tip_unit × 2
    prio_ape         TEXT,               -- tip_unit × 3

    -- computed convenience fields
    tip_unit         TEXT,               -- = prio_medium (the fundamental tip)
    medium_base_mult REAL,               -- base_medium / base_slow (should ≈ 1.021723)
    fast_base_mult   REAL,               -- base_fast  / base_slow (should ≈ 1.065169)
    ape_base_mult    REAL,               -- base_ape   / base_slow (should ≈ 1.195507)

    -- relayer timestamp from response
    updated_ms       INTEGER,

    raw_json         TEXT                -- full response
);
"""

INSERT_ROW = """
INSERT INTO gas_fees (
    ts, block_number,
    base_slow, base_medium, base_fast, base_ape,
    prio_slow, prio_medium, prio_fast, prio_ape,
    tip_unit, medium_base_mult, fast_base_mult, ape_base_mult,
    updated_ms, raw_json
) VALUES (
    :ts, :block_number,
    :base_slow, :base_medium, :base_fast, :base_ape,
    :prio_slow, :prio_medium, :prio_fast, :prio_ape,
    :tip_unit, :medium_base_mult, :fast_base_mult, :ape_base_mult,
    :updated_ms, :raw_json
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


def fetch_gas_fees() -> dict | None:
    try:
        r = requests.get(GAS_URL, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [warn] gas fees fetch failed: {e}")
        return None

# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------
def parse_row(data: dict, block_number: int | None, ts: int) -> dict:
    # Real API wraps response: { "success": true, "data": { "gasPrice": {...} } }
    gp   = data.get("data", data).get("gasPrice", {})
    prio = gp.get("maxPriorityFeePerGas", {})

    base_slow   = str(gp.get("slow",   "")) or None
    base_medium = str(gp.get("medium", "")) or None
    base_fast   = str(gp.get("fast",   "")) or None
    base_ape    = str(gp.get("ape",    "")) or None

    prio_slow   = str(prio.get("slow",   "")) or None
    prio_medium = str(prio.get("medium", "")) or None
    prio_fast   = str(prio.get("fast",   "")) or None
    prio_ape    = str(prio.get("ape",    "")) or None

    medium_mult = fast_mult = ape_mult = None
    if base_slow and base_medium and base_fast and base_ape:
        try:
            bs = int(base_slow)
            if bs > 0:
                medium_mult = int(base_medium) / bs
                fast_mult   = int(base_fast)   / bs
                ape_mult    = int(base_ape)    / bs
        except (ValueError, TypeError):
            pass

    return {
        "ts":           ts,
        "block_number": block_number,

        "base_slow":   base_slow,
        "base_medium": base_medium,
        "base_fast":   base_fast,
        "base_ape":    base_ape,

        "prio_slow":   prio_slow,
        "prio_medium": prio_medium,
        "prio_fast":   prio_fast,
        "prio_ape":    prio_ape,

        "tip_unit":         prio_medium,
        "medium_base_mult": medium_mult,
        "fast_base_mult":   fast_mult,
        "ape_base_mult":    ape_mult,

        "updated_ms": gp.get("updated"),
        "raw_json":   json.dumps(data),
    }

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Collect Ambire relayer gas fees every N seconds")
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
    print(f"Polling : {GAS_URL}\n")
    print(f"{'#':>6}  {'time':>10}  {'block':>9}  {'base_slow':>12}  {'×med':>7}  {'×fast':>7}  tip_unit")
    print("-" * 78)

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

            bs_gwei  = f"{int(row['base_slow']) / 1e9:.3f} Gw"  if row["base_slow"]  else "?"
            med_mult = f"{row['medium_base_mult']:.5f}"          if row["medium_base_mult"] else "N/A"
            fst_mult = f"{row['fast_base_mult']:.5f}"            if row["fast_base_mult"]   else "N/A"
            tip_gwei = f"{int(row['tip_unit']) / 1e9:.4f} Gw"   if row["tip_unit"]   else "?"

            print(
                f"{count:>6}  {ts:>10}  {str(block_number or 'N/A'):>9}"
                f"  {bs_gwei:>12}  {med_mult:>7}  {fst_mult:>7}  {tip_gwei}",
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
