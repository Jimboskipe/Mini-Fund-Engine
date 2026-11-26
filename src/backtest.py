"""Causal walk-forward IS/OOS backtester with explicit transaction costs and tearsheet metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence

from config import EngineConfig
from src.risk_engine import apply_risk_limits, compute_trailing_correlation_alerts
from src.strategy import compute_raw_vol_target_weights, compute_returns


@dataclass
class PerformanceMetrics:
    label: str
    start_date: str
    end_date: str
    days: int
    total_return: float
    cagr: float
    annual_vol: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    win_rate: float
    worst_day: float
    avg_daily_turnover: float
    total_cost_drag: float


@dataclass
class BacktestResult:
    dates: List[str]
    equity_curve: List[float]
    daily_returns: List[float]
    drawdowns: List[float]
    final_weights: Dict[str, float]
    final_signals: Dict[str, float]
    final_vols: Dict[str, float]
    final_status: str
    final_gross: float
    final_net: float
    risk_alerts_log: List[str]
    correlation_matrix: Dict[str, Dict[str, float]]
    correlation_alerts: List[str]
    full_metrics: PerformanceMetrics
    in_sample_metrics: PerformanceMetrics
    out_of_sample_metrics: PerformanceMetrics


def compute_metrics(
    label: str,
    dates: Sequence[str],
    daily_returns: Sequence[float],
    daily_turnovers: Sequence[float],
    daily_costs: Sequence[float],
    trading_days_per_year: int = 365,
) -> PerformanceMetrics:
    n = len(daily_returns)
    if n == 0:
        return PerformanceMetrics(label, "N/A", "N/A", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    eq = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in daily_returns:
        eq *= 1.0 + r
        if eq > peak:
            peak = eq
        dd = (eq / peak) - 1.0
        if dd < max_dd:
            max_dd = dd

    total_return = eq - 1.0
    years = max(n / float(trading_days_per_year), 1.0 / trading_days_per_year)
    cagr = (eq ** (1.0 / years)) - 1.0 if eq > 0 else -1.0

    mean_r = sum(daily_returns) / n
    var_r = sum((r - mean_r) ** 2 for r in daily_returns) / max(1, n - 1)
    daily_vol = math.sqrt(max(var_r, 0.0))
    ann_vol = daily_vol * math.sqrt(trading_days_per_year)
    sharpe = (mean_r / daily_vol) * math.sqrt(trading_days_per_year) if daily_vol > 1e-12 else 0.0

    neg_returns = [r for r in daily_returns if r < 0]
    downside_dev = (
        math.sqrt(sum(r * r for r in neg_returns) / n)
        if neg_returns
        else 0.0
    )
    sortino = (
        (mean_r / downside_dev) * math.sqrt(trading_days_per_year)
        if downside_dev > 1e-12
        else 0.0
    )

    calmar = (cagr / abs(max_dd)) if abs(max_dd) > 1e-9 else 0.0
    non_zero_days = [r for r in daily_returns if abs(r) > 1e-12]
    win_rate = (
        sum(1 for r in non_zero_days if r > 0) / len(non_zero_days)
        if non_zero_days
        else 0.0
    )
    worst_day = min(daily_returns)
    avg_turnover = sum(daily_turnovers) / n
    total_cost_drag = sum(daily_costs)

    return PerformanceMetrics(
        label=label,
        start_date=dates[0],
        end_date=dates[-1],
        days=n,
        total_return=total_return,
        cagr=cagr,
        annual_vol=ann_vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        calmar=calmar,
        win_rate=win_rate,
        worst_day=worst_day,
        avg_daily_turnover=avg_turnover,
        total_cost_drag=total_cost_drag,
    )


def run_backtest(
    dates: Sequence[str],
    closes_by_symbol: Dict[str, List[float]],
    cfg: EngineConfig,
) -> BacktestResult:
    """
    Strictly causal daily simulation:
    - Weights decided at close of day t-1 (using only data <= t-1) earn day t's return.
    - Rebalance turnover between day t-2 and day t-1 incurs one-way fee + slippage drag.
    """
    symbols = list(closes_by_symbol.keys())
    n = len(dates)
    if n < max(cfg.strategy.lookbacks) + 5:
        raise ValueError(f"Not enough aligned bars ({n}) for lookbacks {cfg.strategy.lookbacks}")

    returns_by_symbol = {sym: compute_returns(closes_by_symbol[sym]) for sym in symbols}
    raw_weights, signals, vols = compute_raw_vol_target_weights(
        closes_by_symbol,
        target_annual_vol=cfg.risk.target_annual_vol,
        cfg=cfg.strategy,
    )

    equity = cfg.risk.initial_capital
    peak_equity = equity
    is_halted = False
    prev_weights = {sym: 0.0 for sym in symbols}
    prev_day_ret = 0.0

    equity_curve: List[float] = []
    daily_returns: List[float] = []
    drawdowns: List[float] = []
    daily_turnovers: List[float] = []
    daily_costs: List[float] = []
    risk_alerts_log: List[str] = []

    latest_decision = None

    for t in range(n):
        if t == 0:
            equity_curve.append(equity)
            daily_returns.append(0.0)
            drawdowns.append(0.0)
            daily_turnovers.append(0.0)
            daily_costs.append(0.0)
            continue

        # Target weights decided strictly from close of day t-1 (NO lookahead into day t)
        raw_t_minus_1 = {sym: raw_weights[sym][t - 1] for sym in symbols}
        current_dd = (equity / peak_equity) - 1.0
        decision = apply_risk_limits(
            raw_weights=raw_t_minus_1,
            current_drawdown=current_dd,
            prev_day_return=prev_day_ret,
            cfg=cfg.risk,
            is_halted=is_halted,
        )
        if decision.status == "HALTED":
            is_halted = True
        for alert in decision.alerts:
            risk_alerts_log.append(f"[{dates[t]}] {alert}")

        target_w = decision.adjusted_weights
        turnover = sum(abs(target_w[sym] - prev_weights[sym]) for sym in symbols)
        cost_drag = turnover * cfg.costs.one_way_rate

        gross_ret = sum(target_w[sym] * returns_by_symbol[sym][t] for sym in symbols)
        net_ret = gross_ret - cost_drag

        equity *= max(0.0, 1.0 + net_ret)
        if equity > peak_equity:
            peak_equity = equity
        dd = (equity / peak_equity) - 1.0

        equity_curve.append(equity)
        daily_returns.append(net_ret)
        drawdowns.append(dd)
        daily_turnovers.append(turnover)
        daily_costs.append(cost_drag)

        prev_weights = target_w
        prev_day_ret = net_ret
        latest_decision = decision

    # Also compute live-next-day target weights from the very last close (index n-1)
    final_raw = {sym: raw_weights[sym][-1] for sym in symbols}
    final_dd = drawdowns[-1]
    final_decision = apply_risk_limits(
        raw_weights=final_raw,
        current_drawdown=final_dd,
        prev_day_return=daily_returns[-1],
        cfg=cfg.risk,
        is_halted=is_halted,
    )

    # Evaluate metrics strictly after strategy warm-up so flat warm-up bars don't distort stats
    warmup = max(cfg.strategy.lookbacks) + 1
    active_dates = list(dates[warmup:])
    active_rets = daily_returns[warmup:]
    active_turns = daily_turnovers[warmup:]
    active_costs = daily_costs[warmup:]

    split_idx = max(1, int(len(active_dates) * cfg.in_sample_fraction))
    full_m = compute_metrics(
        "Full Active Period",
        active_dates,
        active_rets,
        active_turns,
        active_costs,
        cfg.strategy.trading_days_per_year,
    )
    is_m = compute_metrics(
        "In-Sample (70%)",
        active_dates[:split_idx],
        active_rets[:split_idx],
        active_turns[:split_idx],
        active_costs[:split_idx],
        cfg.strategy.trading_days_per_year,
    )
    oos_m = compute_metrics(
        "Out-of-Sample (30%)",
        active_dates[split_idx:],
        active_rets[split_idx:],
        active_turns[split_idx:],
        active_costs[split_idx:],
        cfg.strategy.trading_days_per_year,
    )

    corr_matrix, corr_alerts = compute_trailing_correlation_alerts(
        returns_by_symbol,
        window=cfg.risk.corr_window,
        threshold=cfg.risk.high_corr_alert,
    )

    return BacktestResult(
        dates=list(dates),
        equity_curve=equity_curve,
        daily_returns=daily_returns,
        drawdowns=drawdowns,
        final_weights=final_decision.adjusted_weights,
        final_signals={sym: signals[sym][-1] for sym in symbols},
        final_vols={sym: vols[sym][-1] for sym in symbols},
        final_status=final_decision.status,
        final_gross=final_decision.gross_exposure,
        final_net=final_decision.net_exposure,
        risk_alerts_log=risk_alerts_log[-25:],  # Keep latest 25 events in summary
        correlation_matrix=corr_matrix,
        correlation_alerts=corr_alerts,
        full_metrics=full_m,
        in_sample_metrics=is_m,
        out_of_sample_metrics=oos_m,
    )
