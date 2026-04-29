"""
Handles all external data fetching:
  - MetaMask Gas API  → ground truth
  - eth_feeHistory    → raw on-chain data
"""

import time
import requests
from web3 import Web3

from .models import GasFees, Tier

METAMASK_API = "https://gas.api.cx.metamask.io/networks/1/suggestedGasFees"

# All percentiles fetched in a single RPC call — reused across all combos
ALL_PERCENTILES = [5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 75, 80, 90, 95]


# Cette fonction va simplement récupérer les data de mtamask et le transformer en type locale

def fetch_metamask(retries: int = 3, backoff: float = 2.0) -> GasFees:
    """Fetch current suggested gas fees from MetaMask's API."""
    for attempt in range(retries):
        try:
            r = requests.get(METAMASK_API, timeout=10)
            r.raise_for_status()
            d = r.json()
            return GasFees(
                low=Tier(
                    priority_fee=float(d["low"]["suggestedMaxPriorityFeePerGas"]),
                    max_fee=float(d["low"]["suggestedMaxFeePerGas"]),
                ),
                medium=Tier(
                    priority_fee=float(d["medium"]["suggestedMaxPriorityFeePerGas"]),
                    max_fee=float(d["medium"]["suggestedMaxFeePerGas"]),
                ),
                high=Tier(
                    priority_fee=float(d["high"]["suggestedMaxPriorityFeePerGas"]),
                    max_fee=float(d["high"]["suggestedMaxFeePerGas"]),
                ),
                base_fee=float(d["estimatedBaseFee"]),
            )
        except Exception as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"MetaMask API failed after {retries} attempts: {exc}") from exc
            time.sleep(backoff * (attempt + 1))


def fetch_fee_history(w3: Web3, n_blocks: int) -> dict:
    """
    Single eth_feeHistory RPC call with ALL_PERCENTILES.
    Returns values in Gwei (divided by 1e9).

    Structure:
      base_fees[i]  → baseFeePerGas of block i  (index -1 = predicted next block)
      rewards[i][j] → j-th percentile tip of block i
    """
    result = w3.eth.fee_history(n_blocks, "latest", ALL_PERCENTILES)
    return {
        "base_fees": [bf / 1e9 for bf in result["baseFeePerGas"]],
        "rewards":   [[r / 1e9 for r in blk] for blk in result["reward"]],
        "n_blocks":  n_blocks,
    }
