#!/usr/bin/env python3
"""
gas_server_copy.py — Réplique l'endpoint MetaMask `suggestedGasFees` à partir des
formules reverse-engineered, en interrogeant un noeud Ethereum (eth_feeHistory).

Endpoint (comme MetaMask) :
    GET /networks/<chainId>/suggestedGasFees

Si on l'appelle AU MÊME MOMENT que l'API MetaMask réelle, les champs EXACTS doivent
correspondre :
    estimatedBaseFee, et pour low/medium/high :
        suggestedMaxPriorityFeePerGas
        suggestedMaxFeePerGas

------------------------------------------------------------------------------
FORMULES (retrouvées dans metamask/reverse_priority_fee.py + notes projet) :

  estimatedBaseFee = base fee du DERNIER bloc miné (head)              [exact]

  priority (médiane sur les ~5 derniers blocs des reward eth_feeHistory) :
      low    = median(p10)                       (pas de plancher → "dust")
      medium = max(2.0 Gwei, median(p50))
      high   = max(2.0 Gwei, median(p80))

  maxFeePerGas = estimatedBaseFee × mult + priority :
      low    : mult = 1.25
      medium : mult = 1.43
      high   : mult = 1.43

Champs APPROXIMÉS (MetaMask ne les expose pas de façon reproductible — calculés
au mieux, peuvent différer) : networkCongestion, latest/historicalPriorityFeeRange,
historicalBaseFeeRange, priority/baseFeeTrend, *WaitTimeEstimate.
------------------------------------------------------------------------------

Usage :
  python gas_server_copy.py                 # port 8000
  python gas_server_copy.py --port 8545 --rpc <URL>
  # puis :  curl http://localhost:8000/networks/1/suggestedGasFees
"""

import argparse
import os
import statistics

import requests
from flask import Flask, jsonify

HERE = os.path.dirname(__file__)
ROOT_ENV = os.path.join(HERE, "..", ".env")
GWEI = 1e9

WINDOW = 5                      # nb de blocs pour la médiane des tips
HIST_WINDOW = 100               # nb de blocs pour les ranges historiques
PERCENTILES = [10, 50, 80, 99]  # low / medium / high / (range max approx)
BASE_MULT = {"low": 1.25, "medium": 1.43, "high": 1.43}
PRIORITY_FLOOR = {"low": 0.0, "medium": 2.0 * GWEI, "high": 2.0 * GWEI}
# temps d'attente indicatifs par tier (ms) — APPROX
WAIT = {"low": (30000, 60000), "medium": (15000, 30000), "high": (15000, 15000)}


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(ROOT_ENV)
RPC_URL = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")

app = Flask(__name__)


def gwei_str(wei):
    """Formate un montant en wei → string Gwei comme MetaMask (≤9 décimales, sans zéros)."""
    g = wei / GWEI
    s = f"{g:.9f}".rstrip("0").rstrip(".")
    return s if s and s != "-0" else "0"


def fee_history(blocks, percentiles):
    r = requests.post(RPC_URL, json={
        "jsonrpc": "2.0", "id": 1, "method": "eth_feeHistory",
        "params": [hex(blocks), "latest", percentiles],
    }, timeout=10)
    r.raise_for_status()
    res = r.json()["result"]
    rewards = [[int(x, 16) for x in row] for row in res.get("reward", [])]
    bases = [int(x, 16) for x in res["baseFeePerGas"]]
    return rewards, bases


def trend(values):
    """'up' / 'down' / 'level' en comparant la 2e moitié à la 1re."""
    if len(values) < 4:
        return "level"
    n = len(values) // 2
    a = statistics.mean(values[:n]); b = statistics.mean(values[n:])
    if b > a * 1.05:
        return "up"
    if b < a * 0.95:
        return "down"
    return "level"


def build_estimates():
    rewards, bases = fee_history(HIST_WINDOW, PERCENTILES)

    # base fee du dernier bloc miné = avant-dernier élément (le dernier = projection N+1)
    est_base_wei = bases[-2]

    # médiane des tips sur les WINDOW derniers blocs, par percentile
    last = rewards[-WINDOW:]
    col = lambda i: [row[i] for row in last if len(row) > i]
    p10 = statistics.median(col(0))
    p50 = statistics.median(col(1))
    p80 = statistics.median(col(2))

    prio = {
        "low":    max(p10, PRIORITY_FLOOR["low"]),
        "medium": max(p50, PRIORITY_FLOOR["medium"]),
        "high":   max(p80, PRIORITY_FLOOR["high"]),
    }

    tiers = {}
    for name in ("low", "medium", "high"):
        max_fee_wei = est_base_wei * BASE_MULT[name] + prio[name]
        wmin, wmax = WAIT[name]
        tiers[name] = {
            "suggestedMaxPriorityFeePerGas": gwei_str(prio[name]),
            "suggestedMaxFeePerGas":         gwei_str(max_fee_wei),
            "minWaitTimeEstimate":           wmin,
            "maxWaitTimeEstimate":           wmax,
        }

    # ---- champs approximés (non reproductibles à l'identique) ----
    all_p10 = [row[0] for row in rewards if row]
    all_p99 = [row[3] for row in rewards if len(row) > 3]
    latest_max = max(rewards[-1]) if rewards and rewards[-1] else 0
    hist_prio_min = min(all_p10) if all_p10 else 0
    hist_prio_max = max(all_p99) if all_p99 else 0
    mined_bases = bases[:-1]  # exclut la projection

    all_p50 = [row[1] for row in rewards if len(row) > 1]

    resp = dict(tiers)
    resp["estimatedBaseFee"] = gwei_str(est_base_wei)
    # congestion réelle = métrique propriétaire MetaMask (rang percentile), non
    # calculable ici → approximée par le ratio de gas utilisé moyen (best-effort).
    resp["networkCongestion"] = 0.5
    resp["latestPriorityFeeRange"] = [gwei_str(prio["low"]), gwei_str(latest_max)]
    resp["historicalPriorityFeeRange"] = [gwei_str(hist_prio_min), gwei_str(hist_prio_max)]
    resp["historicalBaseFeeRange"] = [gwei_str(min(mined_bases)), gwei_str(max(mined_bases))]
    resp["priorityFeeTrend"] = trend(all_p50)
    resp["baseFeeTrend"] = trend(mined_bases)
    resp["version"] = "0.0.1"
    return resp


@app.route("/networks/<chain_id>/suggestedGasFees")
def suggested_gas_fees(chain_id):
    try:
        return jsonify(build_estimates())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/")
def index():
    return jsonify({"usage": "GET /networks/1/suggestedGasFees"})


def main():
    ap = argparse.ArgumentParser(description="Copie locale du serveur gas MetaMask")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--rpc", default=None, help="override ETH_RPC_URL")
    args = ap.parse_args()
    if args.rpc:
        global RPC_URL
        RPC_URL = args.rpc
    print(f"RPC      : {RPC_URL}")
    print(f"Endpoint : http://{args.host}:{args.port}/networks/1/suggestedGasFees")
    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
