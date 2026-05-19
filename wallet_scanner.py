#!/usr/bin/env python3
"""
wallet_scanner.py — Scan a block range, detect all known EIP-7702 wallets in one pass.

For each transaction, checks eth_getCode(from) once and matches against all known
delegators simultaneously. One block fetch = all wallets covered.

Schema — table `transactions` :
  hash, block, from_addr, wallet,
  max_fee_gwei, max_priority_gwei, base_fee_gwei,
  fee_factor, fee_factor_parent,
  gas_limit, estimated_gas, gas_limit_factor

Usage:
  python wallet_scanner.py --start 22000000 --end 22100000
"""

import argparse
import os
import sqlite3
import sys
import time

from web3 import Web3

# ── Known EIP-7702 delegators ─────────────────────────────────────────────────

KNOWN_DELEGATORS: dict[str, str] = {
    "0x63c0c19a282a1b52b07dd5a65b58948a07dae32b": "MetaMask",
    "0xd2e28229f6f2c235e57de2ebc727025a1d0530fb": "Trust Wallet",
    "0x80296ff8d1ed46f8e3c7992664d13b833504c2bb": "OKX Wallet",
    "0x5A7FC11397E9a8AD41BF10bf13F22B0a63f96f6d": "Ambire",
}

EIP7702_PREFIX = bytes.fromhex("ef0100")

# address → wallet_name | None (None = not a known delegator)
_code_cache: dict[str, str | None] = {}


# ── DB setup ──────────────────────────────────────────────────────────────────

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS transactions (
    hash                TEXT PRIMARY KEY,
    block               INTEGER NOT NULL,
    from_addr           TEXT,
    wallet              TEXT,
    tx_type             INTEGER,
    max_fee_gwei        REAL,
    max_priority_gwei   REAL,
    base_fee_gwei       REAL,
    fee_factor          REAL,
    fee_factor_parent   REAL,
    gas_limit           INTEGER,
    estimated_gas       INTEGER,
    gas_limit_factor    REAL
)
"""

INSERT_TX = """
INSERT OR IGNORE INTO transactions
    (hash, block, from_addr, wallet, tx_type,
     max_fee_gwei, max_priority_gwei, base_fee_gwei,
     fee_factor, fee_factor_parent,
     gas_limit, estimated_gas, gas_limit_factor)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TABLE)
    conn.commit()
    return conn


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_wallet(w3: Web3, address: str, block_number: int) -> str | None:
    """Return the wallet name if the address is a known EIP-7702 delegator, else None."""
    key = address.lower()
    if key in _code_cache:
        return _code_cache[key]
    try:
        code = bytes(w3.eth.get_code(address, block_number))
        if len(code) >= 23 and code[:3] == EIP7702_PREFIX:
            delegated = "0x" + code[3:23].hex().lower()
            result = KNOWN_DELEGATORS.get(delegated)
        else:
            result = None
    except Exception:
        result = None
    _code_cache[key] = result
    return result


def _estimate_gas(w3: Web3, tx, block_number: int) -> int | None:
    call = {
        "from":  tx["from"],
        "to":    tx.get("to"),
        "value": tx["value"],
        "data":  tx.get("input", b""),
    }
    try:
        return w3.eth.estimate_gas(call, block_number - 1)
    except Exception:
        return None


# ── Core scanner ──────────────────────────────────────────────────────────────

def scan(
    w3: Web3,
    start: int,
    end: int,
    conn: sqlite3.Connection,
    delay: float,
) -> None:
    total_blocks   = end - start + 1
    matched_txs    = 0
    scanned_txs    = 0
    scanned_blocks = 0
    counts: dict[str, int] = {name: 0 for name in KNOWN_DELEGATORS.values()}

    print(f"Block range : {start} → {end}  ({total_blocks} blocks)")
    print(f"Wallets     : {', '.join(KNOWN_DELEGATORS.values())}\n")

    try:
        parent = w3.eth.get_block(start - 1)
        prev_base_fee_gwei = parent["baseFeePerGas"] / 1e9 if parent.get("baseFeePerGas") else None
    except Exception:
        prev_base_fee_gwei = None

    for block_num in range(start, end + 1):
        try:
            block = w3.eth.get_block(block_num, full_transactions=True)
        except Exception as exc:
            print(f"[block {block_num}] fetch error: {exc}", flush=True)
            time.sleep(1)
            continue

        base_fee      = block.get("baseFeePerGas")
        base_fee_gwei = base_fee / 1e9 if base_fee else None
        block_txs     = len(block["transactions"])
        block_matches = 0
        rows          = []

        for tx in block["transactions"]:
            scanned_txs += 1

            wallet_name = _get_wallet(w3, tx["from"], block_num)
            if wallet_name is None:
                continue

            max_fee_gwei      = None
            max_priority_gwei = None
            fee_factor        = None
            fee_factor_parent = None
            if (
                base_fee_gwei
                and tx.get("maxFeePerGas") is not None
                and tx.get("maxPriorityFeePerGas") is not None
            ):
                max_fee_gwei      = tx["maxFeePerGas"]         / 1e9
                max_priority_gwei = tx["maxPriorityFeePerGas"] / 1e9
                fee_factor        = (max_fee_gwei - max_priority_gwei) / base_fee_gwei
                if prev_base_fee_gwei:
                    fee_factor_parent = (max_fee_gwei - max_priority_gwei) / prev_base_fee_gwei

            gas_limit        = tx["gas"]
            estimated_gas    = _estimate_gas(w3, tx, block_num)
            gas_limit_factor = gas_limit / estimated_gas if estimated_gas else None

            rows.append((
                tx["hash"].hex(),
                block_num,
                tx["from"],
                wallet_name,
                tx.get("type"),
                round(max_fee_gwei,      6) if max_fee_gwei      is not None else None,
                round(max_priority_gwei, 6) if max_priority_gwei is not None else None,
                round(base_fee_gwei,     6) if base_fee_gwei     is not None else None,
                round(fee_factor,        6) if fee_factor        is not None else None,
                round(fee_factor_parent, 6) if fee_factor_parent is not None else None,
                gas_limit,
                estimated_gas,
                round(gas_limit_factor,  6) if gas_limit_factor  is not None else None,
            ))
            counts[wallet_name] += 1
            block_matches       += 1

        if rows:
            conn.executemany(INSERT_TX, rows)
            conn.commit()

        matched_txs        += block_matches
        scanned_blocks     += 1
        prev_base_fee_gwei  = base_fee_gwei

        pct         = 100 * scanned_blocks / total_blocks
        counts_str  = "  ".join(f"{name}={counts[name]}" for name in KNOWN_DELEGATORS.values())
        print(
            f"[block {block_num}]  {pct:5.1f}%  txs={block_txs}"
            f"  matched={block_matches}  [{counts_str}]",
            flush=True,
        )

        if delay > 0:
            time.sleep(delay)

    print(f"\n{'='*60}")
    print(f"SCAN COMPLETE")
    print(f"{'='*60}")
    print(f"  Blocks scanned : {scanned_blocks}")
    print(f"  Txs scanned    : {scanned_txs}")
    print(f"  Txs matched    : {matched_txs}")
    for name, count in counts.items():
        print(f"  {name:<15} : {count} tx(s)")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Scan a block range for all known EIP-7702 wallet transactions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--rpc",   default=os.environ.get("ETH_RPC_URL"), required=not os.environ.get("ETH_RPC_URL"), help="Ethereum JSON-RPC endpoint")
    p.add_argument("--start", type=int, default=24960000, help="First block to scan")
    p.add_argument("--end",   type=int, default=24970000, help="Last block to scan")
    p.add_argument("--db",    default="gas.db", help="SQLite database file (default: gas.db)")
    p.add_argument("--delay", type=float, default=0.02, help="Seconds between block fetches (default: 0.02)")
    args = p.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        sys.exit("ERROR: Cannot connect to Ethereum node. Check --rpc.")

    print(f"Connected. Latest block: {w3.eth.block_number}")
    print(f"Database    : {args.db}\n")

    conn = init_db(args.db)
    try:
        scan(w3=w3, start=args.start, end=args.end, conn=conn, delay=args.delay)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
