#!/usr/bin/env python3
"""
reverse_v2.py — Reverse OKX priority fee : balayage offset × fenêtre × percentile.

Améliore reverse_priority_fee.py :
  * récupère feeHistory une seule fois par bloc (offset avant large) puis tranche
    en mémoire pour tester plusieurs offsets de bloc sans re-requêter ;
  * cherche d'abord le MEILLEUR offset/percentile/fenêtre pour PROPOSE (l'ancre,
    non cappée), puis évalue safe et fast avec leurs caps :
        safe = min(tip_safe , propose)
        fast = min(tip_fast , 5 × propose)

Usage : python reverse_v2.py --sample 300
"""
import argparse, os, sqlite3, sys
import numpy as np, requests

HERE = os.path.dirname(__file__)
GWEI = 1e9


def load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(HERE, ".env"))
DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
DEFAULT_DB = os.path.join(HERE, "gas_fees_collected.db")

PERCENTILES = [5, 10, 15, 20, 25, 30, 40, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95]
WINDOWS = [1, 2, 3, 4, 5, 8, 10, 15, 20, 30]
OFFSETS = [-3, -2, -1, 0, 1, 2]
# feeHistory fetché avec end = block + FWD, count = FWD + max(WINDOWS) + marge
FWD = max(OFFSETS)
COUNT = FWD + max(WINDOWS) + 2


def rpc(url, method, params):
    r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                 "params": params}, timeout=20)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["result"]


def fee_history(url, end_block, count, pcts):
    res = rpc(url, "eth_feeHistory", [hex(count), hex(end_block), pcts])
    oldest = int(res["oldestBlock"], 16)
    rewards = np.array([[int(x, 16) for x in row] for row in res.get("reward", [])],
                       dtype=float)
    return oldest, rewards


def load_rows(db, sample):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT block_number, base_fee, safe_priority_fee, propose_priority_fee, "
        "fast_priority_fee FROM gas_fees WHERE block_number IS NOT NULL "
        "AND propose_priority_fee IS NOT NULL ORDER BY id").fetchall()
    con.close()
    if sample and len(rows) > sample:
        step = len(rows) / sample
        rows = [rows[int(i * step)] for i in range(sample)]
    return rows


def build_cache(rows, url):
    """cache[block] = (oldest_block, rewards[count, n_pcts]). end = block + FWD."""
    cache = {}
    blocks = sorted({r["block_number"] for r in rows})
    print(f"feeHistory : {len(blocks)} blocs (end=block+{FWD}, count={COUNT})…")
    for i, b in enumerate(blocks, 1):
        try:
            cache[b] = fee_history(url, b + FWD, COUNT, PERCENTILES)
        except Exception as e:
            cache[b] = None
            if i <= 3:
                print(f"  [warn] {b}: {e}")
        if i % 200 == 0:
            print(f"  … {i}/{len(blocks)}")
    ok = sum(v is not None for v in cache.values())
    print(f"  -> {ok}/{len(blocks)} ok.\n")
    return cache


def predict(cache, block, target_block, p_idx, window, agg):
    """tip = agg(reward[p_idx]) sur les `window` blocs finissant à target_block."""
    entry = cache.get(block)
    if entry is None:
        return None
    oldest, rewards = entry
    if rewards.size == 0:
        return None
    end_idx = target_block - oldest          # index de target_block dans rewards
    if end_idx < 0 or end_idx >= rewards.shape[0]:
        return None
    start = max(0, end_idx - window + 1)
    col = rewards[start:end_idx + 1, p_idx]
    col = col[col >= 0]
    if col.size == 0:
        return None
    return float(np.mean(col)) if agg == "mean" else float(np.median(col))


def fit_tier(rows, cache, obs_key, usable_mask):
    """Grille offset×perc×win×agg. Retourne liste triée (err_med, match10, ...)."""
    obs = np.array([int(r[obs_key]) for r in rows], dtype=float)
    results = []
    for off in OFFSETS:
        for pi, p in enumerate(PERCENTILES):
            for w in WINDOWS:
                for agg in ("mean", "median"):
                    preds, obss = [], []
                    for r, o, ok in zip(rows, obs, usable_mask):
                        if not ok:
                            continue
                        b = r["block_number"]
                        pr = predict(cache, b, b + off, pi, w, agg)
                        if pr is None:
                            continue
                        preds.append(pr); obss.append(o)
                    if len(preds) < 20:
                        continue
                    preds = np.array(preds); obss = np.array(obss)
                    rel = np.abs(preds - obss) / np.maximum(obss, GWEI * 0.01)
                    results.append((float(np.median(rel)), float(np.mean(rel < 0.10)),
                                    off, p, w, agg, len(preds)))
    results.sort(key=lambda x: (round(x[0], 4), -x[1]))
    return results


def fit_tier_bestoffset(rows, cache, obs_key, usable_mask):
    """Comme fit_tier mais prend le MEILLEUR offset PAR LIGNE (teste l'hypothèse
    'percentile d'un seul bloc récent, mal aligné'). Optimiste sur l'offset."""
    obs = np.array([int(r[obs_key]) for r in rows], dtype=float)
    results = []
    for pi, p in enumerate(PERCENTILES):
        for w in WINDOWS:
            for agg in ("mean", "median"):
                rels = []
                for r, o, ok in zip(rows, obs, usable_mask):
                    if not ok:
                        continue
                    b = r["block_number"]
                    best = None
                    for off in OFFSETS:
                        pr = predict(cache, b, b + off, pi, w, agg)
                        if pr is None:
                            continue
                        e = abs(pr - o) / max(o, GWEI * 0.01)
                        if best is None or e < best:
                            best = e
                    if best is not None:
                        rels.append(best)
                if len(rels) < 20:
                    continue
                rels = np.array(rels)
                results.append((float(np.median(rels)), float(np.mean(rels < 0.10)),
                                p, w, agg, len(rels)))
    results.sort(key=lambda x: (round(x[0], 4), -x[1]))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--rpc", default=DEFAULT_RPC)
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--bestoffset", action="store_true",
                    help="meilleur offset par ligne (révèle le percentile sous bruit d'alignement)")
    args = ap.parse_args()

    rows = load_rows(args.db, args.sample)
    if not rows:
        sys.exit("Aucune ligne.")
    print(f"DB {args.db}\nRPC {args.rpc}\n{len(rows)} lignes "
          f"(blocs {rows[0]['block_number']}→{rows[-1]['block_number']})")
    cache = build_cache(rows, args.rpc)

    # masques de saturation
    prop = np.array([int(r["propose_priority_fee"]) for r in rows], dtype=float)
    safe = np.array([int(r["safe_priority_fee"]) for r in rows], dtype=float)
    fast = np.array([int(r["fast_priority_fee"]) for r in rows], dtype=float)
    floor_p, floor_s = prop.min(), safe.min()
    prop_ok = prop > max(floor_p, 1) * 1.0001
    safe_cap = np.abs(safe - prop) <= 0.002 * np.maximum(prop, 1)   # safe == propose
    safe_ok = (safe > max(floor_s, 1) * 1.0001) & ~safe_cap
    fast_cap = np.abs(fast - 5 * prop) <= 0.002 * np.maximum(fast, 1)
    fast_ok = ~fast_cap & (fast > 1)

    print(f"propose : {int(prop_ok.sum())} exploitables")
    print(f"safe    : {int(safe_ok.sum())} exploitables ({int(safe_cap.sum())} cappés =propose)")
    print(f"fast    : {int(fast_ok.sum())} exploitables ({int(fast_cap.sum())} cappés =5×propose)")

    for label, key, mask in (("PROPOSE", "propose_priority_fee", prop_ok),
                             ("SAFE", "safe_priority_fee", safe_ok),
                             ("FAST", "fast_priority_fee", fast_ok)):
        if args.bestoffset:
            res = fit_tier_bestoffset(rows, cache, key, mask)
            print(f"\n=== {label} (meilleur offset/ligne) ===")
            print(f"  {'perc':>4} {'win':>3} {'agg':>6}  {'err_med':>8} {'match<10%':>9}  n")
            for em, m10, p, w, agg, n in res[:10]:
                print(f"  {p:>4} {w:>3} {agg:>6}  {em*100:>7.1f}% {m10*100:>8.0f}%  {n}")
        else:
            res = fit_tier(rows, cache, key, mask)
            print(f"\n=== {label} ===")
            print(f"  {'off':>3} {'perc':>4} {'win':>3} {'agg':>6}  {'err_med':>8} {'match<10%':>9}  n")
            for em, m10, off, p, w, agg, n in res[:10]:
                print(f"  {off:>3} {p:>4} {w:>3} {agg:>6}  {em*100:>7.1f}% {m10*100:>8.0f}%  {n}")


if __name__ == "__main__":
    main()