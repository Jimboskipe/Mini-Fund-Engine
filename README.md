# Crypto & Tokenized-Asset Mini-Fund Engine (v1)

A modular, zero-dependency Python systematic trading and risk-management engine built for **24/7 crypto spot/perpetuals and tokenized assets** (`BTC`, `ETH`, `SOL`, `PAXG`, `LINK`)—designed so you can both **actually use it** for live paper/systematic execution and **showcase it** as institutional-grade proof-of-work.

## Universe (v1 Crypto + Tokenized Macro)

| Symbol | Kraken Public Pair | Category | Role in Portfolio |
| :--- | :--- | :--- | :--- |
| `BTC` | `XXBTZUSD` | Major Crypto | Core trend & liquidity anchor |
| `ETH` | `XETHZUSD` | Major Crypto | Smart-contract beta & trend |
| `SOL` | `SOLUSD` | High-Beta Crypto | Momentum / volatility sleeve |
| `PAXG` | `PAXGUSD` | Tokenized Gold | Uncorrelated macro safe-haven (on-chain gold) |
| `LINK` | `LINKUSD` | Infra / DeFi | DeFi & tokenized-asset oracle exposure |

> **Extensible:** Add tokenized stocks (e.g., xStocks / Ondo tickers) or exchange-specific perp pairs directly in `config.py` or via custom CSV/exchange adapters.

## Architecture

```text
Mini-Fund-Engine/
├── config.py                  # 24/7 crypto universe, strategy, fee/slippage, and risk limits
├── run_engine.py              # CLI entrypoint (demo, live-fetch, paper-update)
├── src/
│   ├── data_pipeline.py       # Kraken public OHLC ingestion (no API key), data audit, SQLite DB
│   ├── strategy.py            # Multi-horizon TSMOM (trend) + 365-day EWMA volatility targeting
│   ├── risk_engine.py         # Asset caps, gross leverage cap, correlation monitor, DD kill switches
│   ├── backtest.py            # Walk-forward IS/OOS engine with explicit fees + slippage
│   └── paper_ledger.py        # Append-only timestamped forward paper-trading ledger
├── tests/
│   └── test_engine.py         # Unit tests (zero lookahead, cost drag, kill-switch behavior)
├── data/                      # Local SQLite market DB & paper ledger (created on run)
└── reports/                   # Generated Markdown tearsheets & JSON risk snapshots
```

## Quick Start (Python 3.10+, Standard Library Only)

```powershell
# 1. Run automated unit tests
python -m unittest discover -s tests -v

# 2. Run offline verification (synthetic multi-regime crypto data — proves pipeline & risk math)
python run_engine.py --mode demo

# 3. Fetch real free daily OHLCV data from Kraken (US-accessible, no API key), run IS/OOS backtest
python run_engine.py --mode live-fetch

# 4. Log today's forward paper-trading rebalance snapshot to the append-only ledger
python run_engine.py --mode paper-update
```

## Core Risk Guardrails Built Into v1

- **No lookahead bias:** Signals calculated at UTC daily close `t` apply strictly to the return from `t` to `t+1`.
- **24/7 Crypto Calendar (`365` days/year):** Volatility scaling, Sharpe, Sortino, and CAGR use 365-day annualization—not equity 252-day math.
- **Honest execution cost model:** Default `10 bps` per side (`5 bps` exchange fee + `5 bps` slippage/spread = `20 bps` round-trip) charged on every dollar of rebalance turnover.
- **In-Sample / Out-of-Sample split:** Reports IS (first 70%) and OOS (last 30%) performance side-by-side so curve-fitting is exposed immediately.
- **Portfolio-level risk controls:**
  - Annualized portfolio volatility target (`15%` target vol, realistic for a risk-managed crypto/tokenized-asset book)
  - Per-asset notional cap (`35%`) & gross leverage cap (`1.5x`)
  - Daily loss alert (`-3.0%` single day)
  - Trailing drawdown throttle (`-10%` DD cuts risk by 50%) and hard kill switch (`-15%` DD flattens book to `0%` until manual reset)

## Next Milestones (v2 Roadmap)

- [ ] Add perp funding-rate ingestion & carry-adjusted return modeling.
- [ ] Add tokenized-equity tickers (e.g., tokenized SPY/NVDA/TSLA feeds from your venue) into `UNIVERSE`.
- [ ] Wire live testnet/paper execution adapter (exchange REST API) + automated daily UTC close scheduler.
- [ ] Publish live paper equity curve & monthly risk tearsheets to GitHub Pages.
