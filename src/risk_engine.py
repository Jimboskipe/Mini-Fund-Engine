"""Portfolio-level risk overlays, kill switches, and rolling correlation diagnostics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from config import RiskConfig


@dataclass
class RiskDecision:
    adjusted_weights: Dict[str, float]
    gross_exposure: float
    net_exposure: float
    status: str  # "OK", "THROTTLED", or "HALTED"
    alerts: List[str]


def apply_risk_limits(
    raw_weights: Dict[str, float],
    current_drawdown: float,
    prev_day_return: float,
    cfg: RiskConfig,
    is_halted: bool = False,
) -> RiskDecision:
    """
    Enforce hard institutional risk controls on target portfolio weights:
    1. Per-asset notional cap (max_asset_weight)
    2. Gross leverage cap (max_gross_leverage)
    3. Trailing drawdown throttle (-10% -> 50% scale) and hard kill switch (-15% -> flat)
    4. Single-day loss alert (-3%)
    """
    alerts: List[str] = []
    status = "OK"

    if prev_day_return <= cfg.daily_loss_alert:
        alerts.append(
            f"DAILY_LOSS_ALERT: prev day return {prev_day_return:.2%} <= {cfg.daily_loss_alert:.2%}"
        )

    # Hard kill switch: once breached, stays HALTED until explicit manual review/reset
    if is_halted or current_drawdown <= cfg.dd_halt_threshold:
        status = "HALTED"
        alerts.append(
            f"KILL_SWITCH_HALT: drawdown {current_drawdown:.2%} breached {cfg.dd_halt_threshold:.2%}; book flattened"
        )
        flat = {sym: 0.0 for sym in raw_weights}
        return RiskDecision(
            adjusted_weights=flat,
            gross_exposure=0.0,
            net_exposure=0.0,
            status=status,
            alerts=alerts,
        )

    # 1. Per-asset weight clamp
    capped = {
        sym: max(-cfg.max_asset_weight, min(cfg.max_asset_weight, w))
        for sym, w in raw_weights.items()
    }

    # 2. Gross leverage scale-down if needed
    gross = sum(abs(w) for w in capped.values())
    if gross > cfg.max_gross_leverage and gross > 0:
        scale = cfg.max_gross_leverage / gross
        capped = {sym: w * scale for sym, w in capped.items()}
        alerts.append(
            f"GROSS_LEVERAGE_CAP: scaled gross from {gross:.2f}x to {cfg.max_gross_leverage:.2f}x"
        )

    # 3. Drawdown throttle
    if current_drawdown <= cfg.dd_throttle_threshold:
        status = "THROTTLED"
        capped = {sym: w * cfg.dd_throttle_scale for sym, w in capped.items()}
        alerts.append(
            f"DD_THROTTLE: drawdown {current_drawdown:.2%} <= {cfg.dd_throttle_threshold:.2%}; "
            f"risk scaled to {cfg.dd_throttle_scale:.0%}"
        )

    final_gross = sum(abs(w) for w in capped.values())
    final_net = sum(capped.values())
    return RiskDecision(
        adjusted_weights=capped,
        gross_exposure=final_gross,
        net_exposure=final_net,
        status=status,
        alerts=alerts,
    )


def _pearson_corr(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0.0 or dy == 0.0:
        return 0.0
    return num / (dx * dy)


def compute_trailing_correlation_alerts(
    returns_by_symbol: Dict[str, List[float]],
    window: int,
    threshold: float,
) -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    """Compute trailing pairwise return correlation matrix and flag crowded pairs."""
    symbols = list(returns_by_symbol.keys())
    matrix: Dict[str, Dict[str, float]] = {s: {} for s in symbols}
    alerts: List[str] = []

    for i, s1 in enumerate(symbols):
        r1 = returns_by_symbol[s1][-window:]
        for j, s2 in enumerate(symbols):
            r2 = returns_by_symbol[s2][-window:]
            c = 1.0 if i == j else _pearson_corr(r1, r2)
            matrix[s1][s2] = round(c, 3)
            if j > i and abs(c) >= threshold:
                alerts.append(f"HIGH_CORR ({window}d): {s1}/{s2} = {c:+.2f}")

    return matrix, alerts
