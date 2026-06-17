#!/usr/bin/env python3
"""
decode_signinfo.py — Décode une réponse OKX `priapi/v2/wallet/tx/signInfo`
et vérifie la signature gas d'OKX :

  slow   (min)    = floor(baseFee * 1.125) + safePriorityFee
  normal (défaut) = floor(baseFee * 1.35 ) + proposePriorityFee
  fast   (max)    = floor(baseFee * 1.70 ) + fastPriorityFee

  ori* = baseFee + priority  (valeurs oracle brutes, sans inflation)

Usage:
  python decode_signinfo.py response.json
  cat response.json | python decode_signinfo.py
"""
import json, math, sys

K = {"slow": 1.125, "normal": 1.35, "fast": 1.70}


def load():
    if len(sys.argv) > 1:
        return json.load(open(sys.argv[1]))
    return json.load(sys.stdin)


def main():
    doc = load()
    gp = doc["data"]["info"]["gasPrice"]
    base    = int(gp["baseFee"])
    safe    = int(gp["safePriorityFee"])
    propose = int(gp["proposePriorityFee"])
    fast    = int(gp["fastPriorityFee"])
    g = lambda x: f"{int(x)/1e9:.4f} Gwei"

    print(f"baseFee        = {g(base)}")
    print(f"suggestBaseFee = {g(gp.get('suggestBaseFee', 0))}  "
          f"(= floor(base*1.125) ? {math.floor(base*1.125)==int(gp.get('suggestBaseFee',0))})")
    print()

    tiers = [
        ("slow / min",      "min",    safe,    K["slow"]),
        ("normal / default","normal", propose, K["normal"]),
        ("fast / max",      "max",    fast,    K["fast"]),
    ]
    all_ok = True
    for label, field, prio, k in tiers:
        observed = int(gp.get(field, -1))
        predicted = math.floor(base * k) + prio
        ok = observed == predicted
        all_ok &= ok
        print(f"{label:18s}: floor(base*{k}) + prio = {predicted}  "
              f"vs {field}={observed}  {'OK' if ok else 'MISMATCH'}")
        # ratio empirique si mismatch (utile si OKX change les coefficients)
        if not ok and base:
            print(f"    -> mult empirique = {(observed - prio)/base:.5f}")

    print()
    print("Signature OKX vérifiée ✔" if all_ok else "⚠ coefficients différents — voir mult empiriques ci-dessus")


if __name__ == "__main__":
    main()
