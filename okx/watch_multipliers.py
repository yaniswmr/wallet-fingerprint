#!/usr/bin/env python3
"""
watch_multipliers.py — Interroge le serveur gas d'OKX et calcule en direct les
facteurs de chaque tier (safe / propose / fast).

Contrairement à Rabby, l'API OKX renvoie SA PROPRE base fee dans la réponse :
    eip1559Protocol = {baseFee, suggestBaseFee, safePriorityFee,
                       proposePriorityFee, fastPriorityFee}
Donc AUCUNE course temporelle, AUCUN latch, et pas besoin du RPC : tout vient du
même appel.

Facteurs calculés :
  - base_mult        = suggestBaseFee / baseFee        (≈ 1.0 : OKX ne gonfle PAS la base)
  - fee_factor_tier  = (suggestBaseFee + priority_tier) / baseFee   (le "fee factor" on-chain)
Les fee_factors varient car la priority est dérivée du marché (pas un multiplicateur fixe).
Identités legacy vérifiées : min = base+safe, normal = base+propose, max = base+fast.

Auth : lit OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE / OKX_PROJECT_ID depuis okx/.env.

Usage :
  python watch_multipliers.py
  python watch_multipliers.py --interval 4
"""

import argparse
import base64
import datetime
import hmac
import json
import os
import time

import requests

GWEI = 1e9
GAS_URL  = "https://www.okx.com/api/v6/dex/pre-transaction/gas-price"
CHAIN_ID = "1"
HERE     = os.path.dirname(__file__)
ENV_FILE = os.path.join(HERE, ".env")
OUT_FILE = os.path.join(HERE, "multipliers.jsonl")

TIERS = (("safe", "safePriorityFee"), ("propose", "proposePriorityFee"), ("fast", "fastPriorityFee"))


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
API_KEY    = os.environ.get("OKX_API_KEY", "")
SECRET_KEY = os.environ.get("OKX_SECRET_KEY", "")
PASSPHRASE = os.environ.get("OKX_PASSPHRASE", "")
PROJECT_ID = os.environ.get("OKX_PROJECT_ID", "")


def _now_ts():
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _headers(path):
    ts = _now_ts()
    pre = ts + "GET" + path
    sign = base64.b64encode(
        hmac.new(SECRET_KEY.encode(), pre.encode(), digestmod="sha256").digest()
    ).decode()
    h = {
        "OK-ACCESS-KEY":        API_KEY,
        "OK-ACCESS-SIGN":       sign,
        "OK-ACCESS-TIMESTAMP":  ts,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "User-Agent":           "Mozilla/5.0",
    }
    if PROJECT_ID:
        h["OK-ACCESS-PROJECT"] = PROJECT_ID
    return h


def fetch_gas():
    """Retourne le dict eip1559Protocol (valeurs en wei, str)."""
    path = f"/api/v6/dex/pre-transaction/gas-price?chainIndex={CHAIN_ID}"
    r = requests.get(GAS_URL, params={"chainIndex": CHAIN_ID},
                     headers=_headers(path), timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise RuntimeError(f"API code={data.get('code')} msg={data.get('msg')}")
    d = data["data"][0]
    return d.get("eip1559Protocol") or d.get("eip1599Protocol") or {}


def main():
    p = argparse.ArgumentParser(description="Watch live des facteurs gas OKX")
    p.add_argument("--interval", type=float, default=4.0, help="intervalle en s (def 4)")
    p.add_argument("--out", default=OUT_FILE, help=f"fichier jsonl (def {OUT_FILE})")
    args = p.parse_args()

    if not all([API_KEY, SECRET_KEY, PASSPHRASE]):
        raise SystemExit("[ERROR] credentials manquants dans okx/.env "
                         "(OKX_API_KEY / OKX_SECRET_KEY / OKX_PASSPHRASE)")

    print(f"Poll OKX toutes les {args.interval}s — écriture dans {args.out}")
    print("Ctrl+C pour arrêter\n")
    print(f"  {'time':<19}  {'baseFee':>9}  {'base_mult':>9}  |  "
          f"{'ff_safe':>7} {'ff_prop':>7} {'ff_fast':>7}  |  "
          f"{'pf_safe':>7} {'pf_prop':>7} {'pf_fast':>7}  {'fast/prop':>9}")
    print("  " + "-" * 104)

    while True:
        try:
            eip  = fetch_gas()
            base = int(eip["baseFee"])
            sug  = int(eip["suggestBaseFee"])
            prio = {name: int(eip.get(key) or 0) for name, key in TIERS}

            base_mult = sug / base if base else float("nan")
            # fee factor on-chain = maxFee_tier / baseFee = (suggest + priority) / base
            ff = {name: (sug + prio[name]) / base if base else float("nan") for name, _ in TIERS}
            fast_over_prop = prio["fast"] / prio["propose"] if prio["propose"] else float("nan")

            ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
            entry = {
                "ts": ts,
                "base_fee_gwei": round(base / GWEI, 6),
                "base_mult": round(base_mult, 5),
                "ff_safe":   round(ff["safe"], 4),
                "ff_propose": round(ff["propose"], 4),
                "ff_fast":   round(ff["fast"], 4),
                "pf_safe":    round(prio["safe"] / GWEI, 6),
                "pf_propose": round(prio["propose"] / GWEI, 6),
                "pf_fast":    round(prio["fast"] / GWEI, 6),
                "fast_over_propose": round(fast_over_prop, 3),
            }
            with open(args.out, "a") as f:
                f.write(json.dumps(entry) + "\n")

            print(f"  {ts[:19]:<19}  {base/GWEI:>8.4f}G  {base_mult:>9.5f}  |  "
                  f"{ff['safe']:>7.4f} {ff['propose']:>7.4f} {ff['fast']:>7.4f}  |  "
                  f"{prio['safe']/GWEI:>6.3f}G {prio['propose']/GWEI:>6.3f}G "
                  f"{prio['fast']/GWEI:>6.3f}G  {fast_over_prop:>9.2f}",
                  flush=True)

        except Exception as e:
            print(f"  [ERROR] {e}", flush=True)

        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nArrêté.")
