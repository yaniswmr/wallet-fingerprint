#!/usr/bin/env python3
"""Replicate Alto's EXACT estimateDynamicGasPrice + bump algorithm and check it
against the live-captured `slow` tips.

Algorithm (from pimlicolabs/alto src/handlers/gasPriceManager.ts):
  feeHistory = eth_feeHistory(N, "latest", [40,50,60,70])
  avgFullness = mean(gasUsedRatio over N blocks)
  pidx = 3 if avgFullness>0.9 else 2 if >0.7 else 1 if >0.5 else 0
  maxPrio = integer-median over N blocks of reward[pidx]
  bumped  = maxPrio * gasPriceBump // 100        # bumpTheGasPrice
  slow    = bumped * 100 // 100                   # slow multiplier (=100)
"""
import json, requests

NODE = "https://app.functori.com/reth"
PCTS = [40, 50, 60, 70]
BUMP = 115            # hypothesis: hosted gasPriceBump
CANDIDATE_N = [4, 5, 6, 8, 10]
OFFSETS = [0, -1, 1]

def hx(v): return int(v, 16)

def int_median(vals):
    s = sorted(vals); m = len(s)//2
    return (s[m-1]+s[m])//2 if len(s) % 2 == 0 else s[m]

def main():
    recs = [json.loads(l) for l in open("slow_live.jsonl") if l.strip()]
    pts, last = [], None
    for r in recs:
        if r["slow"] != last:
            pts.append((r["block"], r["slow"])); last = r["slow"]
    newest = max(r["block"] for r in recs)
    oldest = min(b for b, _ in pts)
    count = newest - oldest + max(CANDIDATE_N) + 5
    fh = requests.post(NODE, json={"jsonrpc":"2.0","id":1,"method":"eth_feeHistory",
        "params":[hex(count), hex(newest), PCTS]}, timeout=30).json()["result"]
    ob = hx(fh["oldestBlock"])
    reward = {ob+i: [hx(x) for x in row] for i, row in enumerate(fh["reward"])}
    ratio  = {ob+i: fh["gasUsedRatio"][i] for i in range(len(fh["gasUsedRatio"]))}

    best = None
    for N in CANDIDATE_N:
        for off in OFFSETS:
            exact = 0; tot = 0; details = []
            for bn, slow in pts:
                end = bn + off
                blocks = [b for b in range(end-N+1, end+1) if b in reward and b in ratio]
                if len(blocks) < N:
                    continue
                tot += 1
                avgf = sum(ratio[b] for b in blocks) / len(blocks)
                pidx = 3 if avgf>0.9 else 2 if avgf>0.7 else 1 if avgf>0.5 else 0
                maxprio = int_median([reward[b][pidx] for b in blocks])
                pred = (maxprio * BUMP // 100) * 100 // 100
                ok = abs(pred - slow) <= 2
                exact += ok
                details.append((bn, slow, pred, PCTS[pidx], round(avgf,2), ok))
            if tot and (best is None or exact/tot > best[0]):
                best = (exact/tot, exact, tot, N, off, details)
    rate, exact, tot, N, off, details = best
    print(f"BEST: N={N} offset={off:+d} bump={BUMP}  exact={exact}/{tot} ({rate:.0%})\n")
    print(f"{'block':>10} {'slow':>11} {'pred':>11} {'pct':>4} {'full':>5}  ok")
    for bn, slow, pred, pct, full, ok in details:
        print(f"{bn:>10} {slow:>11} {pred:>11} {pct:>4} {full:>5}  {'YES' if ok else ''}")

if __name__ == "__main__":
    main()
