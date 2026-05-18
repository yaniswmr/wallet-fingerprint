#!/usr/bin/env python3
"""
Live search for the maxPriorityFeePerGas formula used by MetaMask.

Key insight from data analysis:
  MetaMask applies FLOORS before returning priority fees:
    pf_low  = max(FLOOR_LOW,  raw_p_low_from_feeHistory)
    pf_med  = max(FLOOR_MED,  raw_p_med_from_feeHistory)
    pf_high = max(FLOOR_MED,  raw_p_high_from_feeHistory)   ← higher percentile

  FLOOR_LOW = 0.0001 Gwei  (active ~51% of the time on mainnet)
  FLOOR_MED = 2.0    Gwei  (active ~95% of the time on mainnet)

  Fitting MAPE against floor-clamped values is meaningless.
  This script only uses samples where the floor is NOT active:
    - pf_low  > FLOOR_LOW  → real raw value visible
    - pf_med  > FLOOR_MED  → real raw value visible
    - pf_high > FLOOR_MED  → real raw high value visible

Every 15s:
  - Fetches MetaMask ground truth
  - If floors are NOT active: uses this sample for fitting
  - Tests all (n_blocks, percentile) combos against real values only
  - Prints top 10 combos for each tier, ranked by MAPE on valid samples only
"""

import os
import time
import numpy as np
from web3 import Web3

from src.extractor import extract_tips
from src.fetcher import fetch_fee_history, fetch_metamask, ALL_PERCENTILES

RPC_URL          = os.environ["ETH_RPC_URL"]
N_BLOCKS_OPTIONS = [1,2,3,4,5, 10, 15, 20, 25 , 30, 50, 100, 200 , 300]
INTERVAL         = 15  # seconds
TOP_N            = 10

FLOOR_LOW = 0.0001  # Gwei
FLOOR_MED = 2.0     # Gwei
FLOOR_TOL = 0.0005  # treat values within this tolerance as floored

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise SystemExit("ERROR: Cannot connect to Ethereum node.")

# scores[(n_blocks, percentile)] = {"low": [mape, ...], "med": [mape, ...], "high": [mape, ...]}
scores: dict[tuple, dict[str, list[float]]] = {}

sample       = 0
used_low     = 0
used_med     = 0
used_high    = 0
skipped      = 0

print("Searching for maxPriorityFeePerGas formula (floor-aware) — Ctrl+C to stop\n")
print(f"  Only using samples where MetaMask returns values ABOVE the floors:")
print(f"  pf_low > {FLOOR_LOW} Gwei  |  pf_med > {FLOOR_MED} Gwei  |  pf_high > {FLOOR_MED} Gwei\n")

while True:
    sample += 1

    actual   = fetch_metamask()
    pf_low   = actual.low.priority_fee
    pf_med   = actual.medium.priority_fee
    pf_high  = actual.high.priority_fee

    low_active  = pf_low  <= FLOOR_LOW + FLOOR_TOL
    med_active  = pf_med  <= FLOOR_MED + FLOOR_TOL
    high_active = pf_high <= FLOOR_MED + FLOOR_TOL

    has_useful = not low_active or not med_active or not high_active

    if not has_useful:
        skipped += 1
        print(f"[#{sample}] SKIP — all floors active  "
              f"(pf_low={pf_low:.6f}  pf_med={pf_med:.4f}  pf_high={pf_high:.4f})  "
              f"skipped={skipped}", flush=True)
        time.sleep(INTERVAL)
        continue

    histories = {n: fetch_fee_history(w3, n) for n in N_BLOCKS_OPTIONS}

    for n in N_BLOCKS_OPTIONS:
        fh = histories[n]
        for p in ALL_PERCENTILES:
            predicted = extract_tips(fh["rewards"], p)
            key = (n, p)
            if key not in scores:
                scores[key] = {"low": [], "med": [], "high": []}

            if not low_active and pf_low > 0:
                scores[key]["low"].append(abs(predicted - pf_low) / pf_low * 100)

            if not med_active and pf_med > 0:
                scores[key]["med"].append(abs(predicted - pf_med) / pf_med * 100)

            if not high_active and pf_high > 0:
                scores[key]["high"].append(abs(predicted - pf_high) / pf_high * 100)

    if not low_active:
        used_low += 1
    if not med_active:
        used_med += 1
    if not high_active:
        used_high += 1

    # ── Rank results ──────────────────────────────────────────────────────────

    def top(tier: str) -> list[tuple]:
        ranked = [
            (k, float(np.mean(v[tier])), float(np.std(v[tier])),
             float(np.mean([extract_tips(histories[k[0]]["rewards"], k[1])])))
            for k, v in scores.items() if v[tier]
        ]
        return sorted(ranked, key=lambda x: x[1])[:TOP_N]

    top_low  = top("low")
    top_med  = top("med")
    top_high = top("high")

    # ── Print ─────────────────────────────────────────────────────────────────

    print(f"\n{'='*65}")
    print(f"  Sample #{sample}  (used: low={used_low} med={used_med} high={used_high}  skipped={skipped})")
    print(f"  actual  low={pf_low:.6f}  med={pf_med:.6f}  high={pf_high:.6f}  base={actual.base_fee:.4f}")
    print(f"  floors  low={'ACTIVE' if low_active else 'off':6}  med={'ACTIVE' if med_active else 'off':6}  high={'ACTIVE' if high_active else 'off'}")
    print(f"{'='*65}")

    header = f"  {'#':>3}  {'blocks':>6}  {'pct':>4}  {'MAPE%':>7}  {'±':>6}  {'predicted':>10}"
    sep    = "  " + "-" * 50

    if top_low:
        print(f"\n  TOP {TOP_N} — LOW priority fee  ({used_low} valid samples)")
        print(header); print(sep)
        for i, (k, mean_mape, std_mape, pred) in enumerate(top_low):
            print(f"  {i+1:>3}  {k[0]:>6}  {k[1]:>4}  {mean_mape:>7.2f}  {std_mape:>6.2f}  {pred:>10.6f}")
    else:
        print(f"\n  LOW  — no valid samples yet (floor always active)")

    if top_med:
        print(f"\n  TOP {TOP_N} — MEDIUM priority fee  ({used_med} valid samples)")
        print(header); print(sep)
        for i, (k, mean_mape, std_mape, pred) in enumerate(top_med):
            print(f"  {i+1:>3}  {k[0]:>6}  {k[1]:>4}  {mean_mape:>7.2f}  {std_mape:>6.2f}  {pred:>10.6f}")
    else:
        print(f"\n  MEDIUM — no valid samples yet (floor always active)")

    if top_high:
        print(f"\n  TOP {TOP_N} — HIGH priority fee  ({used_high} valid samples)")
        print(header); print(sep)
        for i, (k, mean_mape, std_mape, pred) in enumerate(top_high):
            print(f"  {i+1:>3}  {k[0]:>6}  {k[1]:>4}  {mean_mape:>7.2f}  {std_mape:>6.2f}  {pred:>10.6f}")
    else:
        print(f"\n  HIGH   — no valid samples yet (floor always active)")

    time.sleep(INTERVAL)