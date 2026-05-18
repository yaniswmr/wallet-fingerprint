#!/usr/bin/env python3
"""
Reverse-engineer how Ledger computes low/medium/high priority fees
from eth_feeHistory. One RPC call per n_blocks with all percentiles,
then all (p_low, p_medium, p_high) combinations are tested in memory.
"""

import os
import requests
from itertools import combinations
from web3 import Web3

RPC_URL = os.environ["ETH_RPC_URL"]
LEDGER_URL = "https://explorers.api.live.ledger.com/blockchain/v4/eth/gastracker/barometer?display=eip1559"

w3 = Web3(Web3.HTTPProvider(RPC_URL))

# --- Fetch Ledger targets ---
resp = requests.get(LEDGER_URL, timeout=10)
resp.raise_for_status()
data = resp.json()
ledger = {
    "low":    int(data["low"]),
    "medium": int(data["medium"]),
    "high":   int(data["high"]),
}
print("Ledger targets (Gwei):")
for k, v in ledger.items():
    print(f"  {k:6s}: {v / 1e9:.4f}")
print()

BLOCK_COUNTS  = [4, 5, 10, 20, 50, 100]
ALL_PERCENTILES = [1, 5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 95, 99]
AGGREGATIONS  = ["mean"]
#AGGREGATIONS  = ["mean", "median", "last", "min", "max"]

def aggregate(values: list[int], method: str) -> int:
    if not values:
        return 0
    if method == "mean":
        return sum(values) // len(values)
    if method == "median":
        s = sorted(values)
        n = len(s)
        return (s[n // 2] + s[(n - 1) // 2]) // 2
    if method == "last":
        return values[-1]
    if method == "min":
        return min(values)
    if method == "max":
        return max(values)
    raise ValueError(method)

def error_pct(computed: dict, target: dict) -> float:
    total = 0.0
    for k in ("low", "medium", "high"):
        if target[k] == 0:
            continue
        total += abs(computed[k] - target[k]) / target[k]
    return total / 3 * 100

results = []

for n_blocks in BLOCK_COUNTS:
    print(f"Fetching eth_feeHistory({n_blocks} blocks, {len(ALL_PERCENTILES)} percentiles)...")
    try:
        history = w3.eth.fee_history(n_blocks, "latest", ALL_PERCENTILES)
    except Exception as e:
        print(f"  failed: {e}")
        continue

    rewards = history["reward"]
    if not rewards:
        continue

    # Pre-aggregate each percentile for all methods
    # agg_data[agg][p_idx] = aggregated value
    agg_data = {}
    for agg in AGGREGATIONS:
        agg_data[agg] = [
            aggregate([r[p_idx] for r in rewards], agg)
            for p_idx in range(len(ALL_PERCENTILES))
        ]

    # Test all combinations of 3 distinct percentile indices (low < medium < high)
    for i_low, i_med, i_high in combinations(range(len(ALL_PERCENTILES)), 3):
        p_low  = ALL_PERCENTILES[i_low]
        p_med  = ALL_PERCENTILES[i_med]
        p_high = ALL_PERCENTILES[i_high]

        for agg in AGGREGATIONS:
            computed = {
                "low":    agg_data[agg][i_low],
                "medium": agg_data[agg][i_med],
                "high":   agg_data[agg][i_high],
            }
            err = error_pct(computed, ledger)
            results.append({
                "n_blocks":    n_blocks,
                "percentiles": [p_low, p_med, p_high],
                "agg":         agg,
                "computed":    computed,
                "error_pct":   err,
            })

results.sort(key=lambda x: x["error_pct"])

print(f"\nTop 20 closest matches (out of {len(results)} tested):\n")
header = f"{'blocks':>6}  {'percentiles':>14}  {'agg':>6}  {'low Gwei':>10}  {'med Gwei':>10}  {'high Gwei':>10}  {'avg err%':>8}"
print(header)
print("-" * len(header))

for r in results[:20]:
    c = r["computed"]
    print(
        f"{r['n_blocks']:>6}  "
        f"{str(r['percentiles']):>14}  "
        f"{r['agg']:>6}  "
        f"{c['low'] / 1e9:>10.4f}  "
        f"{c['medium'] / 1e9:>10.4f}  "
        f"{c['high'] / 1e9:>10.4f}  "
        f"{r['error_pct']:>7.2f}%"
    )

print()
print("Ledger targets:")
print(f"{'':>6}  {'':>14}  {'':>6}  "
      f"{ledger['low'] / 1e9:>10.4f}  "
      f"{ledger['medium'] / 1e9:>10.4f}  "
      f"{ledger['high'] / 1e9:>10.4f}")