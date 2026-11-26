"""Append-only forward paper-trading ledger for building a verifiable live track record."""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from config import PAPER_LEDGER_DB_PATH, PAPER_LOG_CSV_PATH


def init_paper_ledger(db_path: Path = PAPER_LEDGER_DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at_utc TEXT NOT NULL,
                as_of_date TEXT NOT NULL UNIQUE,
                equity REAL NOT NULL,
                daily_return REAL NOT NULL,
                drawdown REAL NOT NULL,
                risk_status TEXT NOT NULL,
                gross_exposure REAL NOT NULL,
                net_exposure REAL NOT NULL,
                prices_json TEXT NOT NULL,
                target_weights_json TEXT NOT NULL
            )
            """
        )
        conn.commit()


def record_paper_snapshot(
    as_of_date: str,
    latest_prices: Dict[str, float],
    target_weights: Dict[str, float],
    risk_status: str,
    initial_capital: float,
    one_way_cost_rate: float,
    db_path: Path = PAPER_LEDGER_DB_PATH,
    csv_path: Path = PAPER_LOG_CSV_PATH,
) -> Dict[str, object]:
    """
    Append today's mark-to-market forward paper snapshot.
    Uses the PREVIOUS logged snapshot's weights to compute today's forward return (zero hindsight).
    If as_of_date was already logged, returns the existing immutable record without overwriting.
    """
    init_paper_ledger(db_path)
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with closing(sqlite3.connect(db_path)) as conn:
        existing = conn.execute(
            "SELECT as_of_date, equity, daily_return, drawdown, risk_status FROM paper_snapshots WHERE as_of_date = ?",
            (as_of_date,),
        ).fetchone()
        if existing:
            return {
                "already_logged": True,
                "as_of_date": existing[0],
                "equity": existing[1],
                "daily_return": existing[2],
                "drawdown": existing[3],
                "risk_status": existing[4],
            }

        prev = conn.execute(
            """
            SELECT equity, prices_json, target_weights_json
            FROM paper_snapshots
            ORDER BY as_of_date DESC
            LIMIT 1
            """
        ).fetchone()

        peak_row = conn.execute("SELECT MAX(equity) FROM paper_snapshots").fetchone()
        peak_equity = float(peak_row[0]) if peak_row and peak_row[0] is not None else initial_capital

        if prev is None:
            # First forward entry: mark initial capital, pay initial entry turnover cost
            prev_equity = initial_capital
            turnover = sum(abs(w) for w in target_weights.values())
            daily_ret = -(turnover * one_way_cost_rate)
            equity = prev_equity * (1.0 + daily_ret)
        else:
            prev_equity = float(prev[0])
            prev_prices = json.loads(prev[1])
            prev_weights = json.loads(prev[2])

            gross_ret = 0.0
            for sym, w in prev_weights.items():
                p0 = prev_prices.get(sym)
                p1 = latest_prices.get(sym)
                if p0 and p1 and p0 > 0:
                    gross_ret += w * ((p1 / p0) - 1.0)

            turnover = sum(
                abs(target_weights.get(sym, 0.0) - prev_weights.get(sym, 0.0))
                for sym in set(target_weights) | set(prev_weights)
            )
            daily_ret = gross_ret - (turnover * one_way_cost_rate)
            equity = prev_equity * max(0.0, 1.0 + daily_ret)

        peak_equity = max(peak_equity, equity)
        drawdown = (equity / peak_equity) - 1.0
        gross_exp = sum(abs(w) for w in target_weights.values())
        net_exp = sum(target_weights.values())

        conn.execute(
            """
            INSERT INTO paper_snapshots (
                logged_at_utc, as_of_date, equity, daily_return, drawdown,
                risk_status, gross_exposure, net_exposure, prices_json, target_weights_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_utc,
                as_of_date,
                round(equity, 2),
                round(daily_ret, 6),
                round(drawdown, 6),
                risk_status,
                round(gross_exp, 4),
                round(net_exp, 4),
                json.dumps(latest_prices, sort_keys=True),
                json.dumps({k: round(v, 4) for k, v in target_weights.items()}, sort_keys=True),
            ),
        )
        conn.commit()

    _export_ledger_csv(db_path, csv_path)
    return {
        "already_logged": False,
        "as_of_date": as_of_date,
        "equity": round(equity, 2),
        "daily_return": round(daily_ret, 6),
        "drawdown": round(drawdown, 6),
        "risk_status": risk_status,
    }


def _export_ledger_csv(db_path: Path, csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT logged_at_utc, as_of_date, equity, daily_return, drawdown,
                   risk_status, gross_exposure, net_exposure, target_weights_json
            FROM paper_snapshots
            ORDER BY as_of_date ASC
            """
        ).fetchall()

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "logged_at_utc",
                "as_of_date",
                "equity",
                "daily_return",
                "drawdown",
                "risk_status",
                "gross_exposure",
                "net_exposure",
                "target_weights_json",
            ]
        )
        writer.writerows(rows)
