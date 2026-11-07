"""Performance metrics computed from an equity curve and a trade list."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def cagr(equity: pd.Series, periods_per_year: int = 252) -> float:
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return 0.0
    years = (len(equity) - 1) / periods_per_year
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def sharpe(returns: pd.Series, periods_per_year: int = 252, rf: float = 0.0) -> float:
    r = returns.dropna() - rf / periods_per_year
    sd = r.std(ddof=1)
    if len(r) < 2 or np.isnan(sd) or sd < 1e-12:
        return 0.0
    return float(r.mean() / sd * math.sqrt(periods_per_year))


def sortino(returns: pd.Series, periods_per_year: int = 252, rf: float = 0.0) -> float:
    r = returns.dropna() - rf / periods_per_year
    downside = np.sqrt((np.minimum(r, 0) ** 2).mean())
    if len(r) < 2 or downside == 0:
        return 0.0
    return float(r.mean() / downside * math.sqrt(periods_per_year))


def max_consecutive_losses(pnls: list[float]) -> int:
    best = cur = 0
    for p in pnls:
        cur = cur + 1 if p < 0 else 0
        best = max(best, cur)
    return best


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarize(equity: pd.Series, trades: list[dict], initial_capital: float, periods_per_year: int = 252) -> dict:
    rets = equity.pct_change().dropna()
    pnls = [t["net_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    mdd = max_drawdown(equity)
    net = float(equity.iloc[-1] - initial_capital) if len(equity) else 0.0
    turnover = sum(t.get("turnover", 0.0) for t in trades)
    lo, hi = wilson_interval(len(wins), len(pnls))
    return {
        "initial_capital": initial_capital,
        "final_equity": round(float(equity.iloc[-1]), 2) if len(equity) else initial_capital,
        "total_return": round(net / initial_capital, 4),
        "cagr": round(cagr(equity, periods_per_year), 4),
        "annualised_volatility": round(float(rets.std(ddof=1) * math.sqrt(periods_per_year)) if len(rets) > 1 else 0.0, 4),
        "sharpe": round(sharpe(rets, periods_per_year), 3),
        "sortino": round(sortino(rets, periods_per_year), 3),
        "max_drawdown": round(mdd, 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
        "win_rate_95ci": [round(lo, 3), round(hi, 3)],
        "avg_win": round(float(np.mean(wins)), 2) if wins else 0.0,
        "avg_loss": round(float(np.mean(losses)), 2) if losses else 0.0,
        "expectancy_per_trade": round(float(np.mean(pnls)), 2) if pnls else 0.0,
        "avg_r_multiple": round(float(np.mean([t["r_multiple"] for t in trades])), 3) if trades else 0.0,
        "reward_risk_ratio": round(abs(np.mean(wins) / np.mean(losses)), 3) if wins and losses and np.mean(losses) != 0 else None,
        "n_trades": len(pnls),
        "max_consecutive_losses": max_consecutive_losses(pnls),
        "recovery_factor": round(net / (abs(mdd) * initial_capital), 3) if mdd < 0 else None,
        "turnover": round(turnover, 2),
        "total_costs": round(sum(t.get("costs", 0.0) for t in trades), 2),
        "total_slippage": round(sum(t.get("slippage", 0.0) for t in trades), 2),
        "net_profit_after_costs": round(net, 2),
        "small_sample_warning": len(pnls) < 30,
    }
