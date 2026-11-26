"""Unit tests verifying causal execution (no lookahead), cost drag, and risk guardrails."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from config import CostConfig, EngineConfig, RiskConfig
from src.backtest import run_backtest
from src.data_pipeline import align_close_matrix, generate_synthetic_universe
from src.paper_ledger import record_paper_snapshot
from src.risk_engine import apply_risk_limits
from src.strategy import compute_tsmom_signal


class TestMiniFundEngine(unittest.TestCase):
    def test_zero_lookahead_in_signals(self) -> None:
        """Changing future prices after index T must not alter signals at or before T."""
        cfg = EngineConfig()
        bars = generate_synthetic_universe(cfg.universe, num_days=300, seed=7)
        closes = [b.close for b in bars["BTC"]]

        sig_orig = compute_tsmom_signal(closes, cfg.strategy)

        mutated = list(closes)
        for i in range(200, len(mutated)):
            mutated[i] = mutated[i] * 3.0  # Massive future shock after day 199
        sig_mut = compute_tsmom_signal(mutated, cfg.strategy)

        self.assertEqual(sig_orig[:200], sig_mut[:200])

    def test_transaction_costs_reduce_returns(self) -> None:
        """Non-zero fee + slippage must strictly reduce final equity vs zero-cost simulation."""
        base_cfg = EngineConfig()
        bars = generate_synthetic_universe(base_cfg.universe, num_days=400, seed=11)
        dates, aligned = align_close_matrix(bars, base_cfg.symbols)

        zero_cost_cfg = replace(base_cfg, costs=CostConfig(exchange_fee_bps=0.0, slippage_bps=0.0))
        high_cost_cfg = replace(base_cfg, costs=CostConfig(exchange_fee_bps=25.0, slippage_bps=25.0))

        res_zero = run_backtest(dates, aligned, zero_cost_cfg)
        res_high = run_backtest(dates, aligned, high_cost_cfg)

        self.assertGreater(res_zero.equity_curve[-1], res_high.equity_curve[-1])
        self.assertGreater(res_high.full_metrics.total_cost_drag, 0.0)

    def test_risk_engine_caps_and_kill_switch(self) -> None:
        """Verify per-asset cap, gross leverage cap, throttle, and hard drawdown halt."""
        rcfg = RiskConfig(
            max_asset_weight=0.35,
            max_gross_leverage=1.00,
            dd_throttle_threshold=-0.10,
            dd_throttle_scale=0.50,
            dd_halt_threshold=-0.15,
        )
        raw = {"BTC": 0.90, "ETH": 0.80, "SOL": -0.70, "PAXG": 0.60, "LINK": 0.50}

        # Normal state: capped at 1.0x gross
        d_ok = apply_risk_limits(raw, current_drawdown=-0.02, prev_day_return=0.01, cfg=rcfg)
        self.assertEqual(d_ok.status, "OK")
        self.assertAlmostEqual(d_ok.gross_exposure, 1.00, places=6)

        # Throttled state: cut by 50%
        d_throt = apply_risk_limits(raw, current_drawdown=-0.11, prev_day_return=-0.01, cfg=rcfg)
        self.assertEqual(d_throt.status, "THROTTLED")
        self.assertAlmostEqual(d_throt.gross_exposure, 0.50, places=6)

        # Halted state: flattened to 0%
        d_halt = apply_risk_limits(raw, current_drawdown=-0.16, prev_day_return=-0.04, cfg=rcfg)
        self.assertEqual(d_halt.status, "HALTED")
        self.assertEqual(d_halt.gross_exposure, 0.0)
        self.assertTrue(all(w == 0.0 for w in d_halt.adjusted_weights.values()))

    def test_paper_ledger_append_and_idempotency(self) -> None:
        """Paper ledger must log forward returns without overwriting existing dates."""
        with tempfile.TemporaryDirectory() as tmp:
            db_p = Path(tmp) / "ledger.sqlite"
            csv_p = Path(tmp) / "ledger.csv"

            r1 = record_paper_snapshot(
                as_of_date="2026-11-20",
                latest_prices={"BTC": 100_000.0, "PAXG": 2_600.0},
                target_weights={"BTC": 0.25, "PAXG": 0.25},
                risk_status="OK",
                initial_capital=100_000.0,
                one_way_cost_rate=0.001,
                db_path=db_p,
                csv_path=csv_p,
            )
            self.assertFalse(r1["already_logged"])

            # Re-logging the same date must be idempotent
            r1_dup = record_paper_snapshot(
                as_of_date="2026-11-20",
                latest_prices={"BTC": 120_000.0, "PAXG": 2_600.0},
                target_weights={"BTC": 0.25, "PAXG": 0.25},
                risk_status="OK",
                initial_capital=100_000.0,
                one_way_cost_rate=0.001,
                db_path=db_p,
                csv_path=csv_p,
            )
            self.assertTrue(r1_dup["already_logged"])
            self.assertEqual(r1_dup["equity"], r1["equity"])

            # Day 2 (+10% on BTC -> +2.5% gross on 25% weight)
            r2 = record_paper_snapshot(
                as_of_date="2026-11-21",
                latest_prices={"BTC": 110_000.0, "PAXG": 2_600.0},
                target_weights={"BTC": 0.25, "PAXG": 0.25},
                risk_status="OK",
                initial_capital=100_000.0,
                one_way_cost_rate=0.001,
                db_path=db_p,
                csv_path=csv_p,
            )
            self.assertFalse(r2["already_logged"])
            self.assertAlmostEqual(r2["daily_return"], 0.025, places=4)
            self.assertTrue(csv_p.exists())


if __name__ == "__main__":
    unittest.main()
