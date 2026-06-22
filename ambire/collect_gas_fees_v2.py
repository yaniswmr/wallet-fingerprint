#!/usr/bin/env python3
"""
collect_gas_fees_v2.py — Poll the Pimlico `pimlico_getUserOperationGasPrice`
endpoint every 12 seconds and store all fields + the block baseFee in a SQLite
database.

This is the corrected collector: the previous one (collect_gas_fees.py) polled
the Ambire relayer, which does NOT return the real Ethereum mainnet gas
suggestions. This version queries Pimlico's bundler RPC instead.

Usage:
  python collect_gas_fees_v2.py
  python collect_gas_fees_v2.py --db /path/to/output.db
  python collect_gas_fees_v2.py --rpc <ETH_RPC_URL> --interval 12

Pimlico response format
  POST https://api.pimlico.io/v2/1/rpc?apikey=<KEY>
  {"jsonrpc":"2.0","id":1,"method":"pimlico_getUserOperationGasPrice","params":[]}
  ->
  {
    "jsonrpc": "2.0", "id": 1,
    "result": {
      "slow":     { "maxFeePerGas": "0x..", "maxPriorityFeePerGas": "0x.." },
      "standard": { "maxFeePerGas": "0x..", "maxPriorityFeePerGas": "0x.." },
      "fast":     { "maxFeePerGas": "0x..", "maxPriorityFeePerGas": "0x.." }
    }
  }

The endpoint does not expose a suggested baseFee, so we fetch it from the chain
via eth_getBlockByNumber("latest", false) -> result.baseFeePerGas, which also
gives us result.number (the current block).

Per level we compute:
  base_mult = (maxFeePerGas - maxPriorityFeePerGas) / baseFeePerGas
"""

import argparse
import json
import os
import sqlite3
import time

import requests

PIMLICO_API_KEY = os.environ.get("PIMLICO_API_KEY", "pim_JPU8iy5BTbfGchPMJXQ1uP")
GAS_URL     = f"https://api.pimlico.io/v2/1/rpc?apikey={PIMLICO_API_KEY}"
DEFAULT_DB  = os.path.join(os.path.dirname(__file__), "gas_fees_collected_v2.db")
DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS gas_fees (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               INTEGER NOT NULL,   -- unix timestamp (seconds)
    block_number     INTEGER,

    -- suggested baseFee from the latest block (wei, TEXT to preserve precision)
    base_fee         TEXT,

    -- max fee per gas per level (wei)
    max_slow         TEXT,
    max_standard     TEXT,
    max_fast         TEXT,

    -- priority fee per gas per level (wei)
    prio_slow        TEXT,
    prio_standard    TEXT,
    prio_fast        TEXT,

    -- baseFee multiplier per level = (maxFee - prio) / base_fee
    mult_slow        REAL,
    mult_standard    REAL,
    mult_fast        REAL,

    raw_json         TEXT,               -- full Pimlico response
    raw_block_json   TEXT                -- minimal block info (number + baseFee)
);
"""

INSERT_ROW = """
INSERT INTO gas_fees (
    ts, block_number, base_fee,
    max_slow, max_standard, max_fast,
    prio_slow, prio_standard, prio_fast,
    mult_slow, mult_standard, mult_fast,
    raw_json, raw_block_json
) VALUES (
    :ts, :block_number, :base_fee,
    :max_slow, :max_standard, :max_fast,
    :prio_slow, :prio_standard, :prio_fast,
    :mult_slow, :mult_standard, :mult_fast,
    :raw_json, :raw_block_json
);
"""

# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------
def _hex_to_int(v):
    if v is None:
        return None
    try:
        return int(v, 16)
    except (ValueError, TypeError):
        return None


def get_latest_block(rpc_url: str) -> dict | None:
    """Return {'number': int, 'base_fee': int, 'raw': {...}} or None."""
    try:
        r = requests.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "method": "eth_getBlockByNumber",
                "params": ["latest", False],
                "id": 1,
            },
            timeout=5,
        )
        res = r.json()["result"]
        number   = _hex_to_int(res.get("number"))
        base_fee = _hex_to_int(res.get("baseFeePerGas"))
        return {
            "number": number,
            "base_fee": base_fee,
            "raw": {"number": res.get("number"), "baseFeePerGas": res.get("baseFeePerGas")},
        }
    except Exception as e:
        print(f"  [warn] block fetch failed: {e}")
        return None


def fetch_gas_fees() -> dict | None:
    try:
        r = requests.post(
            GAS_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "pimlico_getUserOperationGasPrice", "params": []},
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
def parse_row(data: dict, block: dict | None, ts: int) -> dict:
    result = data.get("result", {})

    slow     = result.get("slow", {})
    standard = result.get("standard", {})
    fast     = result.get("fast", {})

    max_slow     = _hex_to_int(slow.get("maxFeePerGas"))
    max_standard = _hex_to_int(standard.get("maxFeePerGas"))
    max_fast     = _hex_to_int(fast.get("maxFeePerGas"))

    prio_slow     = _hex_to_int(slow.get("maxPriorityFeePerGas"))
    prio_standard = _hex_to_int(standard.get("maxPriorityFeePerGas"))
    prio_fast     = _hex_to_int(fast.get("maxPriorityFeePerGas"))

    block_number = block["number"]   if block else None
    base_fee     = block["base_fee"] if block else None

    def mult(max_fee, prio):
        if max_fee is None or prio is None or not base_fee:
            return None
        return (max_fee - prio) / base_fee

    return {
        "ts":           ts,
        "block_number": block_number,
        "base_fee":     str(base_fee) if base_fee is not None else None,

        "max_slow":     str(max_slow)     if max_slow     is not None else None,
        "max_standard": str(max_standard) if max_standard is not None else None,
        "max_fast":     str(max_fast)     if max_fast     is not None else None,

        "prio_slow":     str(prio_slow)     if prio_slow     is not None else None,
        "prio_standard": str(prio_standard) if prio_standard is not None else None,
        "prio_fast":     str(prio_fast)     if prio_fast     is not None else None,

        "mult_slow":     mult(max_slow,     prio_slow),
        "mult_standard": mult(max_standard, prio_standard),
        "mult_fast":     mult(max_fast,     prio_fast),

        "raw_json":       json.dumps(data),
        "raw_block_json": json.dumps(block["raw"]) if block else None,
    }

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Collect Pimlico user-operation gas prices every N seconds")
    p.add_argument("--db",       default=DEFAULT_DB,  help=f"SQLite database path (default: {DEFAULT_DB})")
    p.add_argument("--rpc",      default=DEFAULT_RPC, help="Ethereum JSON-RPC URL for block baseFee")
    p.add_argument("--interval", type=float, default=12.0, help="Poll interval in seconds (default: 12)")
    args = p.parse_args()

    con = sqlite3.connect(args.db)
    con.execute(CREATE_TABLE)
    con.commit()

    print(f"DB      : {args.db}")
    print(f"RPC     : {args.rpc}")
    print(f"Interval: {args.interval}s")
    print(f"Polling : {GAS_URL}\n")
    print(f"{'#':>6}  {'time':>10}  {'block':>9}  {'baseFee':>12}  {'×slow':>7}  {'×std':>7}  {'×fast':>7}")
    print("-" * 80)

    count = 0
    while True:
        ts    = int(time.time())
        block = get_latest_block(args.rpc)
        data  = fetch_gas_fees()

        if data is not None and data.get("result"):
            row = parse_row(data, block, ts)
            con.execute(INSERT_ROW, row)
            con.commit()
            count += 1

            bf_gwei = f"{int(row['base_fee']) / 1e9:.3f} Gw" if row["base_fee"] else "?"
            m_slow  = f"{row['mult_slow']:.5f}"     if row["mult_slow"]     is not None else "N/A"
            m_std   = f"{row['mult_standard']:.5f}" if row["mult_standard"] is not None else "N/A"
            m_fast  = f"{row['mult_fast']:.5f}"     if row["mult_fast"]     is not None else "N/A"

            print(
                f"{count:>6}  {ts:>10}  {str(row['block_number'] or 'N/A'):>9}"
                f"  {bf_gwei:>12}  {m_slow:>7}  {m_std:>7}  {m_fast:>7}",
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
