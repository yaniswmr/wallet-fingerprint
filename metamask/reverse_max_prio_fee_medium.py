#!/usr/bin/env python3
"""
Reverse-engineering de suggestedMaxPriorityFeePerGas (medium) de MetaMask.

Pour chaque n dans N_BLOCKS_LIST :
  predicted_priority(n) = mean(P50 tips des n derniers blocs via eth_feeHistory)

On compare avec le suggestedMaxPriorityFeePerGas retourné par MetaMask.
"""

import os
import sys
import requests
from web3 import Web3

METAMASK_API  = "https://gas.api.cx.metamask.io/networks/1/suggestedGasFees"
N_BLOCKS_LIST = [1, 5, 10, 20, 30, 50, 100, 200, 300]

rpc_url = os.environ.get("ETH_RPC_URL")
if not rpc_url:
    sys.exit("ERROR: ETH_RPC_URL non défini.")

w3 = Web3(Web3.HTTPProvider(rpc_url))
if not w3.is_connected():
    sys.exit("ERROR: Impossible de se connecter au nœud Ethereum.")

# 1. Fetch MetaMask (un seul appel)
print("Fetching MetaMask API…")
resp = requests.get(METAMASK_API, timeout=10)
resp.raise_for_status()
data = resp.json()

mm_priority = float(data["medium"]["suggestedMaxPriorityFeePerGas"])
mm_base_fee = float(data["estimatedBaseFee"])

print(f"\n  MetaMask medium — suggestedMaxPriorityFeePerGas = {mm_priority:.6f} Gwei")
print(f"  estimatedBaseFee                               = {mm_base_fee:.6f} Gwei\n")

# 2. eth_feeHistory pour chaque n_blocks
results = []
for n in N_BLOCKS_LIST:
    fh = w3.eth.fee_history(n, "latest", [50])
    tips_p50 = [blk[0] / 1e9 for blk in fh["reward"] if blk]
    predicted = sum(tips_p50) / len(tips_p50)
    abs_err = abs(predicted - mm_priority)
    pct_err = abs_err / mm_priority * 100 if mm_priority else float("inf")
    results.append((n, predicted, abs_err, pct_err))

# 3. Affichage trié par erreur absolue
results_sorted = sorted(results, key=lambda r: r[2])

header = f"  {'n':>5}  {'predicted':>10}  {'metamask':>10}  {'err_abs':>9}  {'err%':>6}"
sep    = "  " + "-" * 50
print(header)
print(sep)
for n, pred, abs_err, pct_err in results_sorted:
    star = " ◀" if (n, pred, abs_err, pct_err) == results_sorted[0] else ""
    print(f"  {n:>5}  {pred:>10.6f}  {mm_priority:>10.6f}  {abs_err:>9.6f}  {pct_err:>6.2f}%{star}")
print(sep)
print(f"\n  Meilleur candidat : n={results_sorted[0][0]} blocs  (err={results_sorted[0][2]:.6f} Gwei)\n")
