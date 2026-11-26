"""CLI entrypoint for the Crypto & Tokenized-Asset Mini-Fund Engine (v1)."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from config import EngineConfig, REPORTS_DIR
from src.backtest import BacktestResult, PerformanceMetrics, run_backtest
from src.data_pipeline import (
    DataAuditReport,
    align_close_matrix,
    audit_and_clean_bars,
    fetch_kraken_daily_ohlc,
    generate_synthetic_universe,
    load_bars_from_db,
    save_bars_to_db,
)
from src.paper_ledger import record_paper_snapshot


def _fmt_metrics_row(m: PerformanceMetrics) -> str:
    return (
        f"| {m.label} | {m.start_date} -> {m.end_date} ({m.days}d) | "
        f"{m.total_return:+.1%} | {m.cagr:+.1%} | {m.annual_vol:.1%} | "
        f"{m.sharpe:.2f} | {m.sortino:.2f} | {m.max_drawdown:.1%} | "
        f"{m.calmar:.2f} | {m.total_cost_drag:.2%} |"
    )


def render_markdown_tearsheet(
    mode: str,
    audits: List[DataAuditReport],
    res: BacktestResult,
    cfg: EngineConfig,
    latest_prices: Dict[str, float],
) -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Mini-Fund Engine Tearsheet ({mode})",
        "",
        f"- **Generated:** {now_utc}",
        f"- **Universe:** {', '.join(cfg.symbols)} (365-day crypto/tokenized calendar)",
        f"- **Strategy:** Multi-Horizon TSMOM {cfg.strategy.lookbacks}d + {cfg.strategy.ewma_vol_span}d EWMA Vol Target ({cfg.risk.target_annual_vol:.0%})",
        f"- **Cost Model:** {cfg.costs.exchange_fee_bps:.1f} bps fee + {cfg.costs.slippage_bps:.1f} bps slippage per side ({cfg.costs.one_way_rate*20_000:.1f} bps round-trip)",
        f"- **Risk Status (as of {res.dates[-1]}):** `{res.final_status}` | Gross: `{res.final_gross:.2f}x` | Net: `{res.final_net:+.2f}x`",
        "",
        "## 1. In-Sample vs. Out-of-Sample Performance (Net of Costs)",
        "",
        "| Segment | Window | Total Ret | CAGR | Ann Vol | Sharpe | Sortino | Max DD | Calmar | Cost Drag |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        _fmt_metrics_row(res.full_metrics),
        _fmt_metrics_row(res.in_sample_metrics),
        _fmt_metrics_row(res.out_of_sample_metrics),
        "",
        f"## 2. Next-Session Target Portfolio (As of Close {res.dates[-1]})",
        "",
        "| Symbol | Category | Last Close | Trend Signal | Ann Vol | Target Weight | Target Notional ($100k Ref) |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for sym in cfg.symbols:
        spec = cfg.asset_map[sym]
        px = latest_prices[sym]
        sig = res.final_signals[sym]
        vol = res.final_vols[sym]
        w = res.final_weights[sym]
        notional = w * cfg.risk.initial_capital
        lines.append(
            f"| `{sym}` | {spec.category} | ${px:,.2f} | {sig:+.2f} | {vol:.1%} | {w:+.1%} | ${notional:+,.0f} |"
        )

    lines.extend(["", "## 3. Trailing 60-Day Return Correlation Matrix", ""])
    header = "| Symbol | " + " | ".join(cfg.symbols) + " |"
    sep = "| :--- | " + " | ".join("---:" for _ in cfg.symbols) + " |"
    lines.extend([header, sep])
    for s1 in cfg.symbols:
        vals = " | ".join(f"{res.correlation_matrix[s1][s2]:+.2f}" for s2 in cfg.symbols)
        lines.append(f"| `{s1}` | {vals} |")

    lines.extend(["", "## 4. Data Quality Audit", ""])
    lines.append("| Symbol | Bars | Range | Dropped | Spike Flags | Status |")
    lines.append("| :--- | ---: | :--- | ---: | ---: | :--- |")
    for a in audits:
        status = "PASS" if a.passed else "FAIL"
        lines.append(
            f"| `{a.symbol}` | {a.total_bars} | {a.start_date} -> {a.end_date} | "
            f"{a.invalid_rows_dropped} | {len(a.spike_flags)} | {status} |"
        )

    lines.extend(["", "## 5. Risk & Correlation Alerts", ""])
    if res.correlation_alerts:
        for alert in res.correlation_alerts:
            lines.append(f"- {alert}")
    else:
        lines.append("- No crowded correlation alerts above threshold.")

    if res.risk_alerts_log:
        lines.append("")
        lines.append("Recent simulation risk events:")
        for ev in res.risk_alerts_log[-10:]:
            lines.append(f"- {ev}")
    else:
        lines.append("- No kill-switch or leverage-breach events recorded.")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto & Tokenized-Asset Mini-Fund Engine (v1)")
    parser.add_argument(
        "--mode",
        choices=["demo", "live-fetch", "paper-update"],
        default="demo",
        help="demo = offline synthetic multi-regime test; live-fetch = Kraken public OHLC; paper-update = fetch & append forward ledger",
    )
    parser.add_argument(
        "--long-only",
        action="store_true",
        help="Restrict signals to long/cash only (spot-only mode, no shorting).",
    )
    args = parser.parse_args()

    cfg = EngineConfig()
    if args.long_only:
        from dataclasses import replace

        cfg = replace(cfg, strategy=replace(cfg.strategy, allow_short=False))

    if args.mode == "demo":
        raw_bars = generate_synthetic_universe(cfg.universe)
        source_label = "synthetic_demo"
    else:
        raw_bars = {}
        for asset in cfg.universe:
            print(f"[FETCH] Downloading Kraken daily OHLC for {asset.symbol} ({asset.kraken_pair})...")
            raw_bars[asset.symbol] = fetch_kraken_daily_ohlc(asset)
        source_label = "kraken_public_ohlc"

    cleaned_bars = {}
    audits: List[DataAuditReport] = []
    for sym in cfg.symbols:
        clean, report = audit_and_clean_bars(sym, raw_bars[sym], cfg.data_quality)
        cleaned_bars[sym] = clean
        audits.append(report)
        if not report.passed:
            raise RuntimeError(f"Data quality check FAILED for {sym}: only {report.total_bars} bars")

    save_bars_to_db(cleaned_bars, source=source_label)
    db_bars = load_bars_from_db(cfg.symbols)
    dates, aligned_closes = align_close_matrix(db_bars, cfg.symbols)
    latest_prices = {sym: aligned_closes[sym][-1] for sym in cfg.symbols}

    res = run_backtest(dates, aligned_closes, cfg)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    tearsheet_md = render_markdown_tearsheet(args.mode, audits, res, cfg, latest_prices)
    tearsheet_path = REPORTS_DIR / "latest_tearsheet.md"
    tearsheet_path.write_text(tearsheet_md, encoding="utf-8")

    snapshot_path = REPORTS_DIR / "latest_snapshot.json"
    snapshot_payload = {
        "mode": args.mode,
        "as_of_date": res.dates[-1],
        "risk_status": res.final_status,
        "gross_exposure": round(res.final_gross, 4),
        "net_exposure": round(res.final_net, 4),
        "latest_prices": latest_prices,
        "target_weights": {k: round(v, 4) for k, v in res.final_weights.items()},
        "full_metrics": asdict(res.full_metrics),
        "in_sample_metrics": asdict(res.in_sample_metrics),
        "out_of_sample_metrics": asdict(res.out_of_sample_metrics),
    }
    snapshot_path.write_text(json.dumps(snapshot_payload, indent=2), encoding="utf-8")

    print("\n=== MINI-FUND ENGINE SUMMARY ===")
    print(f"Mode: {args.mode} | Aligned Bars: {len(dates)} ({dates[0]} -> {dates[-1]})")
    print(
        f"In-Sample  -> CAGR: {res.in_sample_metrics.cagr:+.1%} | Sharpe: {res.in_sample_metrics.sharpe:.2f} "
        f"| MaxDD: {res.in_sample_metrics.max_drawdown:.1%}"
    )
    print(
        f"Out-Sample -> CAGR: {res.out_of_sample_metrics.cagr:+.1%} | Sharpe: {res.out_of_sample_metrics.sharpe:.2f} "
        f"| MaxDD: {res.out_of_sample_metrics.max_drawdown:.1%}"
    )
    print(f"Risk Status: {res.final_status} | Gross: {res.final_gross:.2f}x | Net: {res.final_net:+.2f}x")
    print("Next-Day Target Weights:", {k: f"{v:+.1%}" for k, v in res.final_weights.items()})
    print(f"Saved tearsheet -> {tearsheet_path}")
    print(f"Saved snapshot  -> {snapshot_path}")

    if args.mode == "paper-update":
        entry = record_paper_snapshot(
            as_of_date=res.dates[-1],
            latest_prices=latest_prices,
            target_weights=res.final_weights,
            risk_status=res.final_status,
            initial_capital=cfg.risk.initial_capital,
            one_way_cost_rate=cfg.costs.one_way_rate,
        )
        print("Paper Ledger Update:", entry)


if __name__ == "__main__":
    main()
