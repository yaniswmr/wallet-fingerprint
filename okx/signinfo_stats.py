#!/usr/bin/env python3
"""Résumé du log signinfo_live.jsonl : stabilité des multiplicateurs OKX dans le temps."""
import json, os, sys
from collections import Counter

p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "signinfo_live.jsonl")
rows = [json.loads(l) for l in open(p) if l.strip()]
if not rows:
    print("log vide"); raise SystemExit

n = len(rows)
stable = sum(1 for r in rows if r.get("stable"))
print(f"polls: {n}  |  stables (1.125/1.35/1.70): {stable} ({100*stable/n:.1f}%)  |  anomalies: {n-stable}")
for tier in ("k_slow", "k_normal", "k_fast"):
    c = Counter(r[tier] for r in rows)
    top = ", ".join(f"{k}×{v}" for k, v in c.most_common(5))
    print(f"  {tier:9s}: {top}")

bad = [r for r in rows if not r.get("stable")]
if bad:
    print("\n-- anomalies (ts, k_slow/k_normal/k_fast, baseFee Gwei) --")
    for r in bad[:20]:
        print(f"  {r['ts']}  {r['k_slow']}/{r['k_normal']}/{r['k_fast']}  base={int(r['base_fee'])/1e9:.3f}")
