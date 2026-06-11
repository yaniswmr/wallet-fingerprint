#!/usr/bin/env python3
"""Interroge l'endpoint gas_market de Rabby toutes les 12 secondes
et affiche les prix convertis en Gwei."""

import time
from datetime import datetime

import requests

URL = "https://api.rabby.io/v2/wallet/gas_market"
INTERVAL = 12  # secondes
WEI_PER_GWEI = 1e9


def wei_to_gwei(wei):
    return wei / WEI_PER_GWEI


def fetch():
    resp = requests.post(URL, json={"chain_id": "eth"}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def print_levels(levels):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts}]")
    print(f"  {'level':<8} {'price (Gwei)':>14} {'priority (Gwei)':>16} {'~sec':>6}")
    for lvl in levels:
        price = wei_to_gwei(lvl.get("price", 0))
        prio = wei_to_gwei(lvl.get("priority_price") or 0)
        secs = lvl.get("estimated_seconds", "")
        print(f"  {lvl.get('level', '?'):<8} {price:>14.4f} {prio:>16.4f} {secs:>6}")


def main():
    while True:
        try:
            print_levels(fetch())
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] erreur: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
