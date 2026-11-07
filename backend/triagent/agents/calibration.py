"""Calibration of the judge's composite score against realised outcomes.

The composite score is NOT a probability. A calibrator maps it to an empirical
probability that the chosen plan reaches Target 1 before its stop, fitted on a
TRAINING period and evaluated on a later TEST period (Brier score + reliability table).
Isotonic regression (pool-adjacent-violators) keeps the mapping monotone.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _pav(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Pool-adjacent-violators for a non-decreasing fit."""
    vals, wts, sizes = list(y.astype(float)), list(w.astype(float)), [1] * len(y)
    i = 0
    while i < len(vals) - 1:
        if vals[i] > vals[i + 1]:
            tw = wts[i] + wts[i + 1]
            vals[i] = (vals[i] * wts[i] + vals[i + 1] * wts[i + 1]) / tw
            wts[i] = tw
            sizes[i] += sizes[i + 1]
            del vals[i + 1], wts[i + 1], sizes[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    return np.repeat(vals, sizes)


@dataclass
class IsotonicCalibrator:
    x: np.ndarray
    y: np.ndarray
    n_train: int
    train_period: str

    @classmethod
    def fit(cls, scores, outcomes, train_period: str = "", prior: float = 0.33, prior_weight: float = 5.0):
        s = np.asarray(scores, float)
        o = np.asarray(outcomes, float)
        order = np.argsort(s)
        s, o = s[order], o[order]
        # Laplace-style shrinkage toward the prior to avoid 0/1 estimates on tiny samples
        fitted = _pav(o, np.ones_like(o))
        n = len(o)
        fitted = (fitted * n + prior * prior_weight) / (n + prior_weight)
        return cls(x=s, y=fitted, n_train=n, train_period=train_period)

    def predict(self, score: float) -> float:
        if len(self.x) == 0:
            return float("nan")
        return float(np.interp(score, self.x, self.y))

    def describe(self) -> str:
        return (f"Isotonic calibration fitted on {self.n_train} historical decisions ({self.train_period}); "
                f"probability = P(target 1 before stop). Out-of-sample quality reported separately.")


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2)) if len(p) else float("nan")


def reliability_table(p, y, bins: int = 5) -> list[dict]:
    p, y = np.asarray(p, float), np.asarray(y, float)
    if len(p) == 0:
        return []
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    out = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & ((p <= hi) if i == bins - 1 else (p < hi))
        if m.sum() == 0:
            continue
        out.append({"bin": i, "p_low": round(float(lo), 3), "p_high": round(float(hi), 3), "n": int(m.sum()),
                    "mean_predicted": round(float(p[m].mean()), 3), "observed_rate": round(float(y[m].mean()), 3)})
    return out
