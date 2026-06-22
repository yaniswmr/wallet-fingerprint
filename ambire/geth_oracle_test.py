#!/usr/bin/env python3
"""
geth_oracle_test.py — Teste si tipUnit T = oracle geth SuggestGasTipCap.

Algo geth (eth/gasprice/gasprice.go) :
  - pour les `checkBlocks` derniers blocs, récupérer les txs, calculer le tip effectif,
    ignorer tip < ignorePrice (2 wei), trier, garder les `limit` (3) plus bas par bloc
  - concaténer tous les échantillons, trier, prendre le `percentile` (60e)
  - balayer plus de blocs si pas assez d'échantillons (max maxBlocks)
On teste plusieurs (checkBlocks, limit, percentile).
"""
import argparse, os, sqlite3, time
import requests, numpy as np

DEFAULT_DB  = os.path.join(os.path.dirname(__file__), "gas_fees_collected.db")
DEFAULT_RPC = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
IGNORE = 2

def rpc(url,m,p,timeout=30):
    return requests.post(url,json={"jsonrpc":"2.0","method":m,"params":p,"id":1},timeout=timeout).json()["result"]

_blk={}
def block_tips(url, n):
    """tips effectifs triés ascendants pour le bloc n."""
    if n in _blk: return _blk[n]
    b=rpc(url,"eth_getBlockByNumber",[hex(n),True])
    if not b: _blk[n]=[]; return []
    base=int(b["baseFeePerGas"],16)
    tips=[]
    for tx in b["transactions"]:
        if isinstance(tx,str): continue
        if tx.get("maxPriorityFeePerGas") is not None and tx.get("maxFeePerGas") is not None:
            mp=int(tx["maxPriorityFeePerGas"],16); mf=int(tx["maxFeePerGas"],16)
            tip=min(mp, mf-base)
        elif tx.get("gasPrice") is not None:
            tip=int(tx["gasPrice"],16)-base
        else: continue
        if tip>=IGNORE: tips.append(tip)
    tips.sort()
    _blk[n]=tips
    return tips

def geth_suggest(url, head, check_blocks, limit, percentile):
    samples=[]
    n=head
    blocks_used=0
    while blocks_used<check_blocks and n>0:
        tips=block_tips(url,n)
        if tips:
            samples.extend(tips[:limit])
        n-=1; blocks_used+=1
    if not samples: return 0
    samples.sort()
    idx=(len(samples)-1)*percentile//100
    return samples[idx]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--db",default=DEFAULT_DB)
    ap.add_argument("--rpc",default=DEFAULT_RPC)
    ap.add_argument("--samples",type=int,default=20)
    ap.add_argument("--sleep",type=float,default=0.05)
    args=ap.parse_args()
    con=sqlite3.connect(args.db)
    rows=con.execute("""SELECT block_number,prio_medium,prio_fast,prio_ape,ts,updated_ms
        FROM gas_fees WHERE block_number IS NOT NULL AND prio_medium IS NOT NULL
        AND prio_medium NOT LIKE '%.%' AND CAST(prio_medium AS INTEGER)>0
        GROUP BY updated_ms ORDER BY id""").fetchall()
    idx=np.linspace(0,len(rows)-1,args.samples).astype(int)
    rows=[rows[i] for i in idx]
    print(f"{len(rows)} computations\n")

    configs=[(cb,lim,pc) for cb in [2,3,5,10,20] for lim in [1,2,3] for pc in [50,60,70]]
    cand={c:[] for c in configs}
    for i,(blk,m,f,a,ts,upd) in enumerate(rows,1):
        m,f,a=int(m),int(f),int(a); t=(1*m+2*f+3*a)/14.0
        delta=max(0,round((ts*1000-upd)/12000)) if upd else 0
        head=max(1,blk-delta)
        try:
            for c in configs:
                cb,lim,pc=c
                cand[c].append((geth_suggest(args.rpc,head,cb,lim,pc),t))
            time.sleep(args.sleep)
        except Exception as e:
            print("skip",head,e)
        if i%5==0: print(f"  {i}/{len(rows)} (cache {len(_blk)} blocks)")

    print("\n"+"="*72)
    print(f"{'cfg(blk,lim,pct)':18} | {'ratio_med':>9} {'cv%':>7} {'MAPE%':>9} {'<15%':>6}")
    print("-"*72)
    res=[]
    for c,pairs in cand.items():
        pr=np.array([p for p,_ in pairs],float); ac=np.array([a for _,a in pairs],float)
        nz=pr>0
        if nz.sum()<len(pairs)//2: continue
        ratio=ac[nz]/pr[nz]
        mape=np.mean(np.abs(pr-ac)/np.maximum(ac,1))*100
        within=100*np.mean(np.abs(pr-ac)/np.maximum(ac,1)<0.15)
        res.append((c,np.median(ratio),100*np.std(ratio)/max(np.mean(ratio),1e-9),mape,within))
    res.sort(key=lambda x:x[2])
    for c,rmed,rcv,mape,within in res[:20]:
        print(f"{str(c):18} | {rmed:>9.3f} {rcv:>7.1f} {mape:>9.1f} {within:>5.0f}%")

if __name__=="__main__":
    try: main()
    except KeyboardInterrupt: print("\nInterrompu.")
