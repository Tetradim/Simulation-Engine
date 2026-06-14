from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MarketBar(BaseModel):
    timestamp: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    vwap: float | None = None
    trade_count: int | None = None
    source: str = "user_csv"

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: Any) -> str:
        symbol = str(value or "").strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        return symbol


class SimulationConfig(BaseModel):
    starting_cash: float = 100000.0
    default_quantity: float = 10.0
    max_allocation_pct: float = Field(default=25.0, ge=0.0, le=100.0)
    fill_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    slippage_bps: float = 0.0
    commission_per_order: float = 0.0
    latency_ms: int = Field(default=0, ge=0)
    reject_below_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    default_trailing_percent: float = Field(default=2.0, gt=0.0, le=100.0)
    regular_stop_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    take_profit_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    signal_buy_threshold: float = Field(default=1.0, ge=-10.0, le=10.0)
    signal_sell_threshold: float = Field(default=-1.0, ge=-10.0, le=10.0)


class ReplaySession(BaseModel):
    session_id: str
    name: str
    source: str = "user_csv"
    symbols: list[str]
    bar_count: int
    first_timestamp: str
    last_timestamp: str


class ReplayState(BaseModel):
    active: bool = False
    session_id: str | None = None
    speed: float = 1.0
    loop: bool = False
    index: int = 0
    current_timestamp: str | None = None


class Position(BaseModel):
    symbol: str
    quantity: float
    entry_price: float
    avg_entry: float
    current_price: float
    market_price: float
    pnl: float = 0.0
    pnl_pct: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    trailing_enabled: bool = False
    trailing_percent: float | None = None
    high_water_mark: float | None = None


class AccountState(BaseModel):
    starting_cash: float
    cash: float
    total_equity: float
    account_balance: float
    buying_power: float
    available: float
    day_pnl_dollar: float
    day_pnl_pct: float
    open_positions: int
    positions: dict[str, Position] = Field(default_factory=dict)


class TickerState(BaseModel):
    symbol: str
    enabled: bool = True
    trailing_enabled: bool = False
    trailing_percent: float | None = None
    auto_stop_reason: str | None = None


class PulseHandoffDcaPlan(BaseModel):
    steps: int | None = Field(default=None, ge=1)
    interval_seconds: int | None = Field(default=None, ge=0)
    allocation_pct: float | None = Field(default=None, ge=0.0, le=100.0)

    model_config = ConfigDict(extra="allow")


class PulseHandoffRequest(BaseModel):
    contract_version: Literal["edge.pulse.handoff.v1"] = "edge.pulse.handoff.v1"
    symbol: str
    action: Literal[
        "buy",
        "sell",
        "stop_buying",
        "stop_all",
        "regular_stop",
        "trailing_stop",
        "opening_trailing_stop",
        "tighten_stop",
        "tighten_trailing_stop",
        "dca",
        "emergency_exit",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    mode: Literal["paper", "live"]
    orb_session: Literal["premarket_30m", "market_open", "puzzle_key"] = "market_open"
    stop_type: Literal["regular", "trailing", "tighten", "tighten_trailing"] | None = None
    trailing_percent: float | None = Field(default=None, gt=0.0)
    dca: PulseHandoffDcaPlan | None = None
    idempotency_key: str = Field(min_length=1)
    source: Literal["sentinel_edge"] = "sentinel_edge"
    created_at: float = Field(gt=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_handoff_symbol(cls, value: Any) -> str:
        symbol = str(value or "").strip().upper()
        if not symbol:
            raise ValueError("symbol is required")
        return symbol


class SimulationSnapshot(BaseModel):
    config: SimulationConfig
    sessions: list[ReplaySession]
    replay: ReplayState
    current_prices: dict[str, float]
    account: AccountState
    tickers: list[TickerState]
    decisions: list[dict[str, Any]]
    event_log: list[dict[str, Any]]
