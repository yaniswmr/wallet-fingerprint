#!/usr/bin/env python3

import os
import sqlite3
import time

from web3 import Web3

RPC_URL = os.environ["ETH_RPC_URL"]
DB_PATH = os.path.join(os.path.dirname(__file__), "rpc_truth.db")
POLL_INTERVAL = 10

w3 = Web3(Web3.HTTPProvider(RPC_URL))


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rpc_samples (
            block_number         INTEGER PRIMARY KEY,
            max_priority_fee_wei INTEGER NOT NULL,
            gas_price_wei        INTEGER NOT NULL,
            base_fee_wei         INTEGER NOT NULL,
            ts                   INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("client_version", w3.client_version),
    )
    conn.commit()
    return conn


def sample():
    block_before = w3.eth.block_number
    max_priority_fee = w3.eth.max_priority_fee
    gas_price = w3.eth.gas_price
    block_after = w3.eth.block_number

    if block_before != block_after:
        return None

    base_fee = w3.eth.get_block(block_before)["baseFeePerGas"]
    return {
        "block_number": block_before,
        "max_priority_fee_wei": max_priority_fee,
        "gas_price_wei": gas_price,
        "base_fee_wei": base_fee,
        "ts": int(time.time()),
    }


def main():
    conn = init_db()
    print(f"client      : {w3.client_version}")
    print(f"db          : {DB_PATH}")
    print("collecting (Ctrl-C to stop)…\n")

    last_block = None
    while True:
        try:
            head = w3.eth.block_number
            if head != last_block:
                row = sample()
                if row is not None:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO rpc_samples
                            (block_number, max_priority_fee_wei, gas_price_wei, base_fee_wei, ts)
                        VALUES (:block_number, :max_priority_fee_wei, :gas_price_wei, :base_fee_wei, :ts)
                        """,
                        row,
                    )
                    conn.commit()
                    last_block = row["block_number"]
                    print(
                        f"block {row['block_number']}  "
                        f"maxPriority {row['max_priority_fee_wei'] / 1e9:.4f} gwei  "
                        f"gasPrice {row['gas_price_wei'] / 1e9:.4f} gwei"
                    )
                else:
                    print("skip (head moved during sampling)")
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            count = conn.execute("SELECT COUNT(*) FROM rpc_samples").fetchone()[0]
            print(f"\nstopped — {count} samples in {DB_PATH}")
            conn.close()
            break


if __name__ == "__main__":
    main()