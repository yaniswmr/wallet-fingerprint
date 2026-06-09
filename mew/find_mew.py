#!/usr/bin/env python3
"""
find_mew.py — Scan the blockchain for MyEtherWallet (MEW) transactions.

Detection signal (EIP-1559 transactions only):

  fee_factor = (maxFeePerGas - maxPriorityFeePerGas) / baseFeePerGas

  MEW applies these multipliers (gasPriceHelper.js):

    economy : baseFee × 1.25  +  priorityFee × 0.80  →  fee_factor ≈ 1.25
    regular : baseFee × 1.50  +  priorityFee × 1.00  →  fee_factor ≈ 1.50
    fast    : baseFee × 1.75  +  priorityFee × 1.25  →  fee_factor ≈ 1.75

  where:
    baseFee     = block.baseFeePerGas  (subscribed via newBlockHeaders)
    priorityFee = eth_getGasPrice() × gasPriceMultiplier - baseFee
                  (gasPriceMultiplier = 1.0 on ETH mainnet)
    min(maxPriorityFeePerGas) = 1.25 gwei  (getMinPriorityFee)

  Source: github.com/MyEtherWallet/MyEtherWallet
    src/core/helpers/gasPriceHelper.js  (getBaseFeeBasedOnType, getPriorityFeeBasedOnType)
    src/core/store/global/actions.js    (updateGasPrice)

Display:
  ✅ GREEN — fee_factor ∈ {1.25, 1.50, 1.75} AND priority ≥ 1.25 gwei → saved to DB
  ~  BLUE  — fee_factor matches but priority < 1.25 gwei (rare, edge case)

Usage:
  python find_mew.py --rpc <ETH_RPC_URL>
  python find_mew.py --rpc <ETH_RPC_URL> --start 22000000 --end 22000500 --db mew.db
"""

import argparse
import os
import sqlite3
import sys
import time

from web3 import Web3

# ── ANSI colors ───────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"

# ── MEW Tier Definitions ──────────────────────────────────────────────────────
#
# (baseFee_multiplier, priority_multiplier)
# Source: gasPriceHelper.js — getBaseFeeBasedOnType / getPriorityFeeBasedOnType

MEW_TIERS: dict[str, tuple[float, float]] = {
    "economy": (1.25, 0.80),
    "regular": (1.50, 1.00),
    "fast":    (1.75, 1.25),
}

FACTOR_TOLERANCE    = 0.001   # tight: fee_factor is exact (BN integer math on baseFee)
MIN_PRIORITY_GWEI   = 1.25    # getMinPriorityFee() = toWei('1.25', 'gwei')

# ── DB ────────────────────────────────────────────────────────────────────────

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS transactions (
    hash              TEXT PRIMARY KEY,
    block             INTEGER NOT NULL,
    from_addr         TEXT,
    tier              TEXT,
    tx_type           INTEGER,
    max_fee_gwei      REAL,
    max_priority_gwei REAL,
    base_fee_gwei     REAL,
    fee_factor        REAL,
    raw_priority_gwei REAL,
    gas_limit         INTEGER,
    estimated_gas     INTEGER,
    gas_limit_factor  REAL
)
"""

INSERT_TX = """
INSERT OR IGNORE INTO transactions
    (hash, block, from_addr, tier, tx_type,
     max_fee_gwei, max_priority_gwei, base_fee_gwei, fee_factor,
     raw_priority_gwei,
     gas_limit, estimated_gas, gas_limit_factor)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TABLE)
    conn.commit()
    return conn


# ── Tier detection ────────────────────────────────────────────────────────────

def _detect_tier(fee_factor: float, max_priority_gwei: float) -> tuple[str | None, float | None]:
    """
    Return (tier, raw_priority_gwei) if fee_factor matches a MEW tier, else (None, None).

    raw_priority_gwei = maxPriorityFeePerGas / priority_multiplier
    This is what MEW's eth_getGasPrice() - baseFee would have returned.
    """
    for tier, (bf_mult, prio_mult) in MEW_TIERS.items():
        if abs(fee_factor - bf_mult) <= FACTOR_TOLERANCE:
            raw = max_priority_gwei / prio_mult if prio_mult else None
            return tier, raw
    return None, None


def _is_confirmed(max_priority_gwei: float) -> bool:
    """MEW enforces min priority fee of 1.25 gwei via getMinPriorityFee()."""
    return max_priority_gwei >= MIN_PRIORITY_GWEI - 0.001


# ── Gas limit estimation ──────────────────────────────────────────────────────

def _estimate_gas_limit_factor(
    w3: Web3, tx, block_number: int
) -> tuple[int, int | None, float | None]:
    gas_limit = tx["gas"]
    call = {
        "from":  tx["from"],
        "to":    tx.get("to"),
        "value": tx["value"],
        "data":  tx.get("input", b""),
    }
    try:
        estimated = w3.eth.estimate_gas(call, block_number - 1)
        factor = gas_limit / estimated if estimated else None
        return gas_limit, estimated, factor
    except Exception:
        return gas_limit, None, None


# ── Block analysis ────────────────────────────────────────────────────────────

def analyze_block(block, w3: Web3) -> list[dict]:
    base_fee = block.get("baseFeePerGas")
    if base_fee is None or base_fee == 0:
        return []

    base_fee_gwei = base_fee / 1e9
    block_number  = block["number"]
    matches: list[dict] = []

    for tx in block["transactions"]:
        if tx.get("maxFeePerGas") is None or tx.get("maxPriorityFeePerGas") is None:
            continue

        max_fee_gwei      = tx["maxFeePerGas"]         / 1e9
        max_priority_gwei = tx["maxPriorityFeePerGas"] / 1e9
        # fee_factor = adjustedBaseFee / baseFee, exact for MEW (no rounding on baseFee)
        fee_factor        = (max_fee_gwei - max_priority_gwei) / base_fee_gwei

        tier, raw_priority_gwei = _detect_tier(fee_factor, max_priority_gwei)
        if tier is None:
            continue

        confirmed = _is_confirmed(max_priority_gwei)

        gas_limit, estimated_gas, gas_limit_factor = _estimate_gas_limit_factor(
            w3, tx, block_number
        )

        matches.append({
            "hash":             tx["hash"].hex(),
            "from":             tx["from"],
            "to":               tx.get("to") or "contract-creation",
            "tx_type":          tx.get("type"),
            "maxFee":           round(max_fee_gwei, 6),
            "maxPriority":      round(max_priority_gwei, 6),
            "baseFee":          round(base_fee_gwei, 6),
            "fee_factor":       round(fee_factor, 6),
            "tier":             tier,
            "raw_priority":     round(raw_priority_gwei, 6) if raw_priority_gwei is not None else None,
            "confirmed":        confirmed,
            "gas_limit":        gas_limit,
            "estimated_gas":    estimated_gas,
            "gas_limit_factor": round(gas_limit_factor, 6) if gas_limit_factor is not None else None,
        })

    return matches


# ── Display ───────────────────────────────────────────────────────────────────

_TIER_TARGETS = {t: bf for t, (bf, _) in MEW_TIERS.items()}


def print_match(block_num: int, m: dict) -> None:
    confirmed = m["confirmed"]
    tier      = m["tier"]
    target    = _TIER_TARGETS[tier]
    ff        = m["fee_factor"]

    if confirmed:
        header = (
            f"{GREEN}✅ MEW CONFIRMED{RESET}  "
            f"[tier={GREEN}{tier}{RESET}, factor={GREEN}{ff:.6f}{RESET}]"
        )
    else:
        header = f"{BLUE}[GAS]  MEW pattern  [tier={tier}, factor≈{ff:.6f}]{RESET}"

    ff_str   = f"{GREEN}{ff:.6f}{RESET}" if abs(ff - target) <= FACTOR_TOLERANCE else f"{ff:.6f}"
    prio_str = f"{GREEN}{m['maxPriority']:.6f}{RESET}" if confirmed else f"{YELLOW}{m['maxPriority']:.6f}{RESET}"

    print(f"\n[block {block_num}] {header}")
    print(f"  tx               : {m['hash']}")
    print(f"  from             : {m['from']}")
    print(f"  to               : {m['to']}")
    print(f"  maxFeePerGas     = {m['maxFee']:.6f} Gwei")
    print(f"  maxPriority      = {prio_str} Gwei  (min MEW: {MIN_PRIORITY_GWEI} Gwei)")
    print(f"  baseFee          = {m['baseFee']:.6f} Gwei")
    print(f"  fee_factor       = {ff_str}  (target {target})")
    if m["raw_priority"] is not None:
        prio_mult = MEW_TIERS[tier][1]
        print(
            f"  raw priorityFee  = {m['raw_priority']:.6f} Gwei"
            f"  (= maxPriority / {prio_mult}  ← inferred eth_getGasPrice() − baseFee)"
        )

    print(f"  gasLimit         = {m['gas_limit']}")
    if m["estimated_gas"] is not None:
        glf = m["gas_limit_factor"]
        # MEW: no buffer on gasLimit → expect factor ≈ 1.0
        glf_col = GREEN if abs(glf - 1.0) <= 0.05 else YELLOW
        print(
            f"  estimatedGas     = {m['estimated_gas']}  (simulated at block {block_num - 1})\n"
            f"  gasLimit factor  = {glf_col}{glf:.6f}{RESET}"
            f"  ({m['gas_limit']} / {m['estimated_gas']})"
            f"{'  ← no buffer (MEW)' if abs(glf - 1.0) <= 0.05 else ''}"
        )
    else:
        print(f"  estimatedGas     = N/A  (simulation failed)")


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Scan blockchain for MyEtherWallet (MEW) transactions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--rpc",
        default=os.environ.get("ETH_RPC_URL"),
        required=not os.environ.get("ETH_RPC_URL"),
        help="Ethereum JSON-RPC endpoint",
    )
    p.add_argument("--start", type=int, default=None, help="Premier bloc (default: latest)")
    p.add_argument("--end",   type=int, default=None, help="Dernier bloc (default: scan infini)")
    p.add_argument("--db",    default="mew.db",       help="SQLite database file (default: mew.db)")
    p.add_argument("--delay", type=float, default=0.0, help="Secondes entre chaque bloc (default: 0)")
    args = p.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        sys.exit("ERROR: Cannot connect to Ethereum node. Check --rpc.")

    conn = init_db(args.db)

    latest  = w3.eth.block_number
    start   = args.start if args.start is not None else latest
    end     = args.end   if args.end   is not None else latest
    current = start

    print(f"Connected. Latest block : {latest}")
    print(f"Database               : {args.db}")
    print(f"Range                  : {start} → {end}")
    print(f"MEW tiers (fee_factor) : economy={GREEN}1.25{RESET}  regular={GREEN}1.50{RESET}  fast={GREEN}1.75{RESET}")
    print(f"min priorityFee        : {MIN_PRIORITY_GWEI} Gwei")
    print(
        f"Signals: {BLUE}[GAS]{RESET} fee_factor match  |  "
        f"{GREEN}✅ + priority ≥ {MIN_PRIORITY_GWEI} Gwei → saved to DB{RESET}"
    )
    print("Scanning. Ctrl+C to stop.\n")

    total_blocks  = 0
    total_matches = 0
    total_saved   = 0

    while True:
        try:
            block = w3.eth.get_block(current, full_transactions=True)
        except Exception as exc:
            print(f"[block {current}] fetch error: {exc}", flush=True)
            time.sleep(1)
            continue

        matches        = analyze_block(block, w3)
        total_blocks  += 1
        total_matches += len(matches)

        confirmed = [m for m in matches if m["confirmed"]]
        if confirmed:
            rows = [
                (
                    m["hash"], current, m["from"], m["tier"], m["tx_type"],
                    m["maxFee"], m["maxPriority"], m["baseFee"], m["fee_factor"],
                    m["raw_priority"],
                    m["gas_limit"], m["estimated_gas"], m["gas_limit_factor"],
                )
                for m in confirmed
            ]
            conn.executemany(INSERT_TX, rows)
            conn.commit()
            total_saved += len(confirmed)

        tx_count = len(block["transactions"])
        eip1559  = sum(1 for tx in block["transactions"] if tx.get("maxFeePerGas") is not None)
        print(
            f"[block {current}] txs={tx_count} eip1559={eip1559}"
            f" matches={len(matches)} saved={len(confirmed)}"
            f"  (total: {total_blocks} blocks, {total_matches} pattern, {total_saved} saved)",
            flush=True,
        )
        for m in matches:
            print_match(current, m)

        if current >= end:
            print(f"\n── Reached end block {end}, stopping ──")
            break
        current += 1

        if args.delay > 0:
            time.sleep(args.delay)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
