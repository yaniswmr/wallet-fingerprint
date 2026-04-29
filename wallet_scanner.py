#!/usr/bin/env python3
"""
wallet_scanner.py — Scan a block range for transactions sent by a specific EIP-7702 wallet.

For each transaction in the range, checks eth_getCode(from) to detect EIP-7702 delegation.
Keeps only transactions whose delegated contract matches --delegator.
For each kept transaction, computes:
  - fee factor   = (maxFeePerGas - maxPriorityFeePerGas) / baseFee
  - gasLimit factor = gasLimit / eth_estimateGas (simulated at block-1)

At the end, writes:
  --out   : JSONL file, one transaction per line with all computed fields
  --stats : JSON file with distribution maps (factor → count)

Usage:
  python wallet_scanner.py --delegator 0xD2e28229F6f2c235e57De2EbC727025A1D0530FB \\
                           --start 22000000 --end 22001000

  python wallet_scanner.py --delegator 0x63c0c19a282a1b52b07dd5a65b58948a07dae32b \\
                           --start 22000000
"""

import argparse
import json
import sys
import time
from collections import defaultdict

from web3 import Web3

# ── Known EIP-7702 delegators ─────────────────────────────────────────────────

KNOWN_DELEGATORS: dict[str, str] = {
    "0x63c0c19a282a1b52b07dd5a65b58948a07dae32b": "MetaMask",
    "0xd2e28229f6f2c235e57de2ebc727025a1d0530fb": "Trust Wallet",
    "0x80296ff8d1ed46f8e3c7992664d13b833504c2bb": "OKX Wallet",
    "0x5a7fc11397e9a8ad41bf10bf13f22b0a63f96f6d": "Ambire",
    "0x000000009b1d0af20d8c6d0a44e162d11f9b8f00": "Uniswap (Calibur)",
    "0x23e5f9c457a69ce776d20a8fe812a6701d66fce8": "Otim",
    "0x69007702764179f14f51cdce752f4f775d74e139": "Alchemy ModularAccount v2",
    "0xe66b31678d6c16e9ebf358268a790b763c133750": "Coinbase Smart Wallet",
}

EIP7702_PREFIX = bytes.fromhex("ef0100")

# address.lower() → delegated_contract.lower() or None
_code_cache: dict[str, str | None] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_delegated_contract(w3: Web3, address: str, block_number: int) -> str | None:
    key = address.lower()
    if key in _code_cache:
        return _code_cache[key]
    try:
        code = bytes(w3.eth.get_code(address, block_number))
        result = "0x" + code[3:23].hex().lower() if len(code) >= 23 and code[:3] == EIP7702_PREFIX else None
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


def _round_stat(value: float, decimals: int = 2) -> str:
    """Round float to `decimals` places and return as string key for the stats map."""
    return f"{value:.{decimals}f}"


# ── Core scanner ──────────────────────────────────────────────────────────────

def scan(
    w3: Web3,
    delegator: str,
    start: int,
    end: int,
    out_path: str,
    stats_path: str,
    delay: float,
) -> None:
    wallet_name = KNOWN_DELEGATORS.get(delegator.lower(), delegator)
    total_blocks = end - start + 1

    print(f"Target delegator : {delegator}  ({wallet_name})")
    print(f"Block range      : {start} → {end}  ({total_blocks} blocks)")
    print(f"Output           : {out_path}")
    print(f"Stats            : {stats_path}\n")

    # Stats accumulators
    fee_factor_counts:       dict[str, int] = defaultdict(int)
    gas_factor_counts:       dict[str, int] = defaultdict(int)
    priority_fee_counts:     dict[str, int] = defaultdict(int)

    matched_txs   = 0
    scanned_txs   = 0
    scanned_blocks = 0

    with open(out_path, "w") as out_f:
        for block_num in range(start, end + 1):
            try:
                block = w3.eth.get_block(block_num, full_transactions=True)
            except Exception as exc:
                print(f"[block {block_num}] fetch error: {exc}", flush=True)
                time.sleep(1)
                continue

            base_fee = block.get("baseFeePerGas")
            base_fee_gwei = base_fee / 1e9 if base_fee else None
            block_txs     = len(block["transactions"])
            block_matches = 0

            for tx in block["transactions"]:
                scanned_txs += 1

                # EIP-7702 check on the sender
                delegated = _get_delegated_contract(w3, tx["from"], block_num)
                if delegated is None or delegated.lower() != delegator.lower():
                    continue

                # Fee factor (EIP-1559 only)
                fee_factor       = None
                max_fee_gwei     = None
                max_priority_gwei = None
                if (
                    base_fee_gwei
                    and tx.get("maxFeePerGas") is not None
                    and tx.get("maxPriorityFeePerGas") is not None
                ):
                    max_fee_gwei      = tx["maxFeePerGas"]         / 1e9
                    max_priority_gwei = tx["maxPriorityFeePerGas"] / 1e9
                    if base_fee_gwei > 0:
                        fee_factor = (max_fee_gwei - max_priority_gwei) / base_fee_gwei

                # Gas limit factor
                gas_limit        = tx["gas"]
                estimated_gas    = _estimate_gas(w3, tx, block_num)
                gas_limit_factor = gas_limit / estimated_gas if estimated_gas else None

                # Build record
                record = {
                    "block":             block_num,
                    "hash":              tx["hash"].hex(),
                    "from":              tx["from"],
                    "to":                tx.get("to") or None,
                    "delegated_to":      delegated,
                    "wallet":            wallet_name,
                    "maxFeePerGas":      round(max_fee_gwei,      6) if max_fee_gwei      is not None else None,
                    "maxPriorityFeePerGas": round(max_priority_gwei, 4) if max_priority_gwei is not None else None,
                    "baseFeePerGas":     round(base_fee_gwei,     6) if base_fee_gwei     is not None else None,
                    "feeFactor":         round(fee_factor,        6) if fee_factor        is not None else None,
                    "gasLimit":          gas_limit,
                    "estimatedGas":      estimated_gas,
                    "gasLimitFactor":    round(gas_limit_factor,  6) if gas_limit_factor  is not None else None,
                }
                out_f.write(json.dumps(record) + "\n")
                out_f.flush()

                # Accumulate stats
                if fee_factor is not None:
                    fee_factor_counts[_round_stat(fee_factor, 2)] += 1
                if gas_limit_factor is not None:
                    gas_factor_counts[_round_stat(gas_limit_factor, 2)] += 1
                if max_priority_gwei is not None:
                    priority_fee_counts[_round_stat(max_priority_gwei, 4)] += 1

                matched_txs   += 1
                block_matches += 1

            scanned_blocks += 1
            pct = 100 * scanned_blocks / total_blocks
            print(
                f"[block {block_num}]  {pct:5.1f}%  txs={block_txs}  "
                f"matched={block_matches}  total_matched={matched_txs}",
                flush=True,
            )

            if delay > 0:
                time.sleep(delay)

    # ── Final stats ───────────────────────────────────────────────────────────
    stats = {
        "delegator":           delegator,
        "wallet":              wallet_name,
        "block_range":         {"start": start, "end": end},
        "scanned_blocks":      scanned_blocks,
        "scanned_txs":         scanned_txs,
        "matched_txs":         matched_txs,
        "fee_factor_dist":     dict(sorted(fee_factor_counts.items(),   key=lambda x: float(x[0]))),
        "gas_limit_factor_dist": dict(sorted(gas_factor_counts.items(), key=lambda x: float(x[0]))),
        "priority_fee_dist":   dict(sorted(priority_fee_counts.items(), key=lambda x: float(x[0]))),
    }

    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)

    # ── Print summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"SCAN COMPLETE — {wallet_name}")
    print(f"{'='*60}")
    print(f"  Blocks scanned   : {scanned_blocks}")
    print(f"  Txs scanned      : {scanned_txs}")
    print(f"  Txs matched      : {matched_txs}")

    print(f"\n  Fee factor distribution (rounded to 2 dec):")
    for k, v in stats["fee_factor_dist"].items():
        print(f"    {k}  →  {v} tx(s)")

    print(f"\n  gasLimit factor distribution (rounded to 2 dec):")
    for k, v in stats["gas_limit_factor_dist"].items():
        print(f"    {k}  →  {v} tx(s)")

    print(f"\n  maxPriorityFeePerGas distribution (Gwei, rounded to 4 dec):")
    for k, v in stats["priority_fee_dist"].items():
        print(f"    {k}  →  {v} tx(s)")

    print(f"\n  Transactions saved to : {out_path}")
    print(f"  Stats saved to        : {stats_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Scan a block range for EIP-7702 wallet transactions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--rpc",       default="http://192.168.1.42:8555/",  help="Ethereum JSON-RPC endpoint")
    p.add_argument("--delegator", default="0x80296ff8d1ed46f8e3c7992664d13b833504c2bb", help="EIP-7702 delegator contract address to match")
    p.add_argument("--start",     type=int, default=24960000, help="First block to scan")
    p.add_argument("--end",       type=int, default=24970000,  help="Last block to scan (default: latest)")
    p.add_argument("--out",       default="wallet_txs_okx2.jsonl",  help="Output JSONL file (default: wallet_txs.jsonl)")
    p.add_argument("--stats",     default="wallet_stats_ok2.json", help="Stats JSON file (default: wallet_stats.json)")
    p.add_argument("--delay",     type=float, default=0.02, help="Seconds between block fetches (default 0.02)")
    args = p.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        sys.exit("ERROR: Cannot connect to Ethereum node. Check --rpc.")

    end = args.end if args.end is not None else w3.eth.block_number
    print(f"Connected. Latest block: {w3.eth.block_number}\n")

    scan(
        w3        = w3,
        delegator = args.delegator,
        start     = args.start,
        end       = end,
        out_path  = args.out,
        stats_path= args.stats,
        delay     = args.delay,
    )


if __name__ == "__main__":
    main()