#!/usr/bin/env python3
"""
reverse_final.py — Détermination fine de la formule priority fee OKX.

Acquis : window = 1 bloc (percentile d'un SEUL bloc récent). Reste à fixer :
  * le percentile exact de chaque tier (safe / propose / fast) ;
  * l'offset de bloc dominant (le vrai lag head_functori vs head_OKX).

Pour chaque tier : pour chaque ligne, on cherche le couple (offset, percentile)
qui colle le mieux, puis on histogramme les offsets et percentiles gagnants.
"""
import argparse, os, sqlite3, sys
import numpy as np, requests
from collections import Counter

HERE = os.path.dirname(__file__); GWEI = 1e9


def load_env(p):
    if os.path.exists(p):
        for l in open(p):
            l = l.strip()
            if l and not l.startswith("#") and "=" in l:
                k, v = l.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(HERE, ".env"))
DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
DEFAULT_DB = os.path.join(HERE, "gas_fees_collected.db")

PCTS = [40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 99]
OFFSETS = list(range(-4, 4))
FWD = max(OFFSETS); COUNT = FWD - min(OFFSETS) + 2


def rpc(u, m, p):
    r = requests.post(u, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p}, timeout=20)
    r.raise_for_status(); j = r.json()
    if "error" in j: raise RuntimeError(j["error"])
    return j["result"]


def fee_history(u, end, count, pcts):
    res = rpc(u, "eth_feeHistory", [hex(count), hex(end), pcts])
    return int(res["oldestBlock"], 16), np.array(
        [[int(x, 16) for x in row] for row in res.get("reward", [])], dtype=float)


def load_rows(db, sample):
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT block_number, safe_priority_fee, propose_priority_fee, "
                       "fast_priority_fee FROM gas_fees WHERE block_number IS NOT NULL "
                       "AND propose_priority_fee IS NOT NULL ORDER BY id").fetchall()
    con.close()
    if sample and len(rows) > sample:
        step = len(rows) / sample
        rows = [rows[int(i * step)] for i in range(sample)]
    return rows


def build_cache(rows, url):
    cache = {}; blocks = sorted({r["block_number"] for r in rows})
    print(f"feeHistory : {len(blocks)} blocs…")
    for i, b in enumerate(blocks, 1):
        try: cache[b] = fee_history(url, b + FWD, COUNT, PCTS)
        except Exception: cache[b] = None
        if i % 200 == 0: print(f"  … {i}/{len(blocks)}")
    print(f"  -> {sum(v is not None for v in cache.values())}/{len(blocks)} ok.\n")
    return cache


def reward_at(cache, block, target):
    """ligne reward[target] (tous percentiles) ou None."""
    e = cache.get(block)
    if e is None: return None
    oldest, rew = e
    if rew.size == 0: return None
    idx = target - oldest
    if 0 <= idx < rew.shape[0]: return rew[idx]
    return None


def analyse(rows, cache, key, mask, label):
    obs = np.array([int(r[key]) for r in rows], dtype=float)
    off_win = Counter(); pct_win = Counter(); errs = []
    # fit global : pour chaque (offset, pct) calcule l'erreur médiane sur toutes les lignes
    grid = {}
    for off in OFFSETS:
        for pi, p in enumerate(PCTS):
            rels = []
            for r, o, ok in zip(rows, obs, mask):
                if not ok: continue
                row = reward_at(cache, r["block_number"], r["block_number"] + off)
                if row is None: continue
                rels.append(abs(row[pi] - o) / max(o, GWEI * 0.01))
            if len(rels) >= 20:
                grid[(off, p)] = (float(np.median(rels)), float(np.mean(np.array(rels) < 0.10)), len(rels))
    # meilleur (offset, pct) global
    best = sorted(grid.items(), key=lambda kv: (round(kv[1][0], 4), -kv[1][1]))
    print(f"\n=== {label} : meilleurs (offset, percentile) GLOBAUX (window=1) ===")
    print(f"  {'off':>3} {'pct':>3}  {'err_med':>8} {'match<10%':>9}  n")
    for (off, p), (em, m10, n) in best[:8]:
        print(f"  {off:>3} {p:>3}  {em*100:>7.1f}% {m10*100:>8.0f}%  {n}")

    # par ligne : offset & pct gagnants (pour histogramme)
    for r, o, ok in zip(rows, obs, mask):
        if not ok: continue
        be = None; bo = bp = None
        for off in OFFSETS:
            row = reward_at(cache, r["block_number"], r["block_number"] + off)
            if row is None: continue
            for pi, p in enumerate(PCTS):
                e = abs(row[pi] - o) / max(o, GWEI * 0.01)
                if be is None or e < be:
                    be, bo, bp = e, off, p
        if be is not None:
            off_win[bo] += 1; pct_win[bp] += 1; errs.append(be)
    print(f"  offset gagnant/ligne : {dict(sorted(off_win.items()))}")
    print(f"  percentile gagnant/ligne : {dict(sorted(pct_win.items()))}")
    print(f"  err médiane (best off+pct par ligne) : {np.median(errs)*100:.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--sample", type=int, default=400)
    a = ap.parse_args()
    rows = load_rows(a.db, a.sample)
    if not rows: sys.exit("vide")
    print(f"{len(rows)} lignes (blocs {rows[0]['block_number']}→{rows[-1]['block_number']})")
    cache = build_cache(rows, a.rpc)
    prop = np.array([int(r["propose_priority_fee"]) for r in rows], float)
    safe = np.array([int(r["safe_priority_fee"]) for r in rows], float)
    fast = np.array([int(r["fast_priority_fee"]) for r in rows], float)
    prop_ok = prop > prop.min() * 1.0001
    safe_ok = (safe > safe.min() * 1.0001) & (np.abs(safe - prop) > 0.002 * np.maximum(prop, 1))
    fast_ok = (np.abs(fast - 5 * prop) > 0.002 * np.maximum(fast, 1)) & (fast > 1)
    analyse(rows, cache, "safe_priority_fee", safe_ok, "SAFE")
    analyse(rows, cache, "propose_priority_fee", prop_ok, "PROPOSE")
    analyse(rows, cache, "fast_priority_fee", fast_ok, "FAST")


if __name__ == "__main__":
    main()