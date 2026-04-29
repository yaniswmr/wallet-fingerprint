"""
Error metrics for comparing predicted vs actual gas fees.
"""

import numpy as np
from .models import GasFees


def _mape(pairs: list[tuple[float, float]]) -> float:
    errs = [abs(p - a) / a * 100.0 for p, a in pairs if a > 0]
    return float(np.mean(errs)) if errs else float("inf")


def mape_priority(pred: GasFees, actual: GasFees) -> float:
    return _mape([
        (pred.low.priority_fee,    actual.low.priority_fee),
        (pred.medium.priority_fee, actual.medium.priority_fee),
        (pred.high.priority_fee,   actual.high.priority_fee),
    ])


def mape_max_fee(pred: GasFees, actual: GasFees) -> float:
    return _mape([
        (pred.low.max_fee,    actual.low.max_fee),
        (pred.medium.max_fee, actual.medium.max_fee),
        (pred.high.max_fee,   actual.high.max_fee),
    ])


def mape_all(pred: GasFees, actual: GasFees) -> float:
    return (mape_priority(pred, actual) + mape_max_fee(pred, actual)) / 2
