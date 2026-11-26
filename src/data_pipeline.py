"""Market data ingestion (Kraken public OHLC + CSV + synthetic), auditing, and SQLite storage."""

from __future__ import annotations

import csv
import json
import math
import random
import sqlite3
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from config import AssetSpec, DataQualityConfig, MARKET_DB_PATH


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class DataAuditReport:
    symbol: str
    total_bars: int
    start_date: str
    end_date: str
    invalid_rows_dropped: int
    spike_flags: List[Tuple[str, float]]
    passed: bool


def init_market_db(db_path: Path = MARKET_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (symbol, trade_date)
            )
            """
        )
        conn.commit()


def fetch_kraken_daily_ohlc(asset: AssetSpec, timeout: int = 15) -> List[Bar]:
    """
    Fetch free daily (1440m) OHLCV bars from Kraken public REST API (up to ~720 days, no API key).
    Drops the still-open current UTC day candle to prevent partial-bar lookahead.
    """
    url = f"https://api.kraken.com/0/public/OHLC?pair={asset.kraken_pair}&interval=1440"
    req = urllib.request.Request(url, headers={"User-Agent": "MiniFundEngine/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed fetching {asset.symbol} from Kraken ({url}): {exc}") from exc

    if payload.get("error"):
        raise RuntimeError(f"Kraken API returned error for {asset.symbol}: {payload['error']}")

    result = payload.get("result", {})
    pair_key = next((k for k in result.keys() if k != "last"), None)
    if not pair_key:
        raise RuntimeError(f"No OHLC series found in Kraken response for {asset.symbol}")

    today_utc = datetime.now(timezone.utc).date().isoformat()
    bars: List[Bar] = []
    for row in result[pair_key]:
        # Kraken format: [time, open, high, low, close, vwap, volume, count]
        ts = int(row[0])
        bar_date = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        if bar_date >= today_utc:
            # Exclude incomplete in-progress UTC candle
            continue
        o, h, l, c, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[6])
        if c <= 0 or o <= 0 or h < l:
            continue
        bars.append(Bar(date=bar_date, open=o, high=h, low=l, close=c, volume=v))

    bars.sort(key=lambda b: b.date)
    return bars


def load_csv_bars(csv_path: Path) -> List[Bar]:
    """Load custom OHLCV bars from a CSV file (Date,Open,High,Low,Close,Volume) for tokenized stocks etc."""
    bars: List[Bar] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("Date") or not row.get("Close"):
                continue
            try:
                o = float(row["Open"])
                h = float(row["High"])
                l = float(row["Low"])
                c = float(row["Close"])
                v = float(row.get("Volume") or 0.0)
            except ValueError:
                continue
            if c <= 0 or o <= 0 or h < l:
                continue
            bars.append(Bar(date=row["Date"].strip(), open=o, high=h, low=l, close=c, volume=v))
    bars.sort(key=lambda b: b.date)
    return bars


def generate_synthetic_universe(
    assets: Sequence[AssetSpec],
    num_days: int = 1095,
    seed: int = 42,
    start_day: date = date(2023, 1, 1),
) -> Dict[str, List[Bar]]:
    """Generate deterministic 24/7 multi-regime crypto + tokenized-gold bars for offline testing."""
    rng = random.Random(seed)
    # (annual_vol_365, base_drift, starting_price, crypto_beta)
    profiles = {
        "BTC": (0.55, 0.18, 28_000.0, 1.00),
        "ETH": (0.65, 0.14, 1_800.0, 0.90),
        "SOL": (0.85, 0.22, 40.0, 0.85),
        "PAXG": (0.14, 0.07, 1_950.0, 0.05),   # Tokenized gold: low correlation to crypto beta
        "LINK": (0.75, 0.12, 8.0, 0.80),
    }

    trading_dates = [(start_day + timedelta(days=i)).isoformat() for i in range(num_days)]

    # Shared crypto market factor + regime waves (bull, bear, chop)
    crypto_regime: List[float] = []
    crypto_factor: List[float] = []
    regime = 1.0
    for i in range(num_days):
        if i % 110 == 0 and i > 0:
            regime = rng.choice([-1.0, -0.5, 0.0, 0.8, 1.2])
        crypto_regime.append(regime)
        crypto_factor.append(rng.gauss(0.0, 0.45 / math.sqrt(365.0)))

    out: Dict[str, List[Bar]] = {}
    for idx, asset in enumerate(assets):
        ann_vol, base_drift, px, beta = profiles.get(asset.symbol, (0.60, 0.10, 100.0, 0.7))
        daily_vol = ann_vol / math.sqrt(365.0)
        idio_vol = daily_vol * math.sqrt(max(0.15, 1.0 - 0.5 * beta * beta))
        bars: List[Bar] = []
        asset_trend = 1.0 if idx % 2 == 0 else -1.0
        for i, d_str in enumerate(trading_dates):
            if i % (80 + idx * 19) == 0 and i > 0:
                asset_trend = rng.choice([-1.0, 1.0])
            drift_component = (
                (base_drift / 365.0)
                + 0.0007 * asset_trend
                + 0.0008 * beta * crypto_regime[i]
            )
            shock = beta * crypto_factor[i] + rng.gauss(0.0, idio_vol)
            ret = max(-0.30, min(0.30, drift_component + shock))
            open_px = px
            close_px = max(0.5, px * (1.0 + ret))
            high_px = max(open_px, close_px) * (1.0 + abs(rng.gauss(0.0, daily_vol * 0.4)))
            low_px = min(open_px, close_px) * (1.0 - abs(rng.gauss(0.0, daily_vol * 0.4)))
            vol = round(50_000_000 * (1.0 + abs(ret) * 12.0), 2)
            bars.append(
                Bar(
                    date=d_str,
                    open=round(open_px, 4),
                    high=round(high_px, 4),
                    low=round(max(0.1, low_px), 4),
                    close=round(close_px, 4),
                    volume=vol,
                )
            )
            px = close_px
        out[asset.symbol] = bars
    return out


def audit_and_clean_bars(
    symbol: str,
    bars: Sequence[Bar],
    dq: DataQualityConfig,
) -> Tuple[List[Bar], DataAuditReport]:
    """Validate OHLCV series, drop bad/duplicate rows, and flag extreme daily spikes."""
    cleaned: List[Bar] = []
    seen_dates = set()
    dropped = 0
    spikes: List[Tuple[str, float]] = []

    for bar in sorted(bars, key=lambda b: b.date):
        if bar.date in seen_dates:
            dropped += 1
            continue
        if bar.close <= 0 or bar.open <= 0 or bar.high < bar.low:
            dropped += 1
            continue
        if cleaned:
            prev_close = cleaned[-1].close
            daily_ret = (bar.close / prev_close) - 1.0
            if abs(daily_ret) > dq.max_single_day_abs_return:
                spikes.append((bar.date, round(daily_ret, 4)))
        cleaned.append(bar)
        seen_dates.add(bar.date)

    passed = len(cleaned) >= dq.min_history_bars
    report = DataAuditReport(
        symbol=symbol,
        total_bars=len(cleaned),
        start_date=cleaned[0].date if cleaned else "N/A",
        end_date=cleaned[-1].date if cleaned else "N/A",
        invalid_rows_dropped=dropped,
        spike_flags=spikes,
        passed=passed,
    )
    return cleaned, report


def save_bars_to_db(
    bars_by_symbol: Dict[str, List[Bar]],
    source: str,
    db_path: Path = MARKET_DB_PATH,
) -> None:
    init_market_db(db_path)
    rows = []
    for sym, bars in bars_by_symbol.items():
        for b in bars:
            rows.append((sym, b.date, b.open, b.high, b.low, b.close, b.volume, source))
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO daily_bars
            (symbol, trade_date, open, high, low, close, volume, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def load_bars_from_db(
    symbols: Sequence[str],
    db_path: Path = MARKET_DB_PATH,
) -> Dict[str, List[Bar]]:
    if not db_path.exists():
        raise FileNotFoundError(f"Market DB not found at {db_path}. Run --mode demo or --mode live-fetch first.")
    out: Dict[str, List[Bar]] = {s: [] for s in symbols}
    with closing(sqlite3.connect(db_path)) as conn:
        for sym in symbols:
            cur = conn.execute(
                """
                SELECT trade_date, open, high, low, close, volume
                FROM daily_bars
                WHERE symbol = ?
                ORDER BY trade_date ASC
                """,
                (sym,),
            )
            out[sym] = [Bar(*r) for r in cur.fetchall()]
    return out


def align_close_matrix(
    bars_by_symbol: Dict[str, List[Bar]],
    symbols: Sequence[str],
) -> Tuple[List[str], Dict[str, List[float]]]:
    """Align all symbols onto their common UTC trading-date intersection."""
    date_maps = {sym: {b.date: b.close for b in bars_by_symbol[sym]} for sym in symbols}
    common_dates = sorted(set.intersection(*(set(m.keys()) for m in date_maps.values())))
    aligned = {sym: [date_maps[sym][d] for d in common_dates] for sym in symbols}
    return common_dates, aligned
