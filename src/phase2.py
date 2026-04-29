"""
Phase 2 — Max fee multiplier computation.

Base fee = baseFeePerGas of the latest block (confirmed to match MetaMask).
Priority fees = taken directly from MetaMask (ground truth).

For each sample, the multiplier per tier is derived analytically:

    maxFeePerGas = baseFee × multiplier + priorityFee
    ⟹  multiplier = (maxFeePerGas − priorityFee) / baseFee

Final multipliers are the mean over n_samples.
"""

import time

import numpy as np
from web3 import Web3

from .fetcher import fetch_metamask
from .metrics import mape_max_fee
from .models import GasFees, MultiplierResult, Tier


def run(w3: Web3, n_samples: int = 5, pause: float = 15.0) -> MultiplierResult:
    """
    Returns the MultiplierResult computed from n_samples MetaMask snapshots.
    """
    print("\n" + "=" * 60)
    print("PHASE 2 — Max Fee Multiplier Computation")
    print(f"  baseFee = latest block  |  priorityFee = MetaMask ground truth")
    print(f"  {n_samples} samples × {pause}s pause")
    print("=" * 60)

    m_lows, m_meds, m_highs = [], [], []
    mapes = []

    for i in range(1, n_samples + 1):
        actual = fetch_metamask()
        base   = w3.eth.get_block("latest")["baseFeePerGas"] / 1e9

        # Analytical resolution: multiplier = (maxFee - priorityFee) / baseFee
        m_low  = (actual.low.max_fee   - actual.low.priority_fee)   / base
        m_med  = (actual.medium.max_fee - actual.medium.priority_fee) / base
        m_high = (actual.high.max_fee  - actual.high.priority_fee)  / base

        m_lows.append(m_low)
        m_meds.append(m_med)
        m_highs.append(m_high)

        # MAPE sanity check: reconstruct maxFee with computed multipliers
        pred = GasFees(
            low    = Tier(priority_fee=actual.low.priority_fee,
                          max_fee=base * m_low  + actual.low.priority_fee),
            medium = Tier(priority_fee=actual.medium.priority_fee,
                          max_fee=base * m_med  + actual.medium.priority_fee),
            high   = Tier(priority_fee=actual.high.priority_fee,
                          max_fee=base * m_high + actual.high.priority_fee),
            base_fee=base,
        )
        mapes.append(mape_max_fee(pred, actual))

        print(f"  [{i}/{n_samples}]  base={base:.4f}  "
              f"m_low={m_low:.4f}  m_med={m_med:.4f}  m_high={m_high:.4f}")

        if i < n_samples:
            time.sleep(pause)

    result = MultiplierResult(
        m_low  = round(float(np.mean(m_lows)),  4),
        m_med  = round(float(np.mean(m_meds)),  4),
        m_high = round(float(np.mean(m_highs)), 4),
        mape   = round(float(np.mean(mapes)),   3),
    )

    print(f"\n  m_low={result.m_low}×  m_med={result.m_med}×  m_high={result.m_high}×  "
          f"MAPE={result.mape:.3f}%")
    return result