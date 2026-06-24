#!/usr/bin/env python3

import os
import sqlite3
import sys

import requests

DB_PATH = os.path.join(os.path.dirname(__file__), "rpc_truth.db")
SERVER_URL = os.environ.get("WALLET_FINGERPRINT_URL", "http://localhost:3020")
TIMEOUT = 30
DEFAULT_TIP = 1_000_000_000
WINDOW = 20
MIN_INDEXED_BLOCK = (
    int(os.environ["MIN_INDEXED_BLOCK"]) if os.environ.get("MIN_INDEXED_BLOCK") else None
)


_color = sys.stdout.isatty()
GREEN = "\033[32m" if _color else ""
RED = "\033[31m" if _color else ""
RESET = "\033[0m" if _color else ""


def window_indexed(block_number):
    if MIN_INDEXED_BLOCK is None:
        return True
    return block_number - (WINDOW - 1) >= MIN_INDEXED_BLOCK


def fetch_db_value(endpoint, block_number):
    url = f"{SERVER_URL}/{endpoint}/{block_number}"
    resp = requests.get(url, timeout=TIMEOUT)
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    try:
        return int(resp.json()["value_wei"]), None
    except (ValueError, KeyError) as e:
        return None, f"bad body: {e}"


def gwei(wei):
    return wei / 1e9


def compare(samples):
    metrics = {
        "max_priority_fee": "max_priority_fee_wei",
        "gas_price": "gas_price_wei",
    }
    results = {name: {"exact": 0, "mismatch": 0, "error": 0, "diffs": []} for name in metrics}
    skipped = 0

    for row in samples:
        block = row["block_number"]

        if not window_indexed(block):
            skipped += 1
            continue

        fetched = {}
        for endpoint in metrics:
            value, err = fetch_db_value(endpoint, block)
            fetched[endpoint] = value
            if value is None:
                results[endpoint]["error"] += 1
                print(f"  [{endpoint}] block {block}: ERROR ({err})")

        if fetched["gas_price"] == DEFAULT_TIP and fetched["max_priority_fee"] == DEFAULT_TIP:
            skipped += 1
            continue

        for endpoint, truth_col in metrics.items():
            db_value = fetched[endpoint]
            if db_value is None:
                continue

            node_value = row[truth_col]
            stats = results[endpoint]

            if db_value == node_value:
                stats["exact"] += 1
                print(
                    f"{GREEN}  [{endpoint}] block {block}: MATCH     "
                    f"node={node_value} wei  db={db_value} wei{RESET}"
                )
            else:
                stats["mismatch"] += 1
                diff = db_value - node_value
                rel = diff / node_value if node_value else float("inf")
                stats["diffs"].append((block, node_value, db_value, diff, rel))
                print(
                    f"{RED}  [{endpoint}] block {block}: MISMATCH  "
                    f"node={node_value} wei  db={db_value} wei  "
                    f"diff={diff:+} wei ({rel:+.2%}){RESET}"
                )

    return results, skipped


def print_summary(results, total, skipped):
    print("\n" + "=" * 60)
    print(f"compared {total - skipped}/{total} blocks against {SERVER_URL}")
    print(f"skipped (not indexed / partial window): {skipped}")
    print("=" * 60)
    for name, stats in results.items():
        checked = stats["exact"] + stats["mismatch"]
        rate = stats["exact"] / checked if checked else 0.0
        print(f"\n{name}:")
        print(f"  exact match : {stats['exact']}/{checked}  ({rate:.1%})")
        print(f"  mismatch    : {stats['mismatch']}")
        print(f"  errors      : {stats['error']}")
        if stats["diffs"]:
            rels = [abs(d[4]) for d in stats["diffs"]]
            print(f"  median |rel diff| on mismatches: {sorted(rels)[len(rels) // 2]:.2%}")


def main():
    if not os.path.exists(DB_PATH):
        sys.exit(f"no db at {DB_PATH} — run collect_rpc_truth.py first")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    client = conn.execute(
        "SELECT value FROM meta WHERE key='client_version'"
    ).fetchone()
    if client:
        print(f"node client (from collection): {client['value']}")
        print("  reminder: eth_fees.rs uses ignore_price=2 (geth); "
              "if the node is reth (ignore_price=0), max_priority_fee will differ\n")

    samples = conn.execute(
        "SELECT block_number, max_priority_fee_wei, gas_price_wei, base_fee_wei "
        "FROM rpc_samples ORDER BY block_number"
    ).fetchall()
    conn.close()

    if not samples:
        sys.exit("no samples in rpc_truth.db")

    results, skipped = compare(samples)
    print_summary(results, len(samples), skipped)


if __name__ == "__main__":
    main()