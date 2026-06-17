#!/usr/bin/env python3
"""
gas_suggest.py — Récupère les suggestions de gas OKX pour Ethereum mainnet
(chemin WALLET v5, le plus proche de l'extension). Re-signe à chaque appel.

Usage:
  python gas_suggest.py                 # endpoint wallet v5 (défaut)
  python gas_suggest.py --dex           # endpoint DEX v6 (comparaison)
  python gas_suggest.py --chain 137     # autre réseau (Polygon, etc.)
  python gas_suggest.py --curl          # imprime un curl signé prêt à coller
"""
import argparse, base64, datetime, hmac, json, os, sys
import requests

OKX_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


load_env(os.path.join(OKX_DIR, ".env"))
API  = os.environ.get("OKX_API_KEY", "")
SEC  = os.environ.get("OKX_SECRET_KEY", "")
PASS = os.environ.get("OKX_PASSPHRASE", "")
PROJ = os.environ.get("OKX_PROJECT_ID", "")
HOST = "https://web3.okx.com"


def ts_now():
    n = datetime.datetime.now(datetime.timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


def sign(ts, method, path):
    pre = ts + method.upper() + path
    return base64.b64encode(
        hmac.new(SEC.encode(), pre.encode(), "sha256").digest()
    ).decode()


def headers(method, path):
    ts = ts_now()
    h = {
        "OK-ACCESS-KEY":        API,
        "OK-ACCESS-SIGN":       sign(ts, method, path),
        "OK-ACCESS-TIMESTAMP":  ts,
        "OK-ACCESS-PASSPHRASE": PASS,
        "Content-Type":         "application/json",
        "User-Agent":           "Mozilla/5.0",
    }
    if PROJ:
        h["OK-ACCESS-PROJECT"] = PROJ
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dex", action="store_true", help="endpoint DEX v6 au lieu de wallet v5")
    ap.add_argument("--chain", default="1", help="chainIndex (1=Ethereum, 137=Polygon...)")
    ap.add_argument("--curl", action="store_true", help="imprime un curl signé prêt à coller")
    ap.add_argument("--signinfo", action="store_true",
                    help="reconstruit la sortie signInfo (multiplicateurs client 1.125/1.35/1.70)")
    args = ap.parse_args()

    if args.dex:
        path = f"/api/v6/dex/pre-transaction/gas-price?chainIndex={args.chain}"
    else:
        path = f"/api/v5/wallet/pre-transaction/gas-price?chainIndex={args.chain}"

    h = headers("GET", path)

    if args.curl:
        parts = [f"curl -s '{HOST}{path}'"]
        for k, v in h.items():
            parts.append(f"  -H '{k}: {v}'")
        print(" \\\n".join(parts))
        print("\n# (signature valable quelques secondes — régénère via 'python gas_suggest.py --curl')",
              file=sys.stderr)
        return

    r = requests.get(f"{HOST}{path}", headers=h, timeout=15)
    print(f"GET {path}  ->  HTTP {r.status_code}")
    data = r.json()
    print(json.dumps(data, indent=2))

    # Petit résumé lisible si réponse OK
    if data.get("code") == "0" and data.get("data"):
        d = data["data"][0]
        eip = d.get("eip1559Protocol") or {}
        gwei = lambda x: f"{int(x)/1e9:.3f}" if x else "?"
        print("\n--- Résumé (Gwei) ---")
        print(f"  baseFee        = {gwei(eip.get('baseFee'))}")
        print(f"  suggestBaseFee = {gwei(eip.get('suggestBaseFee'))}  "
              f"(mult={int(eip.get('suggestBaseFee',0))/max(int(eip.get('baseFee',1)),1):.3f})")
        print(f"  safePriority   = {gwei(eip.get('safePriorityFee'))}    -> min (slow)    = {gwei(d.get('min'))}")
        print(f"  proposePriority= {gwei(eip.get('proposePriorityFee'))}    -> normal (avg)  = {gwei(d.get('normal'))}")
        print(f"  fastPriority   = {gwei(eip.get('fastPriorityFee'))}    -> max (fast)    = {gwei(d.get('max'))}")

        if args.signinfo:
            import math
            base = int(eip.get("baseFee", 0))
            safe = int(eip.get("safePriorityFee", 0))
            prop = int(eip.get("proposePriorityFee", 0))
            fast = int(eip.get("fastPriorityFee", 0))
            slow_mf = math.floor(base * 1.125) + safe
            avg_mf  = math.floor(base * 1.35)  + prop
            fast_mf = math.floor(base * 1.70)  + fast
            print("\n--- signInfo reconstruit (= ce que l'extension broadcasterait) ---")
            print(f"  slow   maxFeePerGas = floor(base*1.125)+safe    = {gwei(slow_mf)}")
            print(f"  normal maxFeePerGas = floor(base*1.35) +propose  = {gwei(avg_mf)}   (tier défaut)")
            print(f"  fast   maxFeePerGas = floor(base*1.70) +fast     = {gwei(fast_mf)}")
            print(f"  maxPriorityFeePerGas: slow={gwei(safe)} normal={gwei(prop)} fast={gwei(fast)}")


if __name__ == "__main__":
    main()
