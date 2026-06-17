#!/usr/bin/env python3
"""
collect_signinfo_recon.py — Collecte mainnet des valeurs gas OKX *reconstruites*
(équivalent signInfo) sans toucher à l'endpoint privé.

Principe (prouvé via une réponse signInfo réelle) :
  signInfo = API publique gas-price + multiplicateurs client appliqués par l'extension :
    slow   maxFeePerGas = floor(baseFee * 1.125) + safePriorityFee
    normal maxFeePerGas = floor(baseFee * 1.35 ) + proposePriorityFee   (tier défaut)
    fast   maxFeePerGas = floor(baseFee * 1.70 ) + fastPriorityFee

L'API gas-price wallet v5 (web3.okx.com) est pollable librement avec les clés OK-ACCESS
du .env — aucune signature interne requise.

Usage:
  python collect_signinfo_recon.py                 # poll chainIndex=1 toutes les 12 s
  python collect_signinfo_recon.py --interval 6
  python collect_signinfo_recon.py --once          # un seul poll (debug)
"""
import argparse, base64, datetime, hmac, json, math, os, sqlite3, time
import requests

OKX_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "https://web3.okx.com"
DEFAULT_DB = os.path.join(OKX_DIR, "signinfo_recon.db")
MULT = {"slow": 1.125, "normal": 1.35, "fast": 1.70}


def load_env(path):
    if os.path.exists(path):
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


def ts_now():
    n = datetime.datetime.now(datetime.timezone.utc)
    return n.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n.microsecond // 1000:03d}Z"


def headers(path):
    ts = ts_now()
    pre = ts + "GET" + path
    sign = base64.b64encode(hmac.new(SEC.encode(), pre.encode(), "sha256").digest()).decode()
    h = {"OK-ACCESS-KEY": API, "OK-ACCESS-SIGN": sign, "OK-ACCESS-TIMESTAMP": ts,
         "OK-ACCESS-PASSPHRASE": PASS, "Content-Type": "application/json",
         "User-Agent": "Mozilla/5.0"}
    if PROJ:
        h["OK-ACCESS-PROJECT"] = PROJ
    return h


CREATE = """
CREATE TABLE IF NOT EXISTS signinfo_recon (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    base_fee TEXT, safe_prio TEXT, propose_prio TEXT, fast_prio TEXT,
    slow_maxfee TEXT, normal_maxfee TEXT, fast_maxfee TEXT,
    raw_json TEXT
);
"""


def poll(chain):
    path = f"/api/v5/wallet/pre-transaction/gas-price?chainIndex={chain}"
    r = requests.get(HOST + path, headers=headers(path), timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != "0":
        raise RuntimeError(f"API code={data.get('code')} msg={data.get('msg')}")
    eip = data["data"][0]["eip1559Protocol"]
    base = int(eip["baseFee"]); safe = int(eip["safePriorityFee"])
    prop = int(eip["proposePriorityFee"]); fast = int(eip["fastPriorityFee"])
    return {
        "base": base, "safe": safe, "prop": prop, "fast": fast,
        "slow_mf":   math.floor(base * MULT["slow"])   + safe,
        "normal_mf": math.floor(base * MULT["normal"]) + prop,
        "fast_mf":   math.floor(base * MULT["fast"])   + fast,
        "raw": json.dumps(data),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--chain", default="1")
    ap.add_argument("--interval", type=float, default=12.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db); con.execute(CREATE); con.commit()
    g = lambda x: f"{x/1e9:.3f}"
    print(f"Collecte -> {args.db} (chainIndex={args.chain}, {args.interval}s). Ctrl-C pour arrêter.")
    while True:
        try:
            d = poll(args.chain)
            con.execute(
                "INSERT INTO signinfo_recon (ts,base_fee,safe_prio,propose_prio,fast_prio,"
                "slow_maxfee,normal_maxfee,fast_maxfee,raw_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (int(time.time()), str(d["base"]), str(d["safe"]), str(d["prop"]), str(d["fast"]),
                 str(d["slow_mf"]), str(d["normal_mf"]), str(d["fast_mf"]), d["raw"]))
            con.commit()
            print(f"[{datetime.datetime.now():%H:%M:%S}] base={g(d['base'])}  "
                  f"normal_maxFee={g(d['normal_mf'])} (prio {g(d['prop'])})  "
                  f"slow={g(d['slow_mf'])} fast={g(d['fast_mf'])}")
        except Exception as e:
            print(f"  [warn] {e}")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
