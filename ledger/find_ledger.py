#!/usr/bin/env python3
"""
find_ledger.py — Scan the blockchain backwards for Ledger Live transactions.

Detection signal (EIP-1559 transactions only):

  fee_factor = (maxFeePerGas - maxPriorityFeePerGas) / baseFee  ≈ 1.27

  Ledger utilise le baseFee du bloc lui-même (nextBaseFee au moment de la soumission) :
    slow:   maxFeePerGas = baseFee × 1.27 + low_priority
    medium: maxFeePerGas = baseFee × 1.27 + med_priority
    fast:   maxFeePerGas = baseFee × 1.27 + high_priority

  Priority fees calculées via eth_feeHistory(100, block-1, [25, 50, 90]) :
    slow   = mean(p25 tips sur 100 blocs)
    medium = mean(p50 tips sur 100 blocs)
    fast   = mean(p90 tips sur 100 blocs)

Display :
  ✅  GREEN  — factor ≈ 1.27 ET priority matche un tier Ledger
  ~   BLUE   — factor ≈ 1.27 seulement (feeHistory non dispo ou pas de match)

Runs indefinitely: scans backwards from latest block, restarts from new latest
when it reaches block 0.

Usage:
  python find_ledger.py --rpc <ETH_RPC_URL>
  python find_ledger.py --rpc <ETH_RPC_URL> --start 22000000
"""

import argparse
import os
import sys
import time

from web3 import Web3

# ── ANSI colors ───────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"

# ── Thresholds ────────────────────────────────────────────────────────────────

FACTOR_LEDGER    = 1.27
FACTOR_TOLERANCE = 0.001  # ±0.001 autour de 1.27

# ── feeHistory cache (une entrée par numéro de bloc) ─────────────────────────

# block_number → (slow_gwei, medium_gwei, fast_gwei) | None
_fee_history_cache: dict[int, tuple[float, float, float] | None] = {}


def _get_ledger_priorities(w3: Web3, block_number: int) -> tuple[float, float, float] | None:
    """
    Calcule les trois priority fees Ledger pour un bloc donné.
    Appelle eth_feeHistory(100, block_number-1, [25, 50, 90]).
    Résultats mis en cache par numéro de bloc.
    """
    if block_number in _fee_history_cache:
        return _fee_history_cache[block_number]

    try:
        fh = w3.eth.fee_history(100, block_number - 1, [25, 50, 90])
        rewards = [r for r in fh["reward"] if r]
        n = len(rewards)
        slow   = sum(r[0] for r in rewards) / n / 1e9
        medium = sum(r[1] for r in rewards) / n / 1e9
        fast   = sum(r[2] for r in rewards) / n / 1e9
        result: tuple[float, float, float] | None = (slow, medium, fast)
    except Exception:
        result = None

    _fee_history_cache[block_number] = result
    return result


def _match_priority(priority_gwei: float, target: float) -> bool:
    return round(priority_gwei, 3) == round(target, 3)


def _detect_tier(priority_gwei: float, priorities: tuple[float, float, float]) -> str | None:
    slow, medium, fast = priorities
    if _match_priority(priority_gwei, slow):
        return "slow"
    if _match_priority(priority_gwei, medium):
        return "medium"
    if _match_priority(priority_gwei, fast):
        return "fast"
    return None


# ── Gas limit estimation ──────────────────────────────────────────────────────

def _estimate_gas_limit_factor(
    w3: Web3, tx, block_number: int
) -> tuple[int, int | None, float | None]:
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


# ── Block analysis ────────────────────────────────────────────────────────────

def analyze_block(block, w3: Web3) -> list[dict]:
    # Ledger utilise le baseFee du bloc lui-même (nextBaseFee au moment de la soumission)
    base_fee = block.get("baseFeePerGas")
    if base_fee is None or base_fee == 0:
        return []

    base_fee_gwei = base_fee / 1e9
    block_number  = block["number"]
    matches       = []
    priorities    = None   # chargé une seule fois si nécessaire

    for tx in block["transactions"]:
        if tx.get("maxFeePerGas") is None or tx.get("maxPriorityFeePerGas") is None:
            continue

        max_fee_gwei      = tx["maxFeePerGas"]         / 1e9
        max_priority_gwei = tx["maxPriorityFeePerGas"] / 1e9
        factor            = (max_fee_gwei - max_priority_gwei) / base_fee_gwei

        if abs(factor - FACTOR_LEDGER) > FACTOR_TOLERANCE:
            continue

        # Charge feeHistory une seule fois par bloc
        if priorities is None:
            priorities = _get_ledger_priorities(w3, block_number)

        tier = _detect_tier(max_priority_gwei, priorities) if priorities else None

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
            "tier":             tier,
            "ledger_slow":      round(priorities[0], 6) if priorities else None,
            "ledger_medium":    round(priorities[1], 6) if priorities else None,
            "ledger_fast":      round(priorities[2], 6) if priorities else None,
            "gas_limit":        gas_limit,
            "estimated_gas":    estimated_gas,
            "gas_limit_factor": round(gas_limit_factor, 6) if gas_limit_factor is not None else None,
        })

    return matches


# ── Display ───────────────────────────────────────────────────────────────────

def print_match(block_num: int, m: dict) -> None:
    confirmed = m["tier"] is not None

    if confirmed:
        tier_str = f"{GREEN}{m['tier']}{RESET}"
        header   = f"{GREEN}✅ LEDGER CONFIRMED{RESET}  [factor=1.27, tier={tier_str}]"
    else:
        header = f"{BLUE}[GAS]  Ledger pattern  [factor≈1.27]{RESET}"

    ff     = m["fee_factor"]
    ff_str = f"{GREEN}{ff:.6f}{RESET}" if abs(ff - FACTOR_LEDGER) <= FACTOR_TOLERANCE else f"{ff:.6f}"

    prio_str = f"{GREEN}{m['maxPriority']:.6f}{RESET}" if confirmed else f"{m['maxPriority']:.6f}"

    print(f"\n[block {block_num}] {header}")
    print(f"  tx              : {m['hash']}")
    print(f"  from            : {m['from']}")
    print(f"  to              : {m['to']}")
    print(f"  maxFee          = {m['maxFee']:.6f} Gwei")
    print(f"  priority        = {prio_str} Gwei")
    print(f"  baseFee         = {m['baseFee']:.6f} Gwei")
    print(f"  fee factor      = {ff_str}")

    if m["ledger_slow"] is not None:
        def _tier_label(name: str, val: float) -> str:
            tag = f"  {GREEN}◀ MATCH{RESET}" if m["tier"] == name else ""
            return f"{val:.6f} Gwei{tag}"

        print(f"  ledger slow     = {_tier_label('slow',   m['ledger_slow'])}")
        print(f"  ledger medium   = {_tier_label('medium', m['ledger_medium'])}")
        print(f"  ledger fast     = {_tier_label('fast',   m['ledger_fast'])}")
    else:
        print(f"  ledger tiers    = N/A  (feeHistory failed)")

    print(f"  gasLimit        = {m['gas_limit']}")
    if m["estimated_gas"] is not None:
        glf     = m["gas_limit_factor"]
        glf_str = f"{YELLOW}{glf:.6f}{RESET}" if abs(glf - 1.5) <= 0.05 else f"{glf:.6f}"
        print(f"  estimatedGas   = {m['estimated_gas']}  (simulated at block {block_num - 1})")
        print(f"  gasLimit factor = {glf_str}  ({m['gas_limit']} / {m['estimated_gas']})")
    else:
        print(f"  estimatedGas   = N/A  (simulation failed)")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Scan blockchain backwards for Ledger Live transactions",
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
    print(f"Signals: {BLUE}[GAS]{RESET} factor≈1.27  |  {GREEN}✅ factor + priority tier match{RESET}")
    print(f"Scanning backwards. Ctrl+C to stop.\n")

    total_blocks  = 0
    total_matches = 0
    current = args.start if args.start is not None else w3.eth.block_number

    while True:
        try:
            block = w3.eth.get_block(current, full_transactions=True)
        except Exception as exc:
            print(f"[block {current}] fetch error: {exc}", flush=True)
            time.sleep(1)
            continue

        matches       = analyze_block(block, w3)
        total_blocks  += 1
        total_matches += len(matches)

        tx_count = len(block["transactions"])
        eip1559  = sum(1 for tx in block["transactions"] if tx.get("maxFeePerGas") is not None)
        print(
            f"[block {current}] txs={tx_count} eip1559={eip1559} matches={len(matches)}"
            f"  (total: {total_blocks} blocks, {total_matches} Ledger txs)",
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
