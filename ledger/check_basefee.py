#!/usr/bin/env python3

import requests
from web3 import Web3

RPC_URL = "http://192.168.1.42:8555/"
LEDGER_URL = "https://explorers.api.live.ledger.com/blockchain/v4/eth/gastracker/barometer?display=eip1559"

w3 = Web3(Web3.HTTPProvider(RPC_URL))

latest = w3.eth.get_block("latest")
latest_number = latest["number"]

print("Last 5 blocks base fee:")
for n in range(latest_number - 4, latest_number + 1):
    b = w3.eth.get_block(n)
    print(f"  #{b['number']}  {b['baseFeePerGas'] / 1e9:.4f} Gwei")

gas_target = latest["gasLimit"] // 2
delta = latest["gasUsed"] - gas_target
eip1559_next_base = (latest["baseFeePerGas"] + latest["baseFeePerGas"] * delta // gas_target // 8) / 1e9

resp = requests.get(LEDGER_URL, timeout=10)
resp.raise_for_status()
data = resp.json()
ledger_next_base = int(data["next_base"]) / 1e9

print(f"\nEIP-1559 next base fee : {eip1559_next_base:.4f} Gwei")
print(f"Ledger next_base fee   : {ledger_next_base:.4f} Gwei")
