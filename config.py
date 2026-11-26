"""Central configuration for the Crypto & Tokenized-Asset Mini-Fund Engine (v1)."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
MARKET_DB_PATH = DATA_DIR / "market_data.sqlite"
PAPER_LEDGER_DB_PATH = DATA_DIR / "paper_ledger.sqlite"
PAPER_LOG_CSV_PATH = REPORTS_DIR / "paper_ledger.csv"


@dataclass(frozen=True)
class AssetSpec:
    symbol: str
    kraken_pair: str
    category: str
    instrument_note: str


UNIVERSE: Tuple[AssetSpec, ...] = (
    AssetSpec("BTC", "XXBTZUSD", "Major Crypto", "Spot / Perp anchor"),
    AssetSpec("ETH", "XETHZUSD", "Major Crypto", "Smart-contract L1"),
    AssetSpec("SOL", "SOLUSD", "High-Beta Crypto", "Momentum / high-vol L1"),
    AssetSpec("PAXG", "PAXGUSD", "Tokenized Gold", "On-chain macro hedge (1 oz gold)"),
    AssetSpec("LINK", "LINKUSD", "DeFi / RWA Infra", "Oracle / tokenized-asset infra"),
)


@dataclass(frozen=True)
class StrategyConfig:
    lookbacks: Tuple[int, ...] = (14, 30, 90)  # Faster horizons suited for 24/7 crypto regimes
    ewma_vol_span: int = 20
    min_vol_floor: float = 0.10  # 10% annualized floor (prevents over-sizing PAXG during quiet periods)
    trading_days_per_year: int = 365
    allow_short: bool = True     # Set False for spot-only long/cash mode


@dataclass(frozen=True)
class RiskConfig:
    initial_capital: float = 100_000.0
    target_annual_vol: float = 0.15        # 15% portfolio annualized vol target
    max_asset_weight: float = 0.35         # 35% max single-asset notional
    max_gross_leverage: float = 1.50       # 1.5x max total gross exposure
    daily_loss_alert: float = -0.03        # -3.0% single-day loss alert
    dd_throttle_threshold: float = -0.10   # -10.0% drawdown -> cut risk to 50%
    dd_throttle_scale: float = 0.50
    dd_halt_threshold: float = -0.15       # -15.0% drawdown -> flatten to 0%
    corr_window: int = 60
    high_corr_alert: float = 0.80


@dataclass(frozen=True)
class CostConfig:
    exchange_fee_bps: float = 5.0          # 5 bps taker/blended fee per side
    slippage_bps: float = 5.0              # 5 bps spread + impact per side
    # Total round-trip execution drag = 20 bps

    @property
    def one_way_rate(self) -> float:
        return (self.exchange_fee_bps + self.slippage_bps) / 10_000.0


@dataclass(frozen=True)
class DataQualityConfig:
    max_single_day_abs_return: float = 0.50  # Flag >50% single-day jump for audit
    min_history_bars: int = 180


@dataclass(frozen=True)
class EngineConfig:
    universe: Tuple[AssetSpec, ...] = UNIVERSE
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    data_quality: DataQualityConfig = field(default_factory=DataQualityConfig)
    in_sample_fraction: float = 0.70

    @property
    def symbols(self) -> Tuple[str, ...]:
        return tuple(a.symbol for a in self.universe)

    @property
    def asset_map(self) -> Dict[str, AssetSpec]:
        return {a.symbol: a for a in self.universe}
