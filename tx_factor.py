#!/usr/bin/env python3
"""
Compute the baseFee multiplier used in a given EIP-1559 transaction.

Formula:  maxFeePerGas = baseFee * factor + maxPriorityFeePerGas
  =>  factor = (maxFeePerGas - maxPriorityFeePerGas) / baseFee

Usage:
  python tx_factor.py --rpc <ETH_RPC_URL> --tx <TX_HASH>²
"""

import argparse
import os
import sys

from web3 import Web3


def main():
    p = argparse.ArgumentParser(description="Compute baseFee multiplier from an EIP-1559 tx")
    p.add_argument("--rpc", default=os.environ.get("ETH_RPC_URL"), required=not os.environ.get("ETH_RPC_URL"), help="Ethereum JSON-RPC endpoint URL")
    p.add_argument("--tx",  default="0xc31c72de822a397513ac98d9e47b75e8c4065331ce9c01091744be50884518b3", help="Transaction hash (0x...)")
    args = p.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        sys.exit("ERROR: Cannot connect to Ethereum node. Check --rpc.")

    tx = w3.eth.get_transaction(args.tx)

    if tx.get("maxFeePerGas") is None or tx.get("maxPriorityFeePerGas") is None:
        sys.exit("ERROR: This is not an EIP-1559 transaction (no maxFeePerGas / maxPriorityFeePerGas).")

    if tx.get("blockNumber") is None:
        sys.exit("ERROR: Transaction is not yet included in a block (pending).")

    block = w3.eth.get_block(tx["blockNumber"])

    max_fee_gwei      = tx["maxFeePerGas"]      / 1e9
    max_priority_gwei = tx["maxPriorityFeePerGas"] / 1e9
    base_fee_gwei     = block["baseFeePerGas"]  / 1e9

    if base_fee_gwei == 0:
        sys.exit("ERROR: baseFee is 0, cannot compute factor.")

    factor = (max_fee_gwei - max_priority_gwei) / base_fee_gwei

    # eth_estimateGas — simulate at block N-1 (state before tx inclusion)
    gas_limit = tx["gas"]
    estimate_block = tx["blockNumber"] - 1
    call = {
        "from":  tx["from"],
        "to":    tx["to"],
        "value": tx["value"],
        "data":  tx.get("input", b""),
    }
    estimated_gas = None
    gas_factor    = None
    gas_error     = None
    try:
        estimated_gas = w3.eth.estimate_gas(call, estimate_block)
        gas_factor = gas_limit / estimated_gas if estimated_gas else None
    except Exception as exc:
        gas_error = str(exc)

    print(f"Transaction : {args.tx}")
    print(f"Block       : {tx['blockNumber']}")
    print()
    print("── Fee parameters ──────────────────────────────────")
    print(f"  maxFeePerGas         = {max_fee_gwei:.6f} Gwei")
    print(f"  maxPriorityFeePerGas = {max_priority_gwei:.6f} Gwei")
    print(f"  baseFeePerGas        = {base_fee_gwei:.6f} Gwei")
    print(f"  fee factor           = ({max_fee_gwei:.6f} - {max_priority_gwei:.6f}) / {base_fee_gwei:.6f}")
    print(f"                       = {factor:.6f}")
    print()
    print("── Gas parameters ──────────────────────────────────")
    print(f"  gasLimit             = {gas_limit}")
    if estimated_gas is not None:
        print(f"  estimatedGas         = {estimated_gas}  (simulated at block {estimate_block})")
        print(f"  gas factor           = {gas_limit} / {estimated_gas}")
        print(f"                       = {gas_factor:.6f}")
    else:
        print(f"  estimatedGas         = N/A  ({gas_error})")


if __name__ == "__main__":
    main()