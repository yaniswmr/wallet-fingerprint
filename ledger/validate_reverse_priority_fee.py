#!/usr/bin/env python3

import time
import requests
from web3 import Web3

RPC_URL   = "http://192.168.1.42:8555/"
LEDGER_URL = "https://explorers.api.live.ledger.com/blockchain/v4/eth/gastracker/barometer?display=eip1559"

GREEN = "\033[92m✓\033[0m"
RED   = "\033[91m✗\033[0m"

w3 = Web3(Web3.HTTPProvider(RPC_URL))

def mean_reward(history, idx: int) -> int:
    rewards = [r[idx] for r in history["reward"]]
    return sum(rewards) // len(rewards)

def check():
    resp = requests.get(LEDGER_URL, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    ledger = {
        "low":    int(data["low"]),
        "medium": int(data["medium"]),
        "high":   int(data["high"]),
    }

    history = w3.eth.fee_history(100, "latest", [25, 50, 90])

    computed = {
        "low":    mean_reward(history, 0),
        "medium": mean_reward(history, 1),
        "high":   mean_reward(history, 2),
    }

    print(f"\n{'─' * 62}")
    print(f"  {'':4}  {'computed':>12}  {'ledger':>12}  {'diff%':>8}  {'ok':>4}")
    print(f"{'─' * 62}")
    for label, pct in [("low", 25), ("medium", 50), ("high", 90)]:
        c = computed[label]
        t = ledger[label]
        diff = (c - t) / t * 100 if t else 0
        ok = abs(diff) < 5
        icon = GREEN if ok else RED
        print(
            f"  {label:6}(p{pct:<2})  "
            f"{c / 1e9:>10.4f}  "
            f"{t / 1e9:>10.4f}  "
            f"{diff:>+7.2f}%  "
            f"  {icon}"
        )
    print(f"{'─' * 62}")

while True:
    try:
        check()
    except Exception as e:
        print(f"\033[91mError: {e}\033[0m")
    time.sleep(15)