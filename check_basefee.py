#!/usr/bin/env python3

import os

from web3 import Web3

RPC_URL = os.environ["ETH_RPC_URL"]

w3 = Web3(Web3.HTTPProvider(RPC_URL))

block = w3.eth.get_block("latest")
base_fee_gwei = block["baseFeePerGas"] / 1e9

print(f"Block number : {block['number']}")
print(f"Base fee     : {base_fee_gwei:.8f} Gwei")