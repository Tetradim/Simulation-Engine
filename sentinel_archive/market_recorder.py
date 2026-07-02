from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timezone
from typing import Any

from .recorder_models import (
    MarketBarRecord,
    MarketSnapshotRecord,
    ParsedAlert,
    PriceDriftEvent,
    RecorderSettings,
    normalize_contract_key,
    normalize_expiration,
    normalize_option_type,
)
from .recording_store import RecordingStore


STOCK_REQUIRED_COLUMNS = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
OPTION_REQUIRED_COLUMNS = {"timestamp", "underlying", "expiration", "strike", "option_type", "open", "high", "low", "close", "volume"}


def parse_stock_csv(csv_text: str, *, source: str = "stock_csv") -> list[MarketBarRecord]:
    rows = _csv_rows(csv_text, STOCK_REQUIRED_COLUMNS)
    bars: list[MarketBarRecord] = []
    for row in rows:
        symbol = str(row["symbol"]).strip().upper()
        bars.append(
            MarketBarRecord(
                source=source,
                instrument_type="stock",
                timestamp=_normalize_timestamp(row["timestamp"]),
                symbol=symbol,
                open=_number(row.get("open")),
                high=_number(row.get("high")),
                low=_number(row.get("low")),
                close=_number(row.get("close")),
                volume=_number(row.get("volume")) or 0.0,
                bid=_number(row.get("bid")),
                ask=_number(row.get("ask")),
                mid=_number(row.get("mid")),
                last=_number(row.get("last")),
                metadata=_metadata(row, STOCK_REQUIRED_COLUMNS),
            )
        )
    return bars


def parse_option_csv(csv_text: str, *, source: str = "option_csv") -> list[MarketBarRecord]:
    rows = _csv_rows(csv_text, OPTION_REQUIRED_COLUMNS)
    bars: list[MarketBarRecord] = []
    for row in rows:
        underlying = str(row["underlying"]).strip().upper()
        expiration = normalize_expiration(str(row["expiration"]))
        strike = float(row["strike"])
        option_type = normalize_option_type(str(row["option_type"]))
        contract_key = normalize_contract_key(underlying, expiration, strike, option_type)
        bid = _number(row.get("bid"))
        ask = _number(row.get("ask"))
        mid = _number(row.get("mid"))
        if mid is None and bid is not None and ask is not None:
            mid = round((bid + ask) / 2, 6)
        close = _number(row.get("close"))
        bars.append(
            MarketBarRecord(
                source=source,
                instrument_type="option",
                timestamp=_normalize_timestamp(row["timestamp"]),
                symbol=underlying,
                contract_key=contract_key,
                underlying=underlying,
                expiration=expiration,
                strike=strike,
                option_type=option_type,
                open=_number(row.get("open")),
                high=_number(row.get("high")),
                low=_number(row.get("low")),
                close=close,
                volume=_number(row.get("volume")) or 0.0,
                bid=bid,
                ask=ask,
                mid=mid,
                last=_number(row.get("last")) or close,
                metadata=_metadata(row, OPTION_REQUIRED_COLUMNS),
            )
        )
    return bars


def calculate_price_drift(
    *,
    alert_id: str,
    alert_price: float | None,
    market_price: float | None,
    amount_threshold: float,
    percent_threshold: float,
) -> PriceDriftEvent:
    if alert_price is None or market_price is None:
        return PriceDriftEvent(
            alert_id=alert_id,
            alert_price=alert_price,
            market_price=market_price,
            drift_amount_threshold=amount_threshold,
            drift_percent_threshold=percent_threshold,
            drift_direction="market_price_unavailable",
            price_drift_alert=False,
        )

    amount = round(float(market_price) - float(alert_price), 6)
    percent = round((amount / float(alert_price)) * 100, 6) if float(alert_price) else None
    if amount > 0:
        direction = "market_above_alert"
    elif amount < 0:
        direction = "market_below_alert"
    else:
        direction = "no_drift"
    price_drift_alert = abs(amount) >= amount_threshold or (percent is not None and abs(percent) >= percent_threshold)

    return PriceDriftEvent(
        alert_id=alert_id,
        alert_price=float(alert_price),
        market_price=float(market_price),
        price_drift_amount=amount,
        price_drift_pct=percent,
        drift_amount_threshold=amount_threshold,
        drift_percent_threshold=percent_threshold,
        drift_direction=direction,
        price_drift_alert=price_drift_alert,
    )


async def create_snapshot_for_alert(
    store: RecordingStore,
    alert: ParsedAlert,
    *,
    settings: RecorderSettings,
    snapshot_timestamp: str,
) -> tuple[MarketSnapshotRecord, PriceDriftEvent]:
    contract_key = alert.normalized.get("contract_key") if alert.normalized else None
    option_bar = await store.latest_market_bar(contract_key=contract_key, at_or_before=snapshot_timestamp) if contract_key else None
    stock_bar = await store.latest_market_bar(symbol=alert.ticker, at_or_before=snapshot_timestamp) if alert.ticker else None

    selected_market_price = _selected_market_price(option_bar)
    snapshot = MarketSnapshotRecord(
        alert_id=alert.message_id,
        snapshot_timestamp=_normalize_timestamp(snapshot_timestamp),
        underlying=alert.ticker,
        stock_price=_selected_market_price(stock_bar),
        option_contract_key=contract_key,
        option_bid=_float_from_bar(option_bar, "bid"),
        option_ask=_float_from_bar(option_bar, "ask"),
        option_mid=_float_from_bar(option_bar, "mid"),
        option_last=_float_from_bar(option_bar, "last"),
        selected_market_price=selected_market_price,
        price_source=str(option_bar.get("source", "csv")) if option_bar else "unavailable",
        lookup_status="matched" if option_bar else "market_price_unavailable",
    )
    drift = calculate_price_drift(
        alert_id=alert.message_id,
        alert_price=alert.alert_price,
        market_price=selected_market_price,
        amount_threshold=settings.drift_amount_threshold,
        percent_threshold=settings.drift_percent_threshold,
    )
    await store.insert_market_snapshot(snapshot)
    await store.insert_drift_event(drift)
    return snapshot, drift


class YFinanceMarketProvider:
    def __init__(self) -> None:
        self.last_error = ""

    async def latest_option_bar(self, alert: ParsedAlert, *, source: str = "yfinance") -> MarketBarRecord | None:
        if not alert.ticker or not alert.expiration or alert.strike is None or not alert.option_type:
            return None
        return await asyncio.to_thread(self._latest_option_bar_sync, alert, source)

    def _latest_option_bar_sync(self, alert: ParsedAlert, source: str) -> MarketBarRecord | None:
        try:
            import yfinance as yf

            ticker = yf.Ticker(alert.ticker)
            chain = ticker.option_chain(alert.expiration)
            table = chain.calls if alert.option_type == "CALL" else chain.puts
            if table is None or table.empty:
                return None
            ordered = table.assign(_distance=(table["strike"] - float(alert.strike)).abs()).sort_values("_distance")
            row = ordered.iloc[0].to_dict()
            bid = _number(row.get("bid"))
            ask = _number(row.get("ask"))
            mid = round((bid + ask) / 2, 6) if bid is not None and ask is not None else None
            last = _number(row.get("lastPrice"))
            contract_key = normalize_contract_key(alert.ticker, alert.expiration, float(row["strike"]), alert.option_type)
            return MarketBarRecord(
                source=source,
                instrument_type="option",
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol=alert.ticker,
                contract_key=contract_key,
                underlying=alert.ticker,
                expiration=alert.expiration,
                strike=float(row["strike"]),
                option_type=alert.option_type,
                close=last or mid,
                bid=bid,
                ask=ask,
                mid=mid,
                last=last,
                volume=_number(row.get("volume")),
                metadata={"contractSymbol": row.get("contractSymbol")},
            )
        except Exception as exc:
            self.last_error = str(exc)
            return None


def _csv_rows(csv_text: str, required_columns: set[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(str(csv_text or "")))
    fieldnames = {str(field or "").strip() for field in reader.fieldnames or []}
    missing = sorted(required_columns - fieldnames)
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    rows = [{str(key).strip(): (value or "").strip() for key, value in row.items()} for row in reader]
    if not rows:
        raise ValueError("at least one market row is required")
    return rows


def _normalize_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _metadata(row: dict[str, str], known: set[str]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in known and value not in {"", None}}


def _selected_market_price(bar: dict[str, Any] | None) -> float | None:
    if not bar:
        return None
    for key in ("mid", "last", "close"):
        value = _float_from_bar(bar, key)
        if value is not None:
            return value
    bid = _float_from_bar(bar, "bid")
    ask = _float_from_bar(bar, "ask")
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 6)
    return bid or ask


def _float_from_bar(bar: dict[str, Any] | None, key: str) -> float | None:
    if not bar:
        return None
    value = bar.get(key)
    if value is None or value == "":
        return None
    return float(value)
