#!/usr/bin/env python3
"""
reverse_base.py — Reverse-engineering de la formule base_slow du relayer Ambire.

Pour chaque computation unique du serveur, on fetch la feeHistory au bloc correspondant
et on teste toutes les combinaisons (fenêtre, statistique, multiplicateur) pour trouver
celle qui reproduit le mieux les valeurs observées.

Usage:
  python reverse_base.py [--db PATH] [--rpc URL] [--samples N] [--max-blocks N]
"""

import argparse
import os
import sqlite3
import sys
import time

import requests

DEFAULT_DB      = os.path.join(os.path.dirname(__file__), "gas_fees_collected.db")
DEFAULT_RPC     = os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com")
DEFAULT_SAMPLES = 100  # nombre de points à analyser (0 = tous)
DEFAULT_MAX_BLK = 500  # fenêtre maximale testée (blocs)

# ---------------------------------------------------------------------------
# RPC helpers
# ---------------------------------------------------------------------------
_fh_cache: dict = {}

def rpc(rpc_url: str, method: str, params: list, timeout: int = 15) -> dict:
    r = requests.post(
        rpc_url,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        timeout=timeout,
    )
    return r.json()["result"]


def fee_history(rpc_url: str, n: int, block: int, pcts: list[int]) -> tuple[list[int], list[list[int]]]:
    """
    Retourne (baseFees_wei, rewards_wei).
    baseFees a n+1 éléments, le dernier étant le next-block prédit.
    rewards a n éléments (peut être vide si pcts=[]).
    Résultats mis en cache (rpc_url, n, block, pcts).
    """
    key = (rpc_url, n, block, tuple(pcts))
    if key not in _fh_cache:
        raw = rpc(rpc_url, "eth_feeHistory", [hex(n), hex(block), pcts])
        bfs     = [int(x, 16) for x in raw["baseFeePerGas"]]
        rewards = [[int(r, 16) for r in row] for row in raw.get("reward", [])]
        _fh_cache[key] = (bfs, rewards)
    return _fh_cache[key]


def eth_gas_price(rpc_url: str) -> int:
    return int(rpc(rpc_url, "eth_gasPrice", []), 16)

# ---------------------------------------------------------------------------
# Statistics helpers (tout en wei)
# ---------------------------------------------------------------------------
def _avg(vs: list[int]) -> int:
    return sum(vs) // len(vs) if vs else 0

def _max(vs: list[int]) -> int:
    return max(vs) if vs else 0

def _min(vs: list[int]) -> int:
    return min(vs) if vs else 0

def _pct(sorted_vs: list[int], p: float) -> int:
    if not sorted_vs: return 0
    idx = (len(sorted_vs) - 1) * p / 100
    lo  = int(idx)
    hi  = min(lo + 1, len(sorted_vs) - 1)
    return int(sorted_vs[lo] * (1 - idx + lo) + sorted_vs[hi] * (idx - lo))

def _ema(vs: list[int], alpha: float) -> int:
    """EMA du plus ancien au plus récent (vs[0] = plus ancien)."""
    if not vs: return 0
    r = vs[0]
    for v in vs[1:]:
        r = int(alpha * v + (1 - alpha) * r)
    return r

def _wma(vs: list[int], decay: float) -> int:
    """Moyenne pondérée, plus récent = poids plus élevé."""
    if not vs: return 0
    weights = [decay ** i for i in range(len(vs) - 1, -1, -1)]
    total_w = sum(weights)
    return int(sum(v * w for v, w in zip(vs, weights)) / total_w)

def _predict_next(basefees: list[int], gas_used: int, gas_limit: int) -> int:
    """EIP-1559 : baseFee du prochain bloc selon taux de remplissage actuel."""
    bf = basefees[-1]
    target = gas_limit // 2
    if gas_used > target:
        delta = bf * (gas_used - target) // target // 8
        if delta == 0: delta = 1
        return bf + delta
    elif gas_used < target:
        delta = bf * (target - gas_used) // target // 8
        return max(bf - delta, 0)
    return bf

# ---------------------------------------------------------------------------
# Formules testées — retournent toutes une valeur en wei
# ---------------------------------------------------------------------------
def build_formulas(bfs: list[int], rewards_by_pct: list[list[int]], gas_price_wei: int) -> list[tuple[str, int]]:
    """
    bfs         : liste de baseFees wei (n+1 éléments, le dernier = next prédit)
    rewards_by_pct : liste de n listes, chacune contenant les rewards aux percentiles [25, 50, 75, 90, 95]
    gas_price_wei  : résultat de eth_gasPrice au moment de la computation
    Retourne [(nom_formule, valeur_wei), ...]
    """
    formulas = []

    # bfs[:-1] = les N blocs historiques ; bfs[-1] = next block prédit
    hist = bfs[:-1]
    next_pred = bfs[-1]
    n = len(hist)
    hist_sorted = sorted(hist)

    # ── Formules basées sur les baseFees historiques ──────────────────────
    stats = {
        "avg":  _avg(hist),
        "max":  _max(hist),
        "min":  _min(hist),
        "p50":  _pct(hist_sorted, 50),
        "p60":  _pct(hist_sorted, 60),
        "p75":  _pct(hist_sorted, 75),
        "p80":  _pct(hist_sorted, 80),
        "p90":  _pct(hist_sorted, 90),
        "p95":  _pct(hist_sorted, 95),
        "p99":  _pct(hist_sorted, 99),
        "next": next_pred,  # baseFee prédit pour le prochain bloc
    }

    # EMAs avec différents alphas
    for alpha_x10 in [1, 2, 3, 5]:
        alpha = alpha_x10 / 10
        stats[f"ema{alpha_x10}"] = _ema(hist, alpha)

    # Moyennes pondérées (decay)
    for decay_x10 in [7, 8, 9]:
        decay = decay_x10 / 10
        stats[f"wma{decay_x10}"] = _wma(hist, decay)

    # Multiplicateurs à tester — grille large + grille fine autour de 1.10–1.20
    multipliers = {
        # Large
        "×1.00": 1.000,
        "×1.02": 1.020,
        "×1.05": 1.050,
        "×1.08": 1.080,
        # Grille fine ×1.10–×1.20 (pas de 0.01)
        "×1.10": 1.100,
        "×1.11": 1.110,
        "×1.12": 1.120,
        "×1.125": 1.125,
        "×1.13": 1.130,
        "×1.14": 1.140,
        "×1.15": 1.150,
        "×1.16": 1.160,
        "×1.17": 1.170,
        "×1.18": 1.180,
        "×1.19": 1.190,
        "×1.20": 1.200,
        # Large au-dessus
        "×1.25": 1.250,
        "×1.30": 1.300,
        "×1.40": 1.400,
        "×1.50": 1.500,
    }

    for stat_name, val in stats.items():
        for mult_name, mult in multipliers.items():
            formulas.append((f"{stat_name}_{mult_name}", int(val * mult)))

    # ── Formules basées sur eth_gasPrice ──────────────────────────────────
    if gas_price_wei > 0:
        for mult_name, mult in multipliers.items():
            formulas.append((f"gasPrice_{mult_name}", int(gas_price_wei * mult)))

    # ── Formules sur les rewards (tips) ──────────────────────────────────
    # pcts = [25, 50, 75, 90, 95]
    if rewards_by_pct:
        for pct_idx, pct_label in enumerate([25, 50, 75, 90, 95]):
            pct_vals = [row[pct_idx] for row in rewards_by_pct if len(row) > pct_idx]
            if pct_vals:
                avg_tip = _avg(pct_vals)
                max_tip = _max(pct_vals)
                # baseFee + tip percentile
                for mult_name, mult in [("×1.00", 1.0), ("×1.05", 1.05), ("×1.10", 1.10)]:
                    formulas.append((f"bf_avg+tipP{pct_label}avg_{mult_name}",
                                     int((_avg(hist) + avg_tip) * mult)))
                    formulas.append((f"bf_max+tipP{pct_label}avg_{mult_name}",
                                     int((_max(hist) + avg_tip) * mult)))

    # ── Formule "next × prédictions multiples" ──────────────────────────
    # Simule M blocs supplémentaires de remplissage partiel
    cur = hist[-1] if hist else 0
    for m, label in [(1, "2blk"), (2, "3blk"), (3, "4blk")]:
        pred = next_pred
        for _ in range(m):
            pred = int(pred * 1.0125)  # +12.5%/8 per block, si bloc plein
        formulas.append((f"next_fullblocks_{label}", pred))

    # ── max sur sous-fenêtres progressives ───────────────────────────────
    for pct_window in [25, 50, 75, 100]:
        k = max(1, n * pct_window // 100)
        recent = hist[-k:]
        for mult_name, mult in [("×1.00", 1.0), ("×1.05", 1.05), ("×1.10", 1.10)]:
            formulas.append((f"max_{pct_window}pct_{mult_name}", int(_max(recent) * mult)))

    return formulas


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score(predicted: int, actual: int) -> float:
    """Absolute relative error (%)."""
    if actual == 0: return float("inf")
    return abs(predicted - actual) / actual * 100


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Reverse-engineering de base_slow (Ambire relayer)")
    p.add_argument("--db",         default=DEFAULT_DB,      help="SQLite DB path")
    p.add_argument("--rpc",        default=DEFAULT_RPC,     help="Ethereum JSON-RPC URL")
    p.add_argument("--samples",    type=int, default=DEFAULT_SAMPLES,
                   help="Nb de points à analyser (0 = tous)")
    p.add_argument("--max-blocks", type=int, default=DEFAULT_MAX_BLK,
                   help="Fenêtre maximale feeHistory (blocs)")
    p.add_argument("--sleep",      type=float, default=0.3,
                   help="Délai entre requêtes RPC (s) pour éviter rate-limit")
    p.add_argument("--top",        type=int,   default=30,
                   help="Nombre de formules à afficher dans le classement (default: 30)")
    p.add_argument("--filter",     type=str,   default="",
                   help="N'affiche que les formules contenant ce mot (ex: 'w1_' ou 'max')")
    args = p.parse_args()

    con = sqlite3.connect(args.db)
    # Une ligne par computation unique (updated_ms), les plus récentes d'abord
    query = """
        SELECT block_number, base_slow, prio_medium, ts, updated_ms
        FROM gas_fees
        WHERE block_number IS NOT NULL AND base_slow IS NOT NULL
        GROUP BY updated_ms
        ORDER BY id DESC
    """
    if args.samples > 0:
        query += f" LIMIT {args.samples}"

    rows = con.execute(query).fetchall()
    if not rows:
        print("Aucune donnée dans la DB.")
        sys.exit(1)

    print(f"Données : {len(rows)} computations uniques")
    print(f"RPC     : {args.rpc}")
    print(f"Fenêtre : {args.max_blocks} blocs max\n")

    # Fetch eth_gasPrice courant (on ne peut pas récupérer la valeur historique)
    try:
        current_gp = eth_gas_price(args.rpc)
        print(f"eth_gasPrice actuel : {current_gp/1e9:.4f} Gwei (utilisé en fallback)")
    except Exception as e:
        current_gp = 0
        print(f"eth_gasPrice : indisponible ({e})")

    # Accumulateur d'erreurs par formule
    formula_errors: dict[str, list[float]] = {}

    print(f"\n{'#':>4}  {'block':>9}  {'base_exp':>10}  → analyse...")
    print("-" * 55)

    for i, (block_num, base_slow_str, prio_med, ts, updated_ms) in enumerate(rows, 1):
        base_exp = int(base_slow_str)   # valeur attendue en wei

        # Estimation du bloc utilisé par le serveur
        # delta_ms = temps écoulé entre la computation serveur et ma collecte
        delta_ms    = ts * 1000 - updated_ms
        delta_blks  = max(0, round(delta_ms / 12000))
        server_block = max(1, block_num - delta_blks)

        base_exp_gwei = base_exp / 1e9
        print(f"{i:>4}  {server_block:>9}  {base_exp_gwei:>10.4f} Gwei", end="", flush=True)

        try:
            bfs, rewards = fee_history(args.rpc, min(args.max_blocks, 500), server_block, [25, 50, 75, 90, 95])
            time.sleep(args.sleep)
        except Exception as e:
            print(f"  [skip RPC error: {e}]")
            continue

        # Formules sur différentes fenêtres
        all_formula_values: list[tuple[str, int]] = []

        # Fenêtres à tester (en blocs) — w1 = dernier bloc seul
        windows = [1, 2, 3, 4, 5, 10, 20, 50, 100, 200, 300, min(500, args.max_blocks)]
        for window in windows:
            if window > len(bfs) - 1:
                continue
            # Pour w1 : bfs_w = [dernier_bloc_historique, next_prédit]
            bfs_w  = bfs[-window-1:-1] + [bfs[-1]]   # window historiques + next prédit
            rews_w = rewards[-window:] if len(rewards) >= window else rewards

            prefix_formulas = build_formulas(bfs_w, rews_w, current_gp)
            for fname, fval in prefix_formulas:
                all_formula_values.append((f"w{window}_{fname}", fval))

        # Accumule les erreurs
        for fname, fval in all_formula_values:
            err = score(fval, base_exp)
            if fname not in formula_errors:
                formula_errors[fname] = []
            formula_errors[fname].append(err)

        # Trouver la meilleure formule pour ce point
        best_name, best_err = min(
            ((fn, score(fv, base_exp)) for fn, fv in all_formula_values),
            key=lambda x: x[1]
        )
        best_val = next(fv for fn, fv in all_formula_values if fn == best_name)
        print(f"  best={best_name} → {best_val/1e9:.4f} Gwei ({best_err:.2f}%)")

    # ── Classement final ─────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("CLASSEMENT FINAL — formules par MAPE (Mean Absolute Percentage Error)")
    print("=" * 80)

    # Filtre : formules testées sur au moins la moitié des points
    min_samples = max(1, len(rows) // 2)
    ranked = [
        (fname, sum(errs) / len(errs), min(errs), max(errs),
         sum(1 for e in errs if e < 2) / len(errs) * 100,   # % within 2%
         sum(1 for e in errs if e < 5) / len(errs) * 100,   # % within 5%
         len(errs))
        for fname, errs in formula_errors.items()
        if len(errs) >= min_samples
    ]
    ranked.sort(key=lambda x: x[1])

    # Déduplique les formules w1 (avg=max=min=p50=... pour 1 seul bloc)
    # → garde le meilleur représentant par (window, multiplicateur)
    seen_w1: set = set()
    deduped = []
    for entry in ranked:
        fname = entry[0]
        parts = fname.split("_")
        if parts[0] == "w1":
            # Clé = (window, multiplicateur) → garde le premier (meilleur MAPE)
            key = (parts[0], parts[-1])
            if key in seen_w1:
                continue
            seen_w1.add(key)
        deduped.append(entry)
    ranked = deduped

    display = [r for r in ranked if args.filter in r[0]] if args.filter else ranked

    print(f"\n{'Formule':55}  {'MAPE%':>7}  {'min%':>7}  {'max%':>7}  {'<2%':>6}  {'<5%':>6}  {'N':>4}")
    print("-" * 110)
    for fname, mape, mn, mx, pct2, pct5, n_pts in display[:args.top]:
        print(f"{fname:55}  {mape:7.2f}  {mn:7.2f}  {mx:7.2f}  {pct2:6.1f}  {pct5:6.1f}  {n_pts:4}")

    # ── Top 5 en détail ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TOP 5 — Détail formule par formule")
    print("=" * 80)
    for fname, mape, mn, mx, pct2, pct5, n_pts in ranked[:min(5, args.top)]:
        print(f"\n  {fname}")
        print(f"  MAPE={mape:.2f}%  min={mn:.2f}%  max={mx:.2f}%  within2%={pct2:.1f}%  within5%={pct5:.1f}%")

        # Décoder la formule
        parts = fname.split("_")
        window = parts[0]       # ex: "w20"
        stat   = parts[1]       # ex: "max"
        mult   = parts[-1]      # ex: "×1.10"
        print(f"  → {window} blocs, statistique={stat}, multiplicateur={mult}")

    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompu.")
