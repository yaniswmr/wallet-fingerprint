"""
Feature extraction from raw eth_feeHistory data.

Extracts priority fees (tips) from eth_feeHistory rewards.
Base fee is always taken directly from the latest block (confirmed to match
MetaMask's estimatedBaseFee).
"""

import numpy as np
from .fetcher import ALL_PERCENTILES

# Maps percentile value to its index inside ALL_PERCENTILES
PERC_TO_IDX: dict[int, int] = {p: i for i, p in enumerate(ALL_PERCENTILES)}


# ─── Tip aggregator ───────────────────────────────────────────────────────────

def extract_tips(rewards: list[list[float]], percentile: int) -> float:
    """
    Return the mean tip for the given percentile across all blocks.

    rewards[block][percentile_idx]  →  list[float] once we fix the column
    """
    idx = PERC_TO_IDX[percentile]
    tips = [blk[idx] for blk in rewards if blk]
    return float(np.mean(tips))
