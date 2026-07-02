from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_option_type(value: str | None) -> str | None:
    if value is None:
        return None
    upper = str(value).strip().upper()
    if upper in {"C", "CALL", "CALLS"}:
        return "CALL"
    if upper in {"P", "PUT", "PUTS"}:
        return "PUT"
    raise ValueError("option_type must be CALL or PUT")


def normalize_expiration(value: str) -> str:
    raw = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw

    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", raw)
    if not match:
        raise ValueError("expiration must be YYYY-MM-DD or M/D[/YY|YYYY]")

    month = int(match.group(1))
    day = int(match.group(2))
    year_part = match.group(3)
    if year_part is None:
        year = datetime.now(timezone.utc).year
    else:
        year = int(year_part)
        if year < 100:
            year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_contract_key(underlying: str, expiration: str, strike: float, option_type: str) -> str:
    clean_underlying = str(underlying).strip().upper()
    clean_expiration = normalize_expiration(expiration)
    clean_type = normalize_option_type(option_type)
    strike_value = float(strike)
    strike_text = str(int(strike_value)) if strike_value.is_integer() else str(strike_value).rstrip("0").rstrip(".")
    return f"{clean_underlying}|{clean_expiration}|{strike_text}|{clean_type}"


def normalize_channel_ids(value: Any) -> list[str]:
    ids: list[str] = []

    def collect(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            for part in re.split(r"[\s,;]+", item):
                clean = part.strip()
                if clean and clean not in ids:
                    ids.append(clean)
            return
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                collect(nested)
            return
        clean = str(item).strip()
        if clean and clean not in ids:
            ids.append(clean)

    collect(value)
    return ids


class RecorderSettings(BaseModel):
    discord_token: str = ""
    discord_channel_ids: list[str] = Field(default_factory=list)
    drift_amount_threshold: float = Field(default=0.05, ge=0.0)
    drift_percent_threshold: float = Field(default=10.0, ge=0.0)
    yfinance_enabled: bool = False
    record_all_channels: bool = False

    @field_validator("discord_channel_ids", mode="before")
    @classmethod
    def validate_channel_ids(cls, value: Any) -> list[str]:
        return normalize_channel_ids(value)

    def masked(self) -> "RecorderSettings":
        data = self.model_dump()
        if data.get("discord_token"):
            data["discord_token"] = "********"
        return RecorderSettings(**data)


class DiscordSource(BaseModel):
    channel_id: str
    channel_name: str = ""
    guild_id: str = ""
    guild_name: str = ""
    enabled: bool = True
    allowed_author_ids: list[str] = Field(default_factory=list)
    ignored_author_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class DiscordMessageRecord(BaseModel):
    message_id: str
    session_id: str | None = None
    channel_id: str
    channel_name: str = ""
    guild_id: str = ""
    guild_name: str = ""
    author_id: str = ""
    author_name: str = ""
    discord_timestamp: str
    engine_received_timestamp: str = Field(default_factory=utc_now_iso)
    content: str = ""
    embeds: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ParsedAlert(BaseModel):
    message_id: str
    parse_status: Literal["parsed", "unparsed", "error"]
    raw_text: str
    parse_error: str = ""
    action: str | None = None
    ticker: str | None = None
    expiration: str | None = None
    strike: float | None = None
    option_type: str | None = None
    alert_price: float | None = None
    sell_percentage: float | None = None
    confidence: str = "none"
    parser_profile: str = "sentinel_echo_default"
    normalized: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: Any) -> str | None:
        if value is None or value == "":
            return None
        return str(value).strip().upper()

    @field_validator("option_type", mode="before")
    @classmethod
    def validate_option_type(cls, value: Any) -> str | None:
        return normalize_option_type(value)


class MarketBarRecord(BaseModel):
    source: str = "csv"
    instrument_type: Literal["stock", "option"]
    timestamp: str
    symbol: str
    contract_key: str | None = None
    underlying: str | None = None
    expiration: str | None = None
    strike: float | None = None
    option_type: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    last: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol", "underlying", mode="before")
    @classmethod
    def normalize_symbol(cls, value: Any) -> str | None:
        if value is None:
            return None
        return str(value).strip().upper()


class MarketSnapshotRecord(BaseModel):
    alert_id: str
    snapshot_timestamp: str
    underlying: str | None = None
    stock_price: float | None = None
    option_contract_key: str | None = None
    option_bid: float | None = None
    option_ask: float | None = None
    option_mid: float | None = None
    option_last: float | None = None
    selected_market_price: float | None = None
    price_source: str = "unavailable"
    lookup_status: str = "market_price_unavailable"


class PriceDriftEvent(BaseModel):
    alert_id: str
    alert_price: float | None = None
    market_price: float | None = None
    price_drift_amount: float | None = None
    price_drift_pct: float | None = None
    drift_amount_threshold: float
    drift_percent_threshold: float
    drift_direction: str = "market_price_unavailable"
    price_drift_alert: bool = False


class RecordingSession(BaseModel):
    session_id: str
    started_at: str = Field(default_factory=utc_now_iso)
    stopped_at: str | None = None
    channel_ids: list[str] = Field(default_factory=list)
    source: str = "manual"
    notes: str = ""

    @field_validator("channel_ids", mode="before")
    @classmethod
    def validate_channel_ids(cls, value: Any) -> list[str]:
        return normalize_channel_ids(value)


class ExportRecord(BaseModel):
    export_id: str
    created_at: str = Field(default_factory=utc_now_iso)
    channel_id: str = ""
    channel_name: str = ""
    format: Literal["csv", "jsonl"] = "csv"
    file_path: str
    row_count: int = 0
    filters: dict[str, Any] = Field(default_factory=dict)


class RecorderStatus(BaseModel):
    discord_connected: bool = False
    discord_state: str = "stopped"
    active_session_id: str | None = None
    monitored_channels: list[str] = Field(default_factory=list)
    messages_recorded: int = 0
    parsed_alerts: int = 0
    unparsed_alerts: int = 0
    drift_alerts: int = 0
    last_message_timestamp: str | None = None
    last_error: str = ""
