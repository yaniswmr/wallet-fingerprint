"""
Validator — live comparison between the discovered formula and the MetaMask API.

Runs n_checks snapshots and prints a detailed per-field breakdown plus
aggregated statistics at the end.
"""

import time

import numpy as np
from web3 import Web3

from .extractor import extract_tips
from .fetcher import fetch_fee_history, fetch_metamask
from .metrics import mape_max_fee, mape_priority
from .models import GasFees, MultiplierResult, SearchResult, Tier


def run(
    w3: Web3,
    priority_params: SearchResult,
    best_multiplier: MultiplierResult,
    n_checks: int = 10,
    pause: float = 30.0,
) -> None:
    p = priority_params
    m = best_multiplier

    print("\n" + "=" * 60)
    print("VALIDATION — Live comparison")
    print(f"  Formula:\n{m.formula_str(p)}")
    print("=" * 60)

    header = f"  {'field':<26} {'actual':>10} {'predicted':>10} {'error%':>8}"
    sep    = "  " + "-" * 58

    all_errs_p: list[float] = []
    all_errs_m: list[float] = []

    for check_i in range(1, n_checks + 1):
        actual = fetch_metamask()
        fh     = fetch_fee_history(w3, p.n_blocks)

        pf_low  = extract_tips(fh["rewards"], p.p_low)
        pf_med  = extract_tips(fh["rewards"], p.p_med)
        pf_high = extract_tips(fh["rewards"], p.p_high)
        base    = w3.eth.get_block("latest")["baseFeePerGas"] / 1e9

        pred = GasFees(
            low    = Tier(priority_fee=pf_low,  max_fee=base * m.m_low  + pf_low),
            medium = Tier(priority_fee=pf_med,  max_fee=base * m.m_med  + pf_med),
            high   = Tier(priority_fee=pf_high, max_fee=base * m.m_high + pf_high),
            base_fee=base,
        )

        err_p = mape_priority(pred, actual)
        err_m = mape_max_fee(pred, actual)
        all_errs_p.append(err_p)
        all_errs_m.append(err_m)

        print(f"\n  Check {check_i}/{n_checks}")
        print(header)
        print(sep)
        _row("base_fee",          actual.base_fee,           base)
        for tier_name, act_t, pred_t in [
            ("low",    actual.low,    pred.low),
            ("medium", actual.medium, pred.medium),
            ("high",   actual.high,   pred.high),
        ]:
            _row(f"{tier_name}.priority_fee", act_t.priority_fee, pred_t.priority_fee)
            _row(f"{tier_name}.max_fee",      act_t.max_fee,      pred_t.max_fee)
        print(f"  → priority MAPE={err_p:.2f}%   maxFee MAPE={err_m:.2f}%")

        if check_i < n_checks:
            time.sleep(pause)

    print(f"\n  {'─'*58}")
    print(f"  Average over {n_checks} checks:")
    print(f"    priority MAPE : {np.mean(all_errs_p):.2f}% ± {np.std(all_errs_p):.2f}%")
    print(f"    max fee  MAPE : {np.mean(all_errs_m):.2f}% ± {np.std(all_errs_m):.2f}%")


def _row(label: str, actual: float, predicted: float) -> None:
    err = abs(predicted - actual) / actual * 100 if actual > 0 else 0.0
    print(f"  {label:<26} {actual:>10.4f} {predicted:>10.4f} {err:>8.2f}%")
