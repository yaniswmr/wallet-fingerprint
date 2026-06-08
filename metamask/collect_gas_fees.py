#!/usr/bin/env python3
"""
collect_gas_fees.py — Poll MetaMask suggestedGasFees endpoint every 20 seconds
and store all fields + latest block number in a SQLite database.

Usage:
  python collect_gas_fees.py
  python collect_gas_fees.py --db /path/to/output.db
  python collect_gas_fees.py --rpc <ETH_RPC_URL> --interval 10
"""

import argparse
import os
import sqlite3
import time

import requests

GAS_URL = "https://gas.api.cx.metamask.io/networks/1/suggestedGasFees"
DEFAULT_DB = os.path.join(os.path.dirname(__file__), "gas_fees_collected.db")
DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS gas_fees (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                              INTEGER NOT NULL,          -- unix timestamp (seconds)
    block_number                    INTEGER,

    -- low tier
    low_max_priority_fee            TEXT,
    low_max_fee                     TEXT,
    low_min_wait_ms                 INTEGER,
    low_max_wait_ms                 INTEGER,

    -- medium tier
    medium_max_priority_fee         TEXT,
    medium_max_fee                  TEXT,
    medium_min_wait_ms              INTEGER,
    medium_max_wait_ms              INTEGER,

    -- high tier
    high_max_priority_fee           TEXT,
    high_max_fee                    TEXT,
    high_min_wait_ms                INTEGER,
    high_max_wait_ms                INTEGER,

    -- network
    estimated_base_fee              TEXT,
    network_congestion              REAL,

    -- ranges (stored as text to preserve precision)
    latest_priority_fee_range_min   TEXT,
    latest_priority_fee_range_max   TEXT,
    historical_priority_fee_range_min TEXT,
    historical_priority_fee_range_max TEXT,
    historical_base_fee_range_min   TEXT,
    historical_base_fee_range_max   TEXT,

    -- trends
    priority_fee_trend              TEXT,
    base_fee_trend                  TEXT,

    -- metadata
    api_version                     TEXT,
    raw_json                        TEXT       -- full response for safety
);
"""

INSERT_ROW = """
INSERT INTO gas_fees (
    ts, block_number,
    low_max_priority_fee, low_max_fee, low_min_wait_ms, low_max_wait_ms,
    medium_max_priority_fee, medium_max_fee, medium_min_wait_ms, medium_max_wait_ms,
    high_max_priority_fee, high_max_fee, high_min_wait_ms, high_max_wait_ms,
    estimated_base_fee, network_congestion,
    latest_priority_fee_range_min, latest_priority_fee_range_max,
    historical_priority_fee_range_min, historical_priority_fee_range_max,
    historical_base_fee_range_min, historical_base_fee_range_max,
    priority_fee_trend, base_fee_trend,
    api_version, raw_json
) VALUES (
    :ts, :block_number,
    :low_max_priority_fee, :low_max_fee, :low_min_wait_ms, :low_max_wait_ms,
    :medium_max_priority_fee, :medium_max_fee, :medium_min_wait_ms, :medium_max_wait_ms,
    :high_max_priority_fee, :high_max_fee, :high_min_wait_ms, :high_max_wait_ms,
    :estimated_base_fee, :network_congestion,
    :latest_priority_fee_range_min, :latest_priority_fee_range_max,
    :historical_priority_fee_range_min, :historical_priority_fee_range_max,
    :historical_base_fee_range_min, :historical_base_fee_range_max,
    :priority_fee_trend, :base_fee_trend,
    :api_version, :raw_json
);
"""


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


def parse_row(data: dict, block_number: int | None, ts: int) -> dict:
    import json

    lpr = data.get("latestPriorityFeeRange", [None, None])
    hpr = data.get("historicalPriorityFeeRange", [None, None])
    hbr = data.get("historicalBaseFeeRange", [None, None])

    low    = data.get("low", {})
    medium = data.get("medium", {})
    high   = data.get("high", {})

    return {
        "ts":           ts,
        "block_number": block_number,

        "low_max_priority_fee": low.get("suggestedMaxPriorityFeePerGas"),
        "low_max_fee":          low.get("suggestedMaxFeePerGas"),
        "low_min_wait_ms":      low.get("minWaitTimeEstimate"),
        "low_max_wait_ms":      low.get("maxWaitTimeEstimate"),

        "medium_max_priority_fee": medium.get("suggestedMaxPriorityFeePerGas"),
        "medium_max_fee":          medium.get("suggestedMaxFeePerGas"),
        "medium_min_wait_ms":      medium.get("minWaitTimeEstimate"),
        "medium_max_wait_ms":      medium.get("maxWaitTimeEstimate"),

        "high_max_priority_fee": high.get("suggestedMaxPriorityFeePerGas"),
        "high_max_fee":          high.get("suggestedMaxFeePerGas"),
        "high_min_wait_ms":      high.get("minWaitTimeEstimate"),
        "high_max_wait_ms":      high.get("maxWaitTimeEstimate"),

        "estimated_base_fee":  data.get("estimatedBaseFee"),
        "network_congestion":  data.get("networkCongestion"),

        "latest_priority_fee_range_min":      lpr[0] if len(lpr) > 0 else None,
        "latest_priority_fee_range_max":      lpr[1] if len(lpr) > 1 else None,
        "historical_priority_fee_range_min":  hpr[0] if len(hpr) > 0 else None,
        "historical_priority_fee_range_max":  hpr[1] if len(hpr) > 1 else None,
        "historical_base_fee_range_min":      hbr[0] if len(hbr) > 0 else None,
        "historical_base_fee_range_max":      hbr[1] if len(hbr) > 1 else None,

        "priority_fee_trend": data.get("priorityFeeTrend"),
        "base_fee_trend":     data.get("baseFeeTrend"),
        "api_version":        data.get("version"),
        "raw_json":           json.dumps(data),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Collect MetaMask gas fees every N seconds")
    p.add_argument("--db",       default=DEFAULT_DB,  help=f"SQLite database path (default: {DEFAULT_DB})")
    p.add_argument("--rpc",      default=DEFAULT_RPC,  help="Ethereum JSON-RPC URL for block number")
    p.add_argument("--interval", type=float, default=12.0, help="Poll interval in seconds (default: 20)")
    args = p.parse_args()

    con = sqlite3.connect(args.db)
    con.execute(CREATE_TABLE)
    con.commit()

    print(f"DB      : {args.db}")
    print(f"RPC     : {args.rpc}")
    print(f"Interval: {args.interval}s")
    print(f"Polling {GAS_URL}\n")
    print(f"{'#':>6}  {'time':>10}  {'block':>9}  {'baseFee':>12}  {'congestion':>10}  med_priority")
    print("-" * 75)

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
            print(
                f"{count:>6}  {ts:>10}  {str(block_number or 'N/A'):>9}"
                f"  {str(data.get('estimatedBaseFee', '?')):>12}"
                f"  {str(data.get('networkCongestion', '?')):>10}"
                f"  {data.get('medium', {}).get('suggestedMaxPriorityFeePerGas', '?')}",
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
