#!/usr/bin/env python3
"""
Polls MetaMask every 15s and computes the 3 max fee multipliers:
    multiplier = (maxFeePerGas - priorityFeePerGas) / estimatedBaseFee

Results are appended line by line to multipliers.jsonl (one JSON object per line).
"""

import json
import time
from datetime import datetime, timezone

from src.fetcher import fetch_metamask

OUT_FILE = "multipliers.jsonl"
INTERVAL = 15  # seconds

print(f"Polling MetaMask every {INTERVAL}s — writing to {OUT_FILE}")
print("Ctrl+C to stop\n")

while True:
    try:
        actual = fetch_metamask()
        base   = actual.base_fee

        m_low  = (actual.low.max_fee    - actual.low.priority_fee)    / base
        m_med  = (actual.medium.max_fee - actual.medium.priority_fee) / base
        m_high = (actual.high.max_fee   - actual.high.priority_fee)   / base

        entry = {
            "ts":       datetime.now(timezone.utc).isoformat(),
            "base_fee": round(base, 6),
            "m_low":    round(m_low,  4),
            "m_med":    round(m_med,  4),
            "m_high":   round(m_high, 4),
            "pf_low":   round(actual.low.priority_fee,    6),
            "pf_med":   round(actual.medium.priority_fee, 6),
            "pf_high":  round(actual.high.priority_fee,   6),
        }

        with open(OUT_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")

        print(f"[{entry['ts']}]  base={base:.4f}  "
              f"m_low={m_low:.4f}  m_med={m_med:.4f}  m_high={m_high:.4f}")

    except Exception as e:
        print(f"[ERROR] {e}")

    time.sleep(INTERVAL)