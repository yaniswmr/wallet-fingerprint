#!/usr/bin/env python3
"""
validate_model.py — Valide le modèle reverse-engineeré de la priority fee OKX :

    base       : suggestBaseFee = baseFee                 (mult = 1.0, pas d'inflation)
    tip_safe    = p65  d'un bloc récent ;  safe    = min(tip_safe , propose)
    tip_propose = p80  d'un bloc récent ;  propose = tip_propose
    tip_fast    = p90  d'un bloc récent ;  fast    = min(tip_fast , 5 × propose)

L'API gas-price d'OKX ne renvoie PAS de numéro de bloc → le bloc de référence
exact est inconnu (jitter ±4 blocs vs le head Functori enregistré). On neutralise
donc le timing en prenant, par ligne, le meilleur bloc dans une petite fenêtre
d'offsets, et on mesure le taux de correspondance à percentile FIXE.
"""
import argparse, os, sqlite3, sys
import numpy as np, requests

HERE = os.path.dirname(__file__); GWEI = 1e9
P_SAFE, P_PROPOSE, P_FAST = 65, 80, 90
OFFSETS = list(range(-4, 4)); FWD = max(OFFSETS); COUNT = FWD - min(OFFSETS) + 2
PCTS = [P_SAFE, P_PROPOSE, P_FAST]


def load_env(p):
    if os.path.exists(p):
        for l in open(p):
            l = l.strip()
            if l and not l.startswith("#") and "=" in l:
                k, v = l.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(os.path.join(HERE, ".env"))
RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
DB = os.path.join(HERE, "gas_fees_collected.db")


def fee_history(u, end, count, pcts):
    r = requests.post(u, json={"jsonrpc": "2.0", "id": 1, "method": "eth_feeHistory",
                               "params": [hex(count), hex(end), pcts]}, timeout=20)
    r.raise_for_status(); res = r.json()["result"]
    return int(res["oldestBlock"], 16), np.array(
        [[int(x, 16) for x in row] for row in res.get("reward", [])], dtype=float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=400)
    a = ap.parse_args()
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    rows = con.execute("SELECT block_number, safe_priority_fee, propose_priority_fee, "
                       "fast_priority_fee FROM gas_fees WHERE block_number IS NOT NULL "
                       "AND propose_priority_fee IS NOT NULL ORDER BY id").fetchall()
    con.close()
    if a.sample and len(rows) > a.sample:
        step = len(rows) / a.sample
        rows = [rows[int(i * step)] for i in range(a.sample)]
    print(f"{len(rows)} lignes ; percentiles safe={P_SAFE} propose={P_PROPOSE} fast={P_FAST}\n")

    cache = {}
    blocks = sorted({r["block_number"] for r in rows})
    print(f"feeHistory {len(blocks)} blocs…")
    for i, b in enumerate(blocks, 1):
        try: cache[b] = fee_history(RPC, b + FWD, COUNT, PCTS)
        except Exception: cache[b] = None
        if i % 200 == 0: print(f"  {i}/{len(blocks)}")

    def block_pcts(block, off):
        e = cache.get(block)
        if e is None: return None
        oldest, rew = e
        if rew.size == 0: return None
        idx = block + off - oldest
        return rew[idx] if 0 <= idx < rew.shape[0] else None

    stats = {"safe": [], "propose": [], "fast": []}
    capped = {"safe": 0, "fast": 0}
    for r in rows:
        prop_obs = int(r["propose_priority_fee"])
        safe_obs = int(r["safe_priority_fee"])
        fast_obs = int(r["fast_priority_fee"])
        # meilleur offset par ligne : minimise l'erreur COMBINÉE des 3 tiers
        # (teste que les 3 valeurs viennent du MÊME bloc)
        best = None
        for off in OFFSETS:
            pc = block_pcts(r["block_number"], off)
            if pc is None: continue
            tip_s, tip_p, tip_f = pc
            pred_prop = tip_p
            pred_safe = min(tip_s, pred_prop)
            pred_fast = min(tip_f, 5 * pred_prop)
            e = (abs(pred_prop - prop_obs) / max(prop_obs, GWEI * 0.01)
                 + abs(pred_safe - safe_obs) / max(safe_obs, GWEI * 0.01)
                 + abs(pred_fast - fast_obs) / max(fast_obs, GWEI * 0.01))
            if best is None or e < best[0]:
                best = (e, pred_safe, pred_prop, pred_fast)
        if best is None: continue
        _, ps, pp, pf = best
        stats["safe"].append(abs(ps - safe_obs) / max(safe_obs, GWEI * 0.01))
        stats["propose"].append(abs(pp - prop_obs) / max(prop_obs, GWEI * 0.01))
        stats["fast"].append(abs(pf - fast_obs) / max(fast_obs, GWEI * 0.01))
        if abs(safe_obs - prop_obs) <= 0.002 * prop_obs: capped["safe"] += 1
        if abs(fast_obs - 5 * prop_obs) <= 0.002 * fast_obs: capped["fast"] += 1

    print("\n=== Validation (offset choisi par ligne sur propose) ===")
    for t in ("safe", "propose", "fast"):
        e = np.array(stats[t])
        print(f"  {t:<8} err_med={np.median(e)*100:5.1f}%  "
              f"match<10%={np.mean(e<0.10)*100:3.0f}%  match<20%={np.mean(e<0.20)*100:3.0f}%  n={len(e)}")
    print(f"\n  cap safe=propose   : {capped['safe']}/{len(rows)} lignes")
    print(f"  cap fast=5×propose : {capped['fast']}/{len(rows)} lignes")


if __name__ == "__main__":
    main()
