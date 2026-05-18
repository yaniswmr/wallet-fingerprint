#!/usr/bin/env python3
"""
find_metamask.py — Scan the blockchain backwards for MetaMask-like transactions.

Two independent detection signals (EIP-1559 transactions only):

  [GAS]  fee_factor = (maxFeePerGas - maxPriorityFeePerGas) / baseFee
           factor ≈ 1.43  AND  priority ≈ 2.0    Gwei  → MetaMask medium/high
           factor ≈ 1.00  AND  priority ≈ 0.0001 Gwei  → MetaMask low

  [7702] eth_getCode(from) starts with 0xef0100 and the delegated address
         matches a known MetaMask delegator contract.

Display:
  ✅  GREEN  — both signals match  (near-certain MetaMask)
  ~   PLAIN  — gas pattern only
  ~   PLAIN  — EIP-7702 delegation only (shown even without gas match)

Runs indefinitely: scans backwards from latest block, restarts from new latest
when it reaches block 0.

Usage:
  python find_metamask.py --rpc <ETH_RPC_URL>
  python find_metamask.py --rpc <ETH_RPC_URL> --start 22000000
"""

import argparse
import os
import sys
import time

from web3 import Web3

# ── ANSI colors ───────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

# ── Gas detection thresholds ──────────────────────────────────────────────────

FACTOR_TOLERANCE      = 0.01
PRIORITY_MED_HIGH_TOL = 0.01    # Gwei
PRIORITY_LOW_TOL      = 0.00005 # Gwei

METAMASK_GAS_SIGNATURES = [
    (1.43,  2.0,    "medium/high  [factor=1.43, priority=2 Gwei]",       FACTOR_TOLERANCE, PRIORITY_MED_HIGH_TOL),
    (1.00,  0.0001, "low          [factor=1.00, priority=0.0001 Gwei]",  FACTOR_TOLERANCE, PRIORITY_LOW_TOL),
]

# ── EIP-7702 delegation detection ─────────────────────────────────────────────

EIP7702_PREFIX = bytes.fromhex("ef0100")  # 3 bytes written by the protocol

METAMASK_DELEGATORS: dict[str, str] = {
    "0x63c0c19a282a1b52b07dd5a65b58948a07dae32b": "EIP7702StatelessDeleGator",
}

# address.lower() → (is_metamask_7702: bool, delegated_to: str | None)
_code_cache: dict[str, tuple[bool, str | None]] = {}


def _estimate_gas_limit_factor(
    w3: Web3, tx, block_number: int
) -> tuple[int, int | None, float | None]:
    """
    Simulate eth_estimateGas at block_number-1 (state before tx inclusion).
    Returns (gas_limit, estimated_gas, gas_limit / estimated_gas).
    estimated_gas and factor are None if the call fails.
    """
    gas_limit = tx["gas"]
    call = {
        "from":  tx["from"],
        "to":    tx.get("to"),
        "value": tx["value"],
        "data":  tx.get("input", b""),
    }
    try:
        estimated = w3.eth.estimate_gas(call, block_number - 1)
        factor = gas_limit / estimated if estimated else None
        return gas_limit, estimated, factor
    except Exception:
        return gas_limit, None, None


# ── Detection helpers ─────────────────────────────────────────────────────────

def _detect_gas(factor: float, priority_gwei: float) -> str | None:
    for target_f, target_p, label, f_tol, p_tol in METAMASK_GAS_SIGNATURES:
        if abs(factor - target_f) <= f_tol and abs(priority_gwei - target_p) <= p_tol:
            return label
    return None


def _detect_7702(w3: Web3, address: str, block_number: int) -> tuple[bool, str | None]:
    """
    Returns (is_metamask, delegated_address_or_None).
    Results are cached per address to avoid redundant RPC calls.
    """
    key = address.lower()
    if key in _code_cache:
        return _code_cache[key]

    try:
        code = bytes(w3.eth.get_code(address, block_number))
        if len(code) >= 23 and code[:3] == EIP7702_PREFIX:
            delegated_to = "0x" + code[3:23].hex().lower()
            is_metamask  = delegated_to in METAMASK_DELEGATORS
            result: tuple[bool, str | None] = (is_metamask, delegated_to)
        else:
            result = (False, None)
    except Exception:
        result = (False, None)

    _code_cache[key] = result
    return result


# ── Block analysis ────────────────────────────────────────────────────────────

def analyze_block(block, w3: Web3) -> list[dict]:
    base_fee = block.get("baseFeePerGas")
    if base_fee is None or base_fee == 0:
        return []

    base_fee_gwei = base_fee / 1e9
    block_number  = block["number"]
    matches = []

    for tx in block["transactions"]:
        if tx.get("maxFeePerGas") is None or tx.get("maxPriorityFeePerGas") is None:
            continue

        max_fee_gwei      = tx["maxFeePerGas"]         / 1e9
        max_priority_gwei = tx["maxPriorityFeePerGas"] / 1e9
        factor            = (max_fee_gwei - max_priority_gwei) / base_fee_gwei

        gas_label   = _detect_gas(factor, max_priority_gwei)
        is_7702, delegated_to = _detect_7702(w3, tx["from"], block_number)

        if gas_label is None and not is_7702:
            continue

        gas_limit, estimated_gas, gas_limit_factor = _estimate_gas_limit_factor(
            w3, tx, block_number
        )

        matches.append({
            "hash":             tx["hash"].hex(),
            "from":             tx["from"],
            "to":               tx.get("to") or "contract-creation",
            "maxFee":           round(max_fee_gwei, 6),
            "maxPriority":      round(max_priority_gwei, 6),
            "baseFee":          round(base_fee_gwei, 6),
            "fee_factor":       round(factor, 6),
            "gas_label":        gas_label,
            "is_7702":          is_7702,
            "delegated_to":     delegated_to,
            "gas_limit":        gas_limit,
            "estimated_gas":    estimated_gas,
            "gas_limit_factor": round(gas_limit_factor, 6) if gas_limit_factor is not None else None,
        })

    return matches


# ── Display ───────────────────────────────────────────────────────────────────

def print_match(block_num: int, m: dict) -> None:
    both = m["gas_label"] and m["is_7702"]

    if both:
        header = f"{GREEN}✅ METAMASK CONFIRMED{RESET}  {m['gas_label']}  +  7702 delegation"
    elif m["is_7702"]:
        header = f"[7702] MetaMask Smart Account  (gas pattern: no match)"
    else:
        header = f"[GAS]  MetaMask pattern  {m['gas_label']}"

    delegator_name = METAMASK_DELEGATORS.get(m["delegated_to"] or "", "unknown delegator")

    print(f"\n[block {block_num}] {header}")
    print(f"  tx              : {m['hash']}")
    print(f"  from            : {m['from']}")
    print(f"  to              : {m['to']}")
    print(f"  maxFee          = {m['maxFee']:.6f} Gwei")
    print(f"  priority        = {m['maxPriority']:.6f} Gwei")
    print(f"  baseFee         = {m['baseFee']:.6f} Gwei")
    print(f"  fee factor      = {m['fee_factor']:.6f}")
    if m["is_7702"]:
        print(f"  {GREEN}7702 delegate   : {m['delegated_to']}  ({delegator_name}){RESET}")
    elif m["delegated_to"]:
        print(f"  7702 delegate   : {m['delegated_to']}  (not MetaMask)")
    print(f"  gasLimit        = {m['gas_limit']}")
    if m["estimated_gas"] is not None:
        print(f"  estimatedGas   = {m['estimated_gas']}  (simulated at block {block_num - 1})")
        print(f"  gasLimit factor = {m['gas_limit_factor']:.6f}  ({m['gas_limit']} / {m['estimated_gas']})")
    else:
        print(f"  estimatedGas   = N/A  (simulation failed)")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Scan blockchain backwards for MetaMask-like transactions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--rpc",   default=os.environ.get("ETH_RPC_URL"), required=not os.environ.get("ETH_RPC_URL"), help="Ethereum JSON-RPC endpoint")
    p.add_argument("--start", type=int, default=None, help="Start block (default: latest)")
    p.add_argument("--delay", type=float, default=0.05, help="Seconds between block fetches (default 0.05)")
    args = p.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        sys.exit("ERROR: Cannot connect to Ethereum node. Check --rpc.")

    print(f"Connected. Latest block: {w3.eth.block_number}")
    print(f"Signals: [GAS] gas pattern  |  [7702] EIP-7702 MetaMask delegation  |  {GREEN}✅ both{RESET}")
    print(f"Scanning backwards. Ctrl+C to stop.\n")

    total_blocks = 0
    total_matches = 0
    current = args.start if args.start is not None else w3.eth.block_number

    while True:
        try:
            block = w3.eth.get_block(current, full_transactions=True)
        except Exception as exc:
            print(f"[block {current}] fetch error: {exc}", flush=True)
            time.sleep(1)
            continue

        matches      = analyze_block(block, w3)
        total_blocks += 1
        total_matches += len(matches)

        tx_count = len(block["transactions"])
        eip1559  = sum(1 for tx in block["transactions"] if tx.get("maxFeePerGas") is not None)
        print(
            f"[block {current}] txs={tx_count} eip1559={eip1559} matches={len(matches)}"
            f"  (total: {total_blocks} blocks, {total_matches} MetaMask txs)",
            flush=True,
        )

        for m in matches:
            print_match(current, m)

        if current == 0:
            current = w3.eth.block_number
            print(f"\n── Reached genesis, restarting from block {current} ──\n")
        else:
            current -= 1

        if args.delay > 0:
            time.sleep(args.delay)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")