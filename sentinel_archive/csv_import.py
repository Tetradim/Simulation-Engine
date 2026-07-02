from __future__ import annotations

import csv
from io import StringIO

from .models import MarketBar

REQUIRED_COLUMNS = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}


def parse_ohlcv_csv(csv_text: str) -> list[MarketBar]:
    reader = csv.DictReader(StringIO(csv_text.strip()))
    columns = set(reader.fieldnames or [])
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")

    bars: list[MarketBar] = []
    for row_number, row in enumerate(reader, start=2):
        try:
            bars.append(
                MarketBar(
                    timestamp=str(row["timestamp"]).strip(),
                    symbol=str(row["symbol"]).strip(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    vwap=float(row["vwap"]) if row.get("vwap") else None,
                    trade_count=int(float(row["trade_count"])) if row.get("trade_count") else None,
                    source=str(row.get("source") or "user_csv"),
                )
            )
        except Exception as exc:
            raise ValueError(f"invalid OHLCV row {row_number}: {exc}") from exc

    if not bars:
        raise ValueError("CSV did not contain any OHLCV rows")

    return sorted(bars, key=lambda item: (item.timestamp, item.symbol))
