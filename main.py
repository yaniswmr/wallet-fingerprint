#!/usr/bin/env python3
"""
MetaMask Gas API — Reverse Engineering Tool

Finds the eth_feeHistory parameters that best reproduce MetaMask's
suggestedGasFees endpoint (maxPriorityFeePerGas and maxFeePerGas).

Two-phase approach:
  Phase 1 — Grid search on priority fee formula (~13 000 combos, fast)
  Phase 2 — scipy L-BFGS-B for base fee multipliers (continuous, no grid)

Usage:
  pip install -r requirements.txt

  # Full run (phases 1 + 2 + validation)
  python main.py --rpc <ETH_RPC_URL>

  # Fewer samples for a quick test
  python main.py --rpc <ETH_RPC_URL> --samples 3 --pause 10 --validate 5

  # Skip phase 1 if you already have results.json
  python main.py --rpc <ETH_RPC_URL> --load results.json --phase 2

  # Validation only
  python main.py --rpc <ETH_RPC_URL> --load results.json --phase validate
"""

import argparse
import json
import sys

from dataclasses import asdict
from web3 import Web3

from src import phase1, phase2, validator
from src.models import MultiplierResult, SearchResult


def main() -> None:
    args = _parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        sys.exit("ERROR: Cannot connect to Ethereum node. Check --rpc.")
    print(f"Connected. Latest block: {w3.eth.block_number}")

    state: dict = {}
    if args.load:
        with open(args.load) as f:
            state = json.load(f)
        print(f"Loaded state from {args.load}")

    # ── Phase 1 ──────────────────────────────────────────────────────────────
    if args.phase in ("1", "all") and "best_priority" not in state:
        results = phase1.run(w3, n_samples=args.samples, pause=args.pause)
        state["phase1_top20"] = [asdict(r) for r in results[:20]]
        state["best_priority"] = asdict(results[0])
        _save(state, args.out)

    best_priority = SearchResult(**state["best_priority"])

    # ── Phase 2 ──────────────────────────────────────────────────────────────
    if args.phase in ("2", "all") and "best_multiplier" not in state:
        result = phase2.run(w3, n_samples=args.samples, pause=args.pause)
        state["best_multiplier"] = asdict(result)
        _save(state, args.out)

    best_multiplier = MultiplierResult(**state["best_multiplier"])

    # ── Formula summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("FORMULA FOUND")
    print("=" * 60)
    print(best_multiplier.formula_str(best_priority))
    print(f"\n  Priority MAPE (phase 1) : {best_priority.mean_mape:.2f}%")
    print(f"  Max fee  MAPE (phase 2) : {best_multiplier.mape:.2f}%")

    # ── Validation ────────────────────────────────────────────────────────────
    if args.phase in ("validate", "all"):
        validator.run(
            w3, best_priority, best_multiplier,
            n_checks=args.validate, pause=args.pause,
        )

    _save(state, args.out)
    print(f"\nAll results saved to {args.out}")


def _save(state: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reverse engineer MetaMask gas fee formulas via eth_feeHistory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--rpc",      required=True,  help="Ethereum JSON-RPC endpoint URL")
    p.add_argument("--samples",  type=int,   default=5,    help="Snapshots per phase (default 5)")
    p.add_argument("--pause",    type=float, default=15.0, help="Seconds between snapshots (default 15)")
    p.add_argument("--validate", type=int,   default=10,   help="Validation checks (default 10)")
    p.add_argument("--out",      default="results.json",   help="Output JSON file (default results.json)")
    p.add_argument("--load",     metavar="FILE",           help="Resume from a previous results.json")
    p.add_argument(
        "--phase",
        choices=["1", "2", "all", "validate"],
        default="all",
        help="Which phase to run (default: all)",
    )
    return p.parse_args()


if __name__ == "__main__":
    main()
