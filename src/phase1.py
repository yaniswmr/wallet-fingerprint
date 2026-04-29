"""
Phase 1 — Priority fee formula search.

Finds the best (n_blocks, p_low, p_med, p_high) that reproduces
suggestedMaxPriorityFeePerGas for each tier, using the mean as aggregation.

Search space:
  n_blocks    : N_BLOCKS_OPTIONS
  percentiles : all strictly increasing triplets from ALL_PERCENTILES
  ≈ 6 × C(15,3) ≈ 2 730 combinations — evaluated against n_samples snapshots.
"""

import itertools
import time

import numpy as np
from web3 import Web3

from .extractor import ALL_PERCENTILES, extract_tips
from .fetcher import fetch_fee_history, fetch_metamask
from .metrics import mape_priority
from .models import GasFees, SearchResult, Tier

N_BLOCKS_OPTIONS = [5, 10, 15, 20, 30, 50]

# All strictly-increasing percentile triplets
_TRIPLETS = list(itertools.combinations(ALL_PERCENTILES, 3))


def run(w3: Web3, n_samples: int = 5, pause: float = 15.0) -> list[SearchResult]:
    """
    Returns all SearchResult objects sorted by mean_mape ascending.
    """
    print("\n" + "=" * 60)
    print("PHASE 1 — Priority Fee Formula Search")
    print(f"  {len(N_BLOCKS_OPTIONS)} n_blocks × {len(_TRIPLETS)} percentile triplets = "
          f"{len(N_BLOCKS_OPTIONS) * len(_TRIPLETS):,} combos  (aggregation: mean)")
    print(f"  {n_samples} samples × {pause}s pause")
    print("=" * 60)

    # scores[key] = list of MAPE values, one per sample
    scores: dict[tuple, list[float]] = {}

    for sample_i in range(1, n_samples + 1):
        print(f"\n  [Sample {sample_i}/{n_samples}]", flush=True)

        actual = fetch_metamask()
        print(f"    actual → base={actual.base_fee:.3f}  "
              f"priority low={actual.low.priority_fee:.4f}  "
              f"med={actual.medium.priority_fee:.4f}  "
              f"high={actual.high.priority_fee:.4f} Gwei")

        # One RPC call per n_blocks value (covers all percentile combos)
        histories = {n: fetch_fee_history(w3, n) for n in N_BLOCKS_OPTIONS}

        for n in N_BLOCKS_OPTIONS:
            fh = histories[n]
            for (p_low, p_med, p_high) in _TRIPLETS:
                pf_low  = extract_tips(fh["rewards"], p_low)
                pf_med  = extract_tips(fh["rewards"], p_med)
                pf_high = extract_tips(fh["rewards"], p_high)

                pred = GasFees(
                    low    = Tier(priority_fee=pf_low,  max_fee=0),
                    medium = Tier(priority_fee=pf_med,  max_fee=0),
                    high   = Tier(priority_fee=pf_high, max_fee=0),
                    base_fee=0,
                )
                err = mape_priority(pred, actual)

                key = (n, p_low, p_med, p_high)
                scores.setdefault(key, []).append(err)

        if sample_i < n_samples:
            print(f"    waiting {pause}s …", flush=True)
            time.sleep(pause)

    results = sorted(
        [
            SearchResult(
                n_blocks=k[0], p_low=k[1], p_med=k[2], p_high=k[3],
                mean_mape=round(float(np.mean(v)), 3),
                std_mape=round(float(np.std(v)), 3),
            )
            for k, v in scores.items()
        ],
        key=lambda r: r.mean_mape,
    )

    _print_top(results)
    return results


def _print_top(results: list[SearchResult], n: int = 10) -> None:
    print(f"\n  Top {n} priority fee formulas:")
    print(f"  {'#':>3}  {'MAPE%':>7}  {'±':>6}  {'blocks':>6}  {'percentiles':>15}")
    print("  " + "-" * 50)
    for i, r in enumerate(results[:n]):
        print(f"  {i+1:>3}  {r.mean_mape:>7.2f}  {r.std_mape:>6.2f}  "
              f"{r.n_blocks:>6}  ({r.p_low:2d},{r.p_med:2d},{r.p_high:2d})")