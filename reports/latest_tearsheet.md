# Mini-Fund Engine Tearsheet (demo)

- **Generated:** 2026-11-21 22:16 UTC
- **Universe:** BTC, ETH, SOL, PAXG, LINK (365-day crypto/tokenized calendar)
- **Strategy:** Multi-Horizon TSMOM (14, 30, 90)d + 20d EWMA Vol Target (15%)
- **Cost Model:** 5.0 bps fee + 5.0 bps slippage per side (20.0 bps round-trip)
- **Risk Status (as of 2025-12-30):** `OK` | Gross: `0.25x` | Net: `+0.16x`

## 1. In-Sample vs. Out-of-Sample Performance (Net of Costs)

| Segment | Window | Total Ret | CAGR | Ann Vol | Sharpe | Sortino | Max DD | Calmar | Cost Drag |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full Active Period | 2023-04-02 -> 2025-12-30 (1004d) | +2.9% | +1.1% | 4.4% | 0.26 | 0.38 | -8.0% | 0.13 | 4.25% |
| In-Sample (70%) | 2023-04-02 -> 2025-03-03 (702d) | -0.1% | -0.0% | 4.3% | 0.01 | 0.02 | -8.0% | -0.01 | 3.06% |
| Out-of-Sample (30%) | 2025-03-04 -> 2025-12-30 (302d) | +3.0% | +3.6% | 4.7% | 0.79 | 1.17 | -4.1% | 0.89 | 1.19% |

## 2. Next-Session Target Portfolio (As of Close 2025-12-30)

| Symbol | Category | Last Close | Trend Signal | Ann Vol | Target Weight | Target Notional ($100k Ref) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: |
| `BTC` | Major Crypto | $13,773.46 | +0.22 | 55.1% | +1.2% | $+1,197 |
| `ETH` | Major Crypto | $112.12 | -0.22 | 74.0% | -0.9% | $-894 |
| `SOL` | High-Beta Crypto | $20.15 | -0.82 | 73.4% | -3.3% | $-3,347 |
| `PAXG` | Tokenized Gold | $1,279.63 | +0.94 | 16.5% | +17.1% | $+17,089 |
| `LINK` | DeFi / RWA Infra | $3.34 | +0.43 | 61.3% | +2.1% | $+2,117 |

## 3. Trailing 60-Day Return Correlation Matrix

| Symbol | BTC | ETH | SOL | PAXG | LINK |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `BTC` | +1.00 | +0.48 | +0.30 | +0.11 | +0.32 |
| `ETH` | +0.48 | +1.00 | +0.19 | +0.03 | +0.31 |
| `SOL` | +0.30 | +0.19 | +1.00 | +0.12 | +0.36 |
| `PAXG` | +0.11 | +0.03 | +0.12 | +1.00 | +0.32 |
| `LINK` | +0.32 | +0.31 | +0.36 | +0.32 | +1.00 |

## 4. Data Quality Audit

| Symbol | Bars | Range | Dropped | Spike Flags | Status |
| :--- | ---: | :--- | ---: | ---: | :--- |
| `BTC` | 1095 | 2023-01-01 -> 2025-12-30 | 0 | 0 | PASS |
| `ETH` | 1095 | 2023-01-01 -> 2025-12-30 | 0 | 0 | PASS |
| `SOL` | 1095 | 2023-01-01 -> 2025-12-30 | 0 | 0 | PASS |
| `PAXG` | 1095 | 2023-01-01 -> 2025-12-30 | 0 | 0 | PASS |
| `LINK` | 1095 | 2023-01-01 -> 2025-12-30 | 0 | 0 | PASS |

## 5. Risk & Correlation Alerts

- No crowded correlation alerts above threshold.
- No kill-switch or leverage-breach events recorded.
