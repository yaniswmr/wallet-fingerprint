#!/usr/bin/env python3
"""
reverse_priority_fee.py — Reverse-engineering de suggestedMaxPriorityFeePerGas de
MetaMask (low / medium / high), API gas.api.cx.metamask.io/networks/1/suggestedGasFees.

(Le multiplicateur base-fee est déjà connu — cf. notes : low ×1.25, medium/high ×1.43.)

Constat sur les données collectées (metamask/gas_fees_collected.db, valeurs en GWEI) :
  - low    : « dust » market-driven (0.0001–0.001 le plus souvent), JAMAIS planché à 2.
  - medium : = 2.0 dans 99.7 % des lignes (plancher). Hors-plancher : 37 lignes.
  - high   : = 2.0 dans 98.3 % des lignes (plancher). Hors-plancher : 236 lignes.

Donc le signal de marché n'apparaît QUE sur les lignes hors-plancher (pics de
congestion). Ce script :
  1. teste l'hypothèse  low == latestPriorityFeeRange[min]  (sans RPC, depuis la DB) ;
  2. rejoue eth_feeHistory aux blocs hors-plancher et fait une RECHERCHE EN GRILLE
     (fenêtre N, percentile, agrégation) pour retrouver le percentile de chaque tier ;
     formule = max(plancher, percentile).  Planchers : low≈0, medium=2.0, high=2.0 Gwei.

ETH_RPC_URL est lu depuis le .env racine du projet.

Usage :
  python reverse_priority_fee.py
  python reverse_priority_fee.py --low-sample 300 --rpc <URL>
"""

import argparse
import os
import sqlite3
import sys

import numpy as np
import requests

HERE = os.path.dirname(__file__)
ROOT_ENV = os.path.join(HERE, "..", ".env")
DEFAULT_DB = os.path.join(HERE, "gas_fees_collected.db")
GWEI = 1e9


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
DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")

PERCENTILES = [1, 2, 5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 95]
WINDOWS = [1, 2, 3, 4, 5, 8, 10, 15, 20, 30, 45, 50, 60, 100]
MAX_BLOCKS = max(WINDOWS)
FEE_OFFSET = 0

# (nom, colonne, plancher Gwei)
TIERS = (("low",    "low_max_priority_fee",    0.0),
         ("medium", "medium_max_priority_fee", 2.0),
         ("high",   "high_max_priority_fee",   2.0))


def rpc(url, method, params, _id=1):
    r = requests.post(url, json={"jsonrpc": "2.0", "id": _id, "method": method,
                                 "params": params}, timeout=20)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"{method} -> {j['error']}")
    return j["result"]


def fee_history(url, end_block, count, percentiles):
    """rewards[N][P] en WEI."""
    res = rpc(url, "eth_feeHistory", [hex(count), hex(end_block), percentiles])
    rewards = [[int(x, 16) for x in row] for row in res.get("reward", [])]
    return np.array(rewards, dtype=float)


def load_rows(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT block_number, estimated_base_fee, "
        "low_max_priority_fee, medium_max_priority_fee, high_max_priority_fee, "
        "latest_priority_fee_range_min "
        "FROM gas_fees WHERE block_number IS NOT NULL "
        "AND low_max_priority_fee IS NOT NULL ORDER BY id"
    ).fetchall()
    con.close()
    return rows


def predict_tip_gwei(rewards, p_idx, window, agg):
    col = rewards[-window:, p_idx]
    col = col[col >= 0]
    if col.size == 0:
        return None
    v = float(np.mean(col)) if agg == "mean" else float(np.median(col))
    return v / GWEI


# ---------------------------------------------------------------------------
# Hypothèse DB-only : low == latestPriorityFeeRange[min]
# ---------------------------------------------------------------------------
def check_low_equals_range_min(rows):
    print("\n" + "=" * 78)
    print("LOW — hypothèse  low == latestPriorityFeeRange[min]  (sans RPC)")
    print("=" * 78)
    eq = tot = 0
    for r in rows:
        lpr = r["latest_priority_fee_range_min"]
        if lpr is None:
            continue
        tot += 1
        if abs(float(r["low_max_priority_fee"]) - float(lpr)) < 0.001:
            eq += 1
    print(f"  low == range_min (à 0.001 Gwei près) : {eq}/{tot} = {eq/tot*100:.1f}%")
    print("  (les écarts = décalage de cache : la suggestion est figée tandis que le "
          "range bouge)\n")


# ---------------------------------------------------------------------------
# Grid search percentile par tier (lignes hors-plancher)
# ---------------------------------------------------------------------------
def analyse_tier(name, col, floor, rows, cache, offset):
    obs = np.array([float(r[col]) for r in rows])
    print(f"  --- {name.upper()} — {len(rows)} lignes hors-plancher (plancher {floor} Gwei) ---")
    if len(rows) < 8:
        print("      (trop peu de lignes hors-plancher pour ajuster)\n")
        return
    results = []
    for pi, p in enumerate(PERCENTILES):
        for w in WINDOWS:
            for agg in ("mean", "median"):
                preds, ob = [], []
                for r, o in zip(rows, obs):
                    rw = cache.get((r["block_number"], offset))
                    if rw is None or rw.size == 0:
                        continue
                    pr = predict_tip_gwei(rw, pi, min(w, rw.shape[0]), agg)
                    if pr is None:
                        continue
                    preds.append(max(pr, floor)); ob.append(o)   # applique le plancher
                if len(preds) < 8:
                    continue
                preds = np.array(preds); ob = np.array(ob)
                rel = np.abs(preds - ob) / np.maximum(ob, 0.01)
                results.append((float(np.median(rel)), float(np.mean(rel < 0.10)),
                                p, w, agg, len(preds)))
    results.sort(key=lambda x: (round(x[0], 4), -x[1]))
    print(f"      {'perc':>4} {'win':>4} {'agg':>6}  {'err_med':>8}  {'match<10%':>9}  n")
    for med_rel, match, p, w, agg, n in results[:8]:
        print(f"      {p:>4} {w:>4} {agg:>6}  {med_rel*100:>7.1f}%  {match*100:>8.0f}%  {n}")
    if results:
        _, _, bp, bw, bagg, _ = results[0]
        print(f"\n      => meilleur : max({floor}, {bagg}(p{bp}) sur {bw} blocs)\n")


def build_cache(blocks, url, offset):
    cache = {}
    total = len(blocks)
    print(f"\nRécupération feeHistory : {total} blocs (offset {offset:+d})…")
    for done, b in enumerate(sorted(blocks), 1):
        try:
            cache[(b, offset)] = fee_history(url, b + offset, MAX_BLOCKS, PERCENTILES)
        except Exception as e:
            cache[(b, offset)] = None
            if done <= 3:
                print(f"  [warn] feeHistory bloc {b + offset}: {e}")
        if done % 200 == 0:
            print(f"  … {done}/{total}")
    ok = sum(1 for v in cache.values() if v is not None)
    print(f"  -> {ok}/{total} réponses valides.")
    return cache


def sample(lst, n):
    if n and len(lst) > n:
        step = len(lst) / n
        return [lst[int(i * step)] for i in range(n)]
    return lst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--low-sample", type=int, default=250,
                    help="échantillon de lignes low (def 250)")
    ap.add_argument("--offset", type=int, default=FEE_OFFSET)
    args = ap.parse_args()

    rows = load_rows(args.db)
    if not rows:
        sys.exit("Aucune ligne.")
    print(f"DB     : {args.db}")
    print(f"RPC    : {args.rpc}")
    print(f"Lignes : {len(rows)}  (blocs {rows[0]['block_number']} → {rows[-1]['block_number']})")

    # 1) low == range_min (sans RPC)
    check_low_equals_range_min(rows)

    # 2) sélection des lignes par tier (hors-plancher pour med/high, échantillon pour low)
    tier_rows = {
        "low":    sample([r for r in rows], args.low_sample),
        "medium": [r for r in rows if float(r["medium_max_priority_fee"]) > 2.0001],
        "high":   sample([r for r in rows if float(r["high_max_priority_fee"]) > 2.0001], 250),
    }
    blocks = {r["block_number"] for rs in tier_rows.values() for r in rs}
    cache = build_cache(blocks, args.rpc, args.offset)

    print("\n" + "=" * 78)
    print("Priority fee par tier : recherche du percentile (lignes hors-plancher)")
    print("=" * 78)
    for name, col, floor in TIERS:
        analyse_tier(name, col, floor, tier_rows[name], cache, args.offset)


if __name__ == "__main__":
    main()