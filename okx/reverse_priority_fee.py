#!/usr/bin/env python3
"""
reverse_priority_fee.py — Reverse-engineering de la PRIORITY FEE de l'API gas-price
d'OKX (safe / propose / fast).

(Le facteur base-fee est suivi séparément par watch_multipliers.py ; rappel : OKX
renvoie suggestBaseFee == baseFee, donc base_mult = 1.0 — aucune inflation de base.)

Objectif : retrouver, à partir des données collectées (okx/gas_fees_collected.db)
et d'un noeud Ethereum, COMMENT le serveur OKX (fermé) calcule la priority fee :

    tip_tier = AGG( eth_feeHistory().reward[percentile_tier]  sur N blocs )

avec, pour le tier fast, un CAP observé :  fast = min(tip_fast , 5 × propose).

On rejoue eth_feeHistory aux blocs enregistrés et on fait une RECHERCHE EN GRILLE
sur (fenêtre N, percentile, agrégation) pour chaque tier. Les lignes "saturées"
(au plancher dust, ou cappées à 5×propose pour fast) sont exclues de l'ajustement
car elles masquent le signal de marché.

ETH_RPC_URL est lu depuis okx/.env.

Usage :
  python reverse_priority_fee.py
  python reverse_priority_fee.py --sample 300
  python reverse_priority_fee.py --rpc <URL>
"""

import argparse
import os
import sqlite3
import sys

import numpy as np
import requests

HERE     = os.path.dirname(__file__)
ENV_FILE = os.path.join(HERE, ".env")
DEFAULT_DB = os.path.join(HERE, "gas_fees_collected.db")
GWEI = 1e9


def load_env(path):
    """Charge un .env simple (KEY=VALUE) dans os.environ (sans écraser l'existant)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(ENV_FILE)
DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")

PERCENTILES = [5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 75, 80, 85, 90, 95, 99]
WINDOWS = [1, 2, 3, 4, 5, 8, 10, 15, 20, 30, 45, 50, 60, 100]
MAX_BLOCKS = max(WINDOWS)
# offset de bloc pour eth_feeHistory (block_number stocké = head au moment du poll).
FEE_OFFSET = 0

# (nom du tier, colonne SQL)
TIERS = (("safe", "safe_priority_fee"),
         ("propose", "propose_priority_fee"),
         ("fast", "fast_priority_fee"))
FAST_CAP_MULT = 5.0   # fast = min(percentile, 5 × propose) — cap observé


# ---------------------------------------------------------------------------
# RPC
# ---------------------------------------------------------------------------
def rpc(url, method, params, _id=1):
    r = requests.post(url, json={"jsonrpc": "2.0", "id": _id, "method": method,
                                 "params": params}, timeout=20)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"{method} -> {j['error']}")
    return j["result"]


def fee_history(url, end_block, count, percentiles):
    res = rpc(url, "eth_feeHistory", [hex(count), hex(end_block), percentiles])
    rewards = [[int(x, 16) for x in row] for row in res.get("reward", [])]
    return np.array(rewards, dtype=float)


# ---------------------------------------------------------------------------
# Chargement
# ---------------------------------------------------------------------------
def load_rows(db, sample):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT block_number, base_fee, "
        "safe_priority_fee, propose_priority_fee, fast_priority_fee "
        "FROM gas_fees WHERE block_number IS NOT NULL "
        "AND safe_priority_fee IS NOT NULL ORDER BY id"
    ).fetchall()
    con.close()
    if sample and len(rows) > sample:
        step = len(rows) / sample
        rows = [rows[int(i * step)] for i in range(sample)]
    return rows


# ---------------------------------------------------------------------------
# Priority fee
# ---------------------------------------------------------------------------
def predict_tip(rewards, p_idx, window, agg):
    col = rewards[-window:, p_idx]
    col = col[col >= 0]
    if col.size == 0:
        return None
    return float(np.mean(col)) if agg == "mean" else float(np.median(col))


def tier_mask(rows, name):
    """Lignes exploitables (non saturées) pour ajuster le percentile d'un tier."""
    vals = np.array([int(r[dict(TIERS)[name]]) for r in rows], dtype=float)
    floor = vals.min()
    above = vals > max(floor, 1) * 1.0001
    if name == "fast":
        # exclut les lignes cappées à 5×propose
        prop = np.array([int(r["propose_priority_fee"]) for r in rows], dtype=float)
        capped = np.abs(vals - FAST_CAP_MULT * prop) <= 0.005 * np.maximum(vals, 1)
        cap_share = float(np.mean(capped))
        return vals, above & ~capped, floor, cap_share
    return vals, above, floor, None


def analyse_priority(rows, cache, offset):
    print("\n" + "=" * 78)
    print("Priority fee (tip) par tier OKX : recherche du percentile / fenêtre")
    print("=" * 78)

    for name, _ in TIERS:
        obs_all, usable, floor, cap_share = tier_mask(rows, name)
        extra = ""
        if cap_share is not None:
            extra = f", cappé 5×propose {cap_share*100:.0f}% du temps"
        print(f"  {name:<8} plancher ≈ {floor/GWEI:.5f} Gwei, "
              f"{int(usable.sum())} lignes exploitables{extra}")

    print()
    for name, _ in TIERS:
        obs_all, usable, floor, cap_share = tier_mask(rows, name)
        results = []
        for pi, p in enumerate(PERCENTILES):
            for w in WINDOWS:
                for agg in ("mean", "median"):
                    preds, obs = [], []
                    for r, o, ok in zip(rows, obs_all, usable):
                        if not ok:
                            continue
                        rewards = cache.get((r["block_number"], offset))
                        if rewards is None or rewards.size == 0:
                            continue
                        pr = predict_tip(rewards, pi, min(w, rewards.shape[0]), agg)
                        if pr is None:
                            continue
                        preds.append(pr); obs.append(o)
                    if len(preds) < 8:
                        continue
                    preds = np.array(preds); obs = np.array(obs)
                    rel = np.abs(preds - obs) / np.maximum(obs, GWEI * 0.001)
                    results.append((float(np.median(rel)), float(np.mean(rel < 0.10)),
                                    p, w, agg, len(preds)))
        results.sort(key=lambda x: (round(x[0], 4), -x[1]))
        title = f"  --- {name.upper()} (tip)"
        if cap_share is not None:
            title += f" — hors lignes cappées"
        print(f"{title} ---")
        print(f"      {'perc':>4} {'win':>4} {'agg':>6}  {'err_med':>8}  {'match<10%':>9}  n")
        for med_rel, match, p, w, agg, n in results[:8]:
            print(f"      {p:>4} {w:>4} {agg:>6}  {med_rel*100:>7.1f}%  {match*100:>8.0f}%  {n}")
        if not results:
            print("      (pas assez de lignes exploitables)\n")
            continue

        # validation formule complète (sur lignes exploitables, percentile seul)
        _, _, bp, bw, bagg, _ = results[0]
        bpi = PERCENTILES.index(bp)
        so, sp = [], []
        fp, fo = [], []
        for r, o, ok in zip(rows, obs_all, usable):
            if not ok:
                continue
            rewards = cache.get((r["block_number"], offset))
            if rewards is None or rewards.size == 0:
                continue
            pr = predict_tip(rewards, bpi, min(bw, rewards.shape[0]), bagg)
            if pr is None:
                continue
            fp.append(pr); fo.append(o)
            if len(so) < 12:
                so.append(o); sp.append(pr)
        fp = np.array(fp); fo = np.array(fo)
        rel = np.abs(fp - fo) / np.maximum(fo, GWEI * 0.001)
        capnote = "  puis fast = min(tip, 5×propose)" if name == "fast" else ""
        print(f"\n      FORMULE retenue : tip_{name} = {bagg}(p{bp}) sur {bw} blocs{capnote}")
        print(f"        sur {len(fo)} lignes exploitables : match<10% = {np.mean(rel<0.10)*100:.0f}%, "
              f"match<5% = {np.mean(rel<0.05)*100:.0f}%, err médiane = {np.median(rel)*100:.1f}%")
        print(f"        échantillon (obs Gwei | prédit Gwei) :")
        print("        " + "  ".join(f"{o/GWEI:.2f}|{q/GWEI:.2f}" for o, q in zip(so, sp)))
        print()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def build_cache(rows, url, offset):
    cache = {}
    blocks = sorted({r["block_number"] for r in rows})
    total = len(blocks)
    print(f"\nRécupération feeHistory : {total} blocs (offset {offset:+d})…")
    for done, b in enumerate(blocks, 1):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--sample", type=int, default=150,
                    help="nombre de lignes échantillonnées (def 150)")
    ap.add_argument("--offset", type=int, default=FEE_OFFSET,
                    help=f"offset de bloc pour eth_feeHistory (def {FEE_OFFSET})")
    args = ap.parse_args()

    rows = load_rows(args.db, args.sample)
    if not rows:
        sys.exit("Aucune ligne exploitable.")
    print(f"DB     : {args.db}")
    print(f"RPC    : {args.rpc}")
    print(f"Lignes : {len(rows)}  (blocs {rows[0]['block_number']} → {rows[-1]['block_number']})")

    cache = build_cache(rows, args.rpc, args.offset)
    analyse_priority(rows, cache, args.offset)


if __name__ == "__main__":
    main()