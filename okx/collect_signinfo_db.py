#!/usr/bin/env python3
"""
collect_signinfo_db.py — Poll en boucle le VRAI endpoint privé OKX signInfo
(ETH mainnet, chainId=1, coinId=3), stocke chaque réponse dans une base SQLite
AVEC le block_number du nœud Ethereum au moment du poll (pour reverser ensuite
les priority fees via eth_feeHistory).

Auth générée localement (cf. okx/SIGNING_REVERSE.md) — aucun secret stocké.

Usage:
  python collect_signinfo_db.py                 # poll toutes les 12 s
  python collect_signinfo_db.py --interval 6
  python collect_signinfo_db.py --once
  python collect_signinfo_db.py --selftest      # vérifie la signature contre l'oracle
  python collect_signinfo_db.py --db /chemin.db
"""
import argparse, base64, hashlib, hmac, json, math, os, sqlite3, time, uuid
import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

OKX_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(OKX_DIR, "signinfo_live.db")
HOST = "wallet.okex.org"
PATH = "/priapi/v2/wallet/tx/signInfo"
EXPECT = {"slow": 1.125, "normal": 1.35, "fast": 1.70}

# IDs spécifiques à l'installation (capture DevTools — stables/installation)
DEV = {
    "devid": "8f6d9778-9531-403d-a3ba-6a0e6ecf6c10",
    "fp":    "8f6d9778-9531-403d-a3ba-6a0e6ecf6c10",
    "sess":  "2w5n96xp0e5_1781702422349",
    "xid":   "1781702421441-c-137",
    "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
}


def load_env(p):
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


load_env(os.path.join(OKX_DIR, ".env"))
RPC_URL = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")

# --------------------------------------------------------------------------
# Signatures (reverse complet)
# --------------------------------------------------------------------------
def user_device_sign(ts_ms: int) -> str:
    ua = DEV["ua"]
    sha1 = hashlib.sha1(ua.encode()).hexdigest()
    msg = f"{ua}^{sha1}^0^{ts_ms}^Linux x86_64^fr-FR".encode()
    pad = 16 - len(msg) % 16
    msg += bytes([pad]) * pad            # PKCS7
    enc = Cipher(algorithms.AES(b"H6379FIktyVeUAje"), modes.ECB()).encryptor()
    return base64.b64encode(enc.update(msg) + enc.finalize()).decode()


def ok_verify_sign(path: str, body: str, ts_ms: int, token: str) -> str:
    A = ts_ms // 1000
    b = hashlib.sha256(token.encode()).hexdigest()        # 64 hex
    P = math.floor((A / 600) % 32)
    k = math.floor((A / 3600) % 32)
    h = "".join(b[(P + (k + N) * N) % 32] for N in range(32))
    return base64.b64encode(
        hmac.new(h.encode(), (path + body).encode(), hashlib.sha256).digest()
    ).decode()


def selftest() -> bool:
    # Oracle de la capture réelle
    token = "2c15dff6-9812-49dd-82b7-c40f6da91beb"
    ts = 1781702682825
    body = '{"coinId":21100,"fromAddr":"0x4524fc0edb972fa09c8af5859025c795c81287fc","chainId":11155111}'
    sig = ok_verify_sign(PATH, body, ts, token)
    uds = user_device_sign(ts)
    exp_sig = "CRD7IoZEJBGkzRA3VSIjkINQYW+yy3eUXKEdXIYvQLg="
    exp_uds = ("spR4ZSSPsxqWTQVebOVhkWpQya3m858mNtwnQn4Kud6U/nX29sK8HpdfwP9NJUiBRsqHsouzsq0qedc2j0oskvJXDEUXDDEVW8"
               "uDx0O065lMVsYNjowQDjQO2USqYz+6ZRDGdx7TYnsDw4F2+VNpkcGvqoToi7xLevuXcIdPI2V0F4DWccytAmPrkVWfQPOAVE5hb"
               "/BQ+MQpdHAnxTpiol5jBkxtxkGtlEgJ8oJytPheCS/s5Deu/AkdV3wIZflj")
    ok1, ok2 = sig == exp_sig, uds == exp_uds
    print(f"ok-verify-sign  : {'OK' if ok1 else 'FAIL'}")
    print(f"user-device-sign: {'OK' if ok2 else 'FAIL'}")
    return ok1 and ok2


# --------------------------------------------------------------------------
# Réseau
# --------------------------------------------------------------------------
def get_block_number():
    try:
        r = requests.post(RPC_URL, json={"jsonrpc": "2.0", "method": "eth_blockNumber",
                                         "params": [], "id": 1}, timeout=5)
        return int(r.json()["result"], 16)
    except Exception:
        return None


def call_signinfo():
    ts = int(time.time() * 1000)
    token = str(uuid.uuid4())
    body = json.dumps({"coinId": 3, "fromAddr": "0x4524fc0edb972fa09c8af5859025c795c81287fc",
                       "chainId": 1}, separators=(",", ":"))
    headers = {
        "accept": "application/json", "content-type": "application/json",
        "app-type": "web", "platform": "plugin", "plugin-version": "4.4.0",
        "plugin-build-version": "publish",
        "devid": DEV["devid"], "device-token": DEV["devid"], "fingerprint-id": DEV["fp"],
        "tmx-session-id": DEV["sess"],
        "risk-params": f'fingerprint-id={DEV["fp"]}&fp-status=0&session-id={DEV["sess"]}',
        "x-id-group": DEV["xid"], "x-locale": "fr_FR", "x-utc": "2",
        "ok-timestamp": str(ts), "ok-verify-token": token,
        "ok-verify-sign": ok_verify_sign(PATH, body, ts, token),
        "user-device-sign": user_device_sign(ts),
        "user-agent": DEV["ua"],
        "origin": "chrome-extension://mcohilncbfahbmgdjkbpemcciiolgcge",
    }
    r = requests.post(f"https://{HOST}{PATH}?t={ts}", headers=headers, data=body, timeout=15)
    j = r.json()
    if j.get("code") != 0 or not j.get("data", {}).get("info", {}).get("gasPrice"):
        raise RuntimeError(f"code={j.get('code')} msg={j.get('msg')}")
    return ts // 1000, j


# --------------------------------------------------------------------------
# DB
# --------------------------------------------------------------------------
CREATE = """
CREATE TABLE IF NOT EXISTS signinfo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,          -- unix seconds
    block_number INTEGER,         -- head du nœud ETH au moment du poll (pour aligner eth_feeHistory)
    base_fee TEXT, suggest_base_fee TEXT,
    safe_priority_fee TEXT, propose_priority_fee TEXT, fast_priority_fee TEXT,
    min TEXT, normal TEXT, max TEXT,
    ori_min TEXT, ori_normal TEXT, ori_max TEXT,
    gas_limit INTEGER, reserve_fee_ratio TEXT,
    k_slow REAL, k_normal REAL, k_fast REAL,   -- multiplicateurs empiriques (tier-priority)/base
    stable INTEGER,                            -- 1 si k == 1.125/1.35/1.70
    raw_json TEXT
);
"""


def parse_row(ts, j, block):
    gp = j["data"]["info"]["gasPrice"]
    info = j["data"]["info"]
    base = int(gp["baseFee"]); safe = int(gp["safePriorityFee"])
    prop = int(gp["proposePriorityFee"]); fast = int(gp["fastPriorityFee"])
    ks = (int(gp["min"]) - safe) / base
    kn = (int(gp["normal"]) - prop) / base
    kf = (int(gp["max"]) - fast) / base
    near = lambda a, b: abs(a - b) < 1e-4
    stable = near(ks, EXPECT["slow"]) and near(kn, EXPECT["normal"]) and near(kf, EXPECT["fast"])
    return {
        "ts": ts, "block_number": block,
        "base_fee": gp["baseFee"], "suggest_base_fee": gp.get("suggestBaseFee"),
        "safe_priority_fee": gp["safePriorityFee"], "propose_priority_fee": gp["proposePriorityFee"],
        "fast_priority_fee": gp["fastPriorityFee"],
        "min": gp["min"], "normal": gp["normal"], "max": gp["max"],
        "ori_min": gp.get("oriMin"), "ori_normal": gp.get("oriNormal"), "ori_max": gp.get("oriMax"),
        "gas_limit": info.get("gasLimit"), "reserve_fee_ratio": info.get("reserveFeeRatio"),
        "k_slow": round(ks, 6), "k_normal": round(kn, 6), "k_fast": round(kf, 6),
        "stable": 1 if stable else 0, "raw_json": json.dumps(j),
    }


INSERT = """INSERT INTO signinfo
 (ts,block_number,base_fee,suggest_base_fee,safe_priority_fee,propose_priority_fee,fast_priority_fee,
  min,normal,max,ori_min,ori_normal,ori_max,gas_limit,reserve_fee_ratio,k_slow,k_normal,k_fast,stable,raw_json)
 VALUES (:ts,:block_number,:base_fee,:suggest_base_fee,:safe_priority_fee,:propose_priority_fee,:fast_priority_fee,
  :min,:normal,:max,:ori_min,:ori_normal,:ori_max,:gas_limit,:reserve_fee_ratio,:k_slow,:k_normal,:k_fast,:stable,:raw_json)"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--interval", type=float, default=12.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(0 if selftest() else 1)

    if not selftest():
        print("ABORT: la signature ne reproduit pas l'oracle."); raise SystemExit(1)

    con = sqlite3.connect(args.db); con.execute(CREATE); con.commit()
    g = lambda x: f"{int(x)/1e9:.3f}"
    print(f"signInfo mainnet -> {args.db} (interval {args.interval}s, RPC {RPC_URL}). Ctrl-C pour arrêter.")
    n = anomalies = 0
    while True:
        try:
            block = get_block_number()
            ts, j = call_signinfo()
            row = parse_row(ts, j, block)
            con.execute(INSERT, row); con.commit()
            n += 1
            if not row["stable"]:
                anomalies += 1
            flag = "ok" if row["stable"] else "⚠ DÉRIVE"
            print(f"[{time.strftime('%H:%M:%S')}] #{n} blk={block} base={g(row['base_fee'])}G "
                  f"k=[{row['k_slow']}/{row['k_normal']}/{row['k_fast']}] normal={g(row['normal'])}G {flag}"
                  + (f"  (anomalies: {anomalies})" if anomalies else ""))
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] warn: {e}")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
