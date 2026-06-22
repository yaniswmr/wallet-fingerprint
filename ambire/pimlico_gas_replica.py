#!/usr/bin/env python3
"""
pimlico_gas_replica.py — Faithful replica of Pimlico Alto's
`pimlico_getUserOperationGasPrice` (mainnet / generic EIP-1559 chain) and a live
validator against the real API.

Source: github.com/pimlicolabs/alto
  - src/handlers/gasPriceManager.ts   (estimateGasPrice + bumpTheGasPrice)
  - src/rpc/methods/pimlico_getUserOperationGasPrice.ts  (tier multipliers)
  - src/utils/bigInt.ts               (scaleBigIntByPercent = value*pct//100, floor)

Exact integer pipeline (hosted-instance constants):
    maxFee_raw   = floor(baseFee * 1.20) + maxPrio_raw      # viem default base mult
    maxPrio_bump = floor(maxPrio_raw * 1.15)                # gasPriceBump = 115
    maxFee_bump  = floor(maxFee_raw  * 1.15)
    tier(v, m)   = floor(v * m / 100)   with m = 100/105/110 for slow/standard/fast

The ONLY input we cannot fetch is `maxPrio_raw` (Pimlico node's eth_maxPriorityFeePerGas
oracle). But the slow tier multiplier is 100, so the API's slow.maxPriorityFeePerGas
EQUALS maxPrio_bump exactly — we recover everything from that and validate at the wei.
"""

import argparse
import time

import requests

NODE_DEFAULT = "https://app.functori.com/reth"
PIM_DEFAULT = "https://api.pimlico.io/v2/1/rpc?apikey=pim_JPU8iy5BTbfGchPMJXQ1uP"

# Hosted-instance constants (confirmed at the wei, 2026-06-22)
VIEM_BASE_MULT = 120  # scaleBigIntByPercent(latestBaseFee, 120)
GAS_PRICE_BUMP = 115  # config.gasPriceBump
TIER_MULT = {"slow": 100, "standard": 105, "fast": 110}


def scale(value: int, percent: int) -> int:
    """scaleBigIntByPercent — integer (floor) division, exactly like Alto."""
    return value * percent // 100


# ---------------------------------------------------------------------------
# The replica
# ---------------------------------------------------------------------------
def replicate(max_prio_raw: int, base_fee: int) -> dict:
    """Reproduce the full 3-tier response from the two raw inputs."""
    max_fee_raw = scale(base_fee, VIEM_BASE_MULT) + max_prio_raw

    max_prio_bump = scale(max_prio_raw, GAS_PRICE_BUMP)
    max_fee_bump = scale(max_fee_raw, GAS_PRICE_BUMP)
    max_fee_bump = max(max_fee_bump, max_prio_bump)  # bumpTheGasPrice maxBigInt guard

    out = {}
    for tier, m in TIER_MULT.items():
        out[tier] = {
            "maxFeePerGas": scale(max_fee_bump, m),
            "maxPriorityFeePerGas": scale(max_prio_bump, m),
        }
    return out


def recover_max_prio_raw(slow_prio: int) -> list[int]:
    """Invert floor(maxPrio_raw * 115 / 100) == slow_prio → candidate raw values."""
    lo = (slow_prio * 100 + GAS_PRICE_BUMP - 1) // GAS_PRICE_BUMP  # ceil
    return [c for c in range(lo - 1, lo + 3) if scale(c, GAS_PRICE_BUMP) == slow_prio]


# ---------------------------------------------------------------------------
# RPC helpers
# ---------------------------------------------------------------------------
def rpc(url: str, method: str, params=None):
    r = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
        headers={"content-type": "application/json"},
        timeout=15,
    )
    return r.json().get("result")


def fetch_api(pim_url: str) -> dict:
    res = rpc(pim_url, "pimlico_getUserOperationGasPrice")
    return {
        t: {
            "maxFeePerGas": int(res[t]["maxFeePerGas"], 16),
            "maxPriorityFeePerGas": int(res[t]["maxPriorityFeePerGas"], 16),
        }
        for t in ("slow", "standard", "fast")
    }


def fetch_base_fee(node_url: str) -> int:
    blk = rpc(node_url, "eth_getBlockByNumber", ["latest", False])
    return int(blk["baseFeePerGas"], 16)


def fetch_recent_base_fees(node_url: str, count: int = 6) -> list[int]:
    """baseFee of the last `count` blocks (head first) — Pimlico's cached value
    is computed against one of these, so we test them all."""
    fh = rpc(node_url, "eth_feeHistory", [hex(count), "latest", []])
    # feeHistory.baseFeePerGas has count+1 entries (oldest..head, plus next-block projection)
    fees = [int(x, 16) for x in fh["baseFeePerGas"]]
    return list(reversed(fees))  # head/projection first


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_once(api: dict, base_fees: list[int]) -> dict:
    slow_prio = api["slow"]["maxPriorityFeePerGas"]
    slow_fee = api["slow"]["maxFeePerGas"]

    report = {"tier_exact": True, "tier_diffs": [], "basefee_match": False,
              "matched_offset": None, "implied_mult": None}

    # --- (A) tier multiplier check: fully deterministic, must be 0-diff ---
    # slow tier mult is 100 → slow_prio == maxPrio_bump, slow_fee == maxFee_bump.
    for tier, m in TIER_MULT.items():
        for field, base_val in (
            ("maxPriorityFeePerGas", slow_prio),
            ("maxFeePerGas", slow_fee),
        ):
            pred = scale(base_val, m)
            obs = api[tier][field]
            if pred != obs:
                report["tier_exact"] = False
                report["tier_diffs"].append(f"{tier}.{field}: pred={pred} obs={obs}")

    # --- (B) baseFee + bump check: test every recent baseFee (Pimlico cache lag) ---
    cands = recover_max_prio_raw(slow_prio)
    for off, bf in enumerate(base_fees):
        if any(replicate(c, bf)["slow"]["maxFeePerGas"] == slow_fee for c in cands):
            report["basefee_match"] = True
            report["matched_offset"] = off  # 0 = head/projection
            report["implied_mult"] = (slow_fee - slow_prio) / bf
            break
    if report["implied_mult"] is None:
        report["implied_mult"] = (slow_fee - slow_prio) / base_fees[0] if base_fees[0] else None
    return report


def print_full_comparison(sample_no: int, computed: dict, api: dict,
                          max_prio_raw: int, base_fee: int, blk_off, matched: bool):
    print(f"\n┌─ Échantillon #{sample_no} "
          f"{'─'*48}")
    print(f"│ Inputs bruts : maxPrio_raw = {max_prio_raw:>12} wei   "
          f"({max_prio_raw/1e9:.6f} Gwei)")
    print(f"│                baseFee     = {base_fee:>12} wei   "
          f"({base_fee/1e9:.6f} Gwei)  [bloc head{'' if blk_off in (0,None) else f'-{blk_off}'}]")
    print(f"│ Constantes  : baseFee×1.20 → +maxPrio → bump×1.15 → tiers ×100/105/110 (floor)")
    print(f"├─{'─'*66}")
    print(f"│ {'champ':<22} {'CALCULÉ (réplica)':>18} {'SERVEUR (API)':>18}  ok")
    print(f"├─{'─'*66}")
    short = {"maxPriorityFeePerGas": "maxPriorityFee", "maxFeePerGas": "maxFeePerGas"}
    all_ok = True
    for tier in ("slow", "standard", "fast"):
        for field in ("maxPriorityFeePerGas", "maxFeePerGas"):
            c = computed[tier][field]
            a = api[tier][field]
            ok = (c == a)
            all_ok &= ok
            label = f"{tier}.{short[field]}"
            print(f"│ {label:<22} {c:>18} {a:>18}  {'✓' if ok else '✗ Δ='+str(c-a)}")
    print(f"└─{'─'*66}")
    verdict = "EXACT (tous les champs identiques au wei)" if all_ok else "DIVERGENCE"
    note = "" if matched else "  ⚠ baseFee non aligné (cache Pimlico sur un autre bloc) → maxFee décalé"
    print(f"  → {verdict}{note}")
    return all_ok


def run_validation(pim_url: str, node_url: str, n: int, interval: float):
    print(f"API  : {pim_url}")
    print(f"Node : {node_url}")
    print(f"Const: baseMult={VIEM_BASE_MULT/100}  bump={GAS_PRICE_BUMP/100}  tiers={TIER_MULT}")

    full_ok = 0
    for i in range(1, n + 1):
        base_fees = fetch_recent_base_fees(node_url)
        api = fetch_api(pim_url)
        rep = validate_once(api, base_fees)

        # Reconstruct the exact raw inputs that reproduce the API response.
        slow_prio = api["slow"]["maxPriorityFeePerGas"]
        max_prio_raw = recover_max_prio_raw(slow_prio)[0]
        off = rep["matched_offset"]
        base_fee = base_fees[off] if off is not None else base_fees[0]

        computed = replicate(max_prio_raw, base_fee)
        if print_full_comparison(i, computed, api, max_prio_raw, base_fee, off, rep["basefee_match"]):
            full_ok += 1
        if i < n:
            time.sleep(interval)

    print(f"\n{'='*70}")
    print(f"RÉSULTAT : {full_ok}/{n} échantillons reproduits EXACTEMENT sur les 6 champs")
    print(f"           (les ratés = baseFee du nœud non aligné avec celui de Pimlico,")
    print(f"            seul maxFee est touché ; les 6 multiplicateurs de tiers restent exacts)")


# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Pimlico Alto gas-price replica + live validator")
    p.add_argument("--pim", default=PIM_DEFAULT, help="Pimlico RPC URL")
    p.add_argument("--node", default=NODE_DEFAULT, help="Ethereum node URL (baseFee)")
    p.add_argument("--n", type=int, default=10, help="number of live samples")
    p.add_argument("--interval", type=float, default=8.0, help="seconds between samples")
    p.add_argument("--demo", action="store_true", help="run replicate() on a fixed example and exit")
    args = p.parse_args()

    if args.demo:
        base_fee, max_prio_raw = 166_049_142, 100_000_000
        out = replicate(max_prio_raw, base_fee)
        print(f"replicate(maxPrio_raw={max_prio_raw}, baseFee={base_fee}):")
        for t, v in out.items():
            print(f"  {t:>8}: maxFee={v['maxFeePerGas']:>12}  maxPrio={v['maxPriorityFeePerGas']:>12}")
        return

    run_validation(args.pim, args.node, args.n, args.interval)


if __name__ == "__main__":
    main()
