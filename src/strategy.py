"""Multi-horizon Time-Series Momentum (TSMOM) & 365-day EWMA volatility sizing."""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

from config import StrategyConfig


def compute_returns(closes: Sequence[float]) -> List[float]:
    """Simple daily returns; index 0 is 0.0."""
    if not closes:
        return []
    rets = [0.0]
    for i in range(1, len(closes)):
        rets.append((closes[i] / closes[i - 1]) - 1.0)
    return rets


def compute_ewma_annual_vol(
    returns: Sequence[float],
    span: int,
    trading_days: int = 365,
    floor: float = 0.10,
) -> List[float]:
    """Compute strictly causal EWMA annualized volatility (uses only data up to day t)."""
    alpha = 2.0 / (span + 1.0)
    ewma_var = (floor ** 2) / trading_days
    vols: List[float] = []
    for i, r in enumerate(returns):
        if i == 0:
            vols.append(floor)
            continue
        ewma_var = (1.0 - alpha) * ewma_var + alpha * (r * r)
        ann_vol = math.sqrt(max(ewma_var, 1e-12) * trading_days)
        vols.append(max(floor, ann_vol))
    return vols


def compute_tsmom_signal(
    closes: Sequence[float],
    cfg: StrategyConfig,
) -> List[float]:
    """
    Causal multi-horizon trend signal in [-1.0, +1.0] (or [0, +1] when allow_short=False).

    Blends volatility-normalized trend z-scores across configured lookbacks (e.g., 14/30/90d),
    squashed smoothly via tanh so outliers do not blow up sizing.
    """
    n = len(closes)
    rets = compute_returns(closes)
    vols = compute_ewma_annual_vol(
        rets,
        span=cfg.ewma_vol_span,
        trading_days=cfg.trading_days_per_year,
        floor=cfg.min_vol_floor,
    )
    max_lb = max(cfg.lookbacks)
    signals: List[float] = []

    for t in range(n):
        if t < max_lb:
            signals.append(0.0)
            continue
        horizon_scores = []
        daily_vol = vols[t] / math.sqrt(cfg.trading_days_per_year)
        for lb in cfg.lookbacks:
            cum_ret = (closes[t] / closes[t - lb]) - 1.0
            expected_vol = max(daily_vol * math.sqrt(lb), 1e-6)
            z = cum_ret / expected_vol
            horizon_scores.append(math.tanh(z))
        raw = sum(horizon_scores) / len(horizon_scores)
        if not cfg.allow_short:
            raw = max(0.0, raw)
        signals.append(max(-1.0, min(1.0, raw)))

    return signals


def compute_raw_vol_target_weights(
    closes_by_symbol: Dict[str, List[float]],
    target_annual_vol: float,
    cfg: StrategyConfig,
) -> Tuple[Dict[str, List[float]], Dict[str, List[float]], Dict[str, List[float]]]:
    """
    Compute per-day raw volatility-targeted weights before portfolio risk overlays.

    Each asset receives an equal risk budget = target_annual_vol / N_assets,
    scaled by its causal trend signal and inverse causal annualized volatility.
    """
    symbols = list(closes_by_symbol.keys())
    n_assets = max(1, len(symbols))
    per_asset_vol_budget = target_annual_vol / n_assets

    weights: Dict[str, List[float]] = {}
    signals: Dict[str, List[float]] = {}
    vols: Dict[str, List[float]] = {}

    for sym in symbols:
        closes = closes_by_symbol[sym]
        rets = compute_returns(closes)
        sym_vols = compute_ewma_annual_vol(
            rets,
            span=cfg.ewma_vol_span,
            trading_days=cfg.trading_days_per_year,
            floor=cfg.min_vol_floor,
        )
        sym_sigs = compute_tsmom_signal(closes, cfg)
        sym_w = [
            sym_sigs[t] * (per_asset_vol_budget / sym_vols[t])
            for t in range(len(closes))
        ]
        weights[sym] = sym_w
        signals[sym] = sym_sigs
        vols[sym] = sym_vols

    return weights, signals, vols
