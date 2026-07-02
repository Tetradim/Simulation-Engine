from __future__ import annotations

import csv
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterable
from uuid import uuid4

import aiosqlite

from .recorder_models import (
    DiscordMessageRecord,
    DiscordSource,
    ExportRecord,
    MarketBarRecord,
    MarketSnapshotRecord,
    ParsedAlert,
    PriceDriftEvent,
    RecorderSettings,
    RecordingSession,
    normalize_channel_ids,
)


class RecordingStore:
    def __init__(self, db_path: str | Path = "data/sentinel_archive.sqlite3"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS recorder_settings (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS discord_sources (
                    channel_id TEXT PRIMARY KEY,
                    channel_name TEXT,
                    guild_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    data TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recording_sessions (
                    session_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    stopped_at TEXT,
                    data TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS discord_messages (
                    message_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    channel_name TEXT,
                    guild_id TEXT,
                    guild_name TEXT,
                    author_id TEXT,
                    author_name TEXT,
                    discord_timestamp TEXT NOT NULL,
                    engine_received_timestamp TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_discord_messages_channel_time
                    ON discord_messages(channel_id, discord_timestamp);

                CREATE TABLE IF NOT EXISTS parsed_alerts (
                    message_id TEXT PRIMARY KEY,
                    parse_status TEXT NOT NULL,
                    action TEXT,
                    ticker TEXT,
                    expiration TEXT,
                    strike REAL,
                    option_type TEXT,
                    contract_key TEXT,
                    alert_price REAL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_parsed_alerts_contract
                    ON parsed_alerts(contract_key);

                CREATE TABLE IF NOT EXISTS market_bars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    instrument_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    contract_key TEXT,
                    underlying TEXT,
                    timestamp TEXT NOT NULL,
                    close REAL,
                    bid REAL,
                    ask REAL,
                    mid REAL,
                    last REAL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_market_bars_contract_time
                    ON market_bars(contract_key, timestamp);
                CREATE INDEX IF NOT EXISTS idx_market_bars_symbol_time
                    ON market_bars(symbol, timestamp);

                CREATE TABLE IF NOT EXISTS market_snapshots (
                    alert_id TEXT PRIMARY KEY,
                    snapshot_timestamp TEXT NOT NULL,
                    option_contract_key TEXT,
                    lookup_status TEXT NOT NULL,
                    data TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS price_drift_events (
                    alert_id TEXT PRIMARY KEY,
                    price_drift_alert INTEGER NOT NULL DEFAULT 0,
                    drift_direction TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_price_drift_events_alert
                    ON price_drift_events(price_drift_alert);

                CREATE TABLE IF NOT EXISTS exports (
                    export_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    channel_id TEXT,
                    format TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    data TEXT NOT NULL
                );
                """
            )
            await conn.commit()

    async def get_settings(self, *, mask_token: bool = True) -> RecorderSettings:
        async with self._connect() as conn:
            async with conn.execute("SELECT data FROM recorder_settings WHERE id = ?", ("main",)) as cur:
                row = await cur.fetchone()
        settings = RecorderSettings(**json.loads(row["data"])) if row else RecorderSettings()
        return settings.masked() if mask_token else settings

    async def save_settings(self, settings: RecorderSettings) -> RecorderSettings:
        async with self._connect() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO recorder_settings (id, data) VALUES (?, ?)",
                ("main", settings.model_dump_json()),
            )
            await conn.commit()
        return settings.masked()

    async def upsert_source(self, source: DiscordSource) -> None:
        async with self._connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO discord_sources
                (channel_id, channel_name, guild_id, enabled, data)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source.channel_id,
                    source.channel_name,
                    source.guild_id,
                    1 if source.enabled else 0,
                    source.model_dump_json(),
                ),
            )
            await conn.commit()

    async def list_sources(self) -> list[dict[str, Any]]:
        return await self._list_json("discord_sources", "channel_id", 500)

    async def insert_session(self, session: RecordingSession) -> None:
        async with self._connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO recording_sessions
                (session_id, started_at, stopped_at, data)
                VALUES (?, ?, ?, ?)
                """,
                (session.session_id, session.started_at, session.stopped_at, session.model_dump_json()),
            )
            await conn.commit()

    async def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._list_json("recording_sessions", "started_at", limit)

    async def insert_message(self, message: DiscordMessageRecord) -> bool:
        async with self._connect() as conn:
            cursor = await conn.execute(
                """
                INSERT OR IGNORE INTO discord_messages
                (message_id, channel_id, channel_name, guild_id, guild_name, author_id, author_name,
                 discord_timestamp, engine_received_timestamp, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.channel_id,
                    message.channel_name,
                    message.guild_id,
                    message.guild_name,
                    message.author_id,
                    message.author_name,
                    message.discord_timestamp,
                    message.engine_received_timestamp,
                    message.model_dump_json(),
                ),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def insert_parsed_alert(self, alert: ParsedAlert) -> None:
        contract_key = alert.normalized.get("contract_key") if alert.normalized else None
        async with self._connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO parsed_alerts
                (message_id, parse_status, action, ticker, expiration, strike, option_type,
                 contract_key, alert_price, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.message_id,
                    alert.parse_status,
                    alert.action,
                    alert.ticker,
                    alert.expiration,
                    alert.strike,
                    alert.option_type,
                    contract_key,
                    alert.alert_price,
                    alert.model_dump_json(),
                ),
            )
            await conn.commit()

    async def insert_market_bars(self, bars: Iterable[MarketBarRecord]) -> int:
        rows = [
            (
                bar.source,
                bar.instrument_type,
                bar.symbol,
                bar.contract_key,
                bar.underlying,
                bar.timestamp,
                bar.close,
                bar.bid,
                bar.ask,
                bar.mid,
                bar.last,
                bar.model_dump_json(),
            )
            for bar in bars
        ]
        if not rows:
            return 0
        async with self._connect() as conn:
            await conn.executemany(
                """
                INSERT INTO market_bars
                (source, instrument_type, symbol, contract_key, underlying, timestamp,
                 close, bid, ask, mid, last, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await conn.commit()
        return len(rows)

    async def insert_market_snapshot(self, snapshot: MarketSnapshotRecord) -> None:
        async with self._connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO market_snapshots
                (alert_id, snapshot_timestamp, option_contract_key, lookup_status, data)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.alert_id,
                    snapshot.snapshot_timestamp,
                    snapshot.option_contract_key,
                    snapshot.lookup_status,
                    snapshot.model_dump_json(),
                ),
            )
            await conn.commit()

    async def insert_drift_event(self, event: PriceDriftEvent) -> None:
        async with self._connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO price_drift_events
                (alert_id, price_drift_alert, drift_direction, data)
                VALUES (?, ?, ?, ?)
                """,
                (
                    event.alert_id,
                    1 if event.price_drift_alert else 0,
                    event.drift_direction,
                    event.model_dump_json(),
                ),
            )
            await conn.commit()

    async def latest_market_bar(
        self,
        *,
        symbol: str | None = None,
        contract_key: str | None = None,
        at_or_before: str | None = None,
    ) -> dict[str, Any] | None:
        if not symbol and not contract_key:
            raise ValueError("symbol or contract_key is required")
        where = ["timestamp <= ?"] if at_or_before else []
        params: list[Any] = [at_or_before] if at_or_before else []
        if contract_key:
            where.append("contract_key = ?")
            params.append(contract_key)
        else:
            where.append("symbol = ?")
            params.append(str(symbol).upper())

        async with self._connect() as conn:
            async with conn.execute(
                f"""
                SELECT data FROM market_bars
                WHERE {' AND '.join(where)}
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                tuple(params),
            ) as cur:
                row = await cur.fetchone()
        return json.loads(row["data"]) if row else None

    async def list_messages(
        self,
        limit: int = 100,
        channel_id: str | None = None,
        channel_ids: Any = None,
    ) -> list[dict[str, Any]]:
        channels = normalize_channel_ids([channel_id, channel_ids])
        return await self._list_json("discord_messages", "discord_timestamp", limit, channel_ids=channels)

    async def list_alerts(
        self,
        limit: int = 100,
        channel_id: str | None = None,
        channel_ids: Any = None,
    ) -> list[dict[str, Any]]:
        channels = normalize_channel_ids([channel_id, channel_ids])
        channel_filter, params = _channel_filter_sql("m.channel_id", channels)
        where = f"WHERE {channel_filter}" if channel_filter else ""
        params.append(int(limit))
        async with self._connect() as conn:
            async with conn.execute(
                f"""
                SELECT a.data
                FROM parsed_alerts a
                JOIN discord_messages m ON m.message_id = a.message_id
                {where}
                ORDER BY m.discord_timestamp DESC
                LIMIT ?
                """,
                tuple(params),
            ) as cur:
                rows = await cur.fetchall()
        return [json.loads(row["data"]) for row in rows]

    async def list_market_bars(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._list_json("market_bars", "timestamp", limit)

    async def list_market_snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._list_json("market_snapshots", "snapshot_timestamp", limit)

    async def list_drift_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._list_json("price_drift_events", "alert_id", limit)

    async def list_exports(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._list_json("exports", "created_at", limit)

    async def joined_alert_records(
        self,
        *,
        channel_id: str | None = None,
        channel_ids: Any = None,
        limit: int = 1000,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        channels = normalize_channel_ids([channel_id, channel_ids])
        channel_filter, channel_params = _channel_filter_sql("m.channel_id", channels)
        if channel_filter:
            where.append(channel_filter)
            params.extend(channel_params)
        if since:
            where.append("m.discord_timestamp >= ?")
            params.append(since)
        params.append(int(limit))
        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        async with self._connect() as conn:
            async with conn.execute(
                f"""
                SELECT
                    m.data AS message_data,
                    a.data AS alert_data,
                    s.data AS snapshot_data,
                    d.data AS drift_data
                FROM parsed_alerts a
                JOIN discord_messages m ON m.message_id = a.message_id
                LEFT JOIN market_snapshots s ON s.alert_id = a.message_id
                LEFT JOIN price_drift_events d ON d.alert_id = a.message_id
                {where_clause}
                ORDER BY m.discord_timestamp ASC, m.engine_received_timestamp ASC
                LIMIT ?
                """,
                tuple(params),
            ) as cur:
                rows = await cur.fetchall()

        records: list[dict[str, Any]] = []
        for row in rows:
            message = json.loads(row["message_data"])
            alert = json.loads(row["alert_data"])
            snapshot = json.loads(row["snapshot_data"]) if row["snapshot_data"] else None
            drift = json.loads(row["drift_data"]) if row["drift_data"] else None
            records.append(
                {
                    "message": message,
                    "alert": alert,
                    "market_snapshot": snapshot,
                    "price_drift": drift,
                    "timestamp": message.get("discord_timestamp") or message.get("engine_received_timestamp") or "",
                    "channel_id": message.get("channel_id", ""),
                }
            )
        return records

    async def export_alerts(
        self,
        output_root: str | Path = "data/recordings",
        *,
        channel_id: str | None = None,
        channel_ids: Any = None,
        created_at: str | None = None,
        format: str = "csv",
        export_type: str = "alerts",
    ) -> ExportRecord:
        if format != "csv":
            raise ValueError("only csv exports are currently supported")
        if export_type not in {"alerts", "joined"}:
            raise ValueError("export_type must be alerts or joined")

        channels = normalize_channel_ids([channel_id, channel_ids])
        rows = await self._joined_alert_rows(channel_ids=channels, include_market=export_type == "joined")
        export_time = _parse_datetime(created_at)
        channel_meta = _channel_meta(rows, channels)
        folder = (
            Path(output_root)
            / export_time.strftime("%Y-%m-%d")
            / f"channel-{channel_meta['channel_id']}-{_safe_slug(channel_meta['channel_name'])}"
        )
        folder.mkdir(parents=True, exist_ok=True)
        suffix = "joined-alerts" if export_type == "joined" else "alerts"
        file_path = folder / f"{export_time.strftime('%Y%m%d-%H%M%S')}-{suffix}.csv"
        fields = [
            "discord_timestamp",
            "engine_received_timestamp",
            "channel_id",
            "channel_name",
            "guild_id",
            "guild_name",
            "author_id",
            "author_name",
            "message_id",
            "content",
            "parse_status",
            "parse_error",
            "action",
            "ticker",
            "expiration",
            "strike",
            "option_type",
            "alert_price",
            "sell_percentage",
            "confidence",
            "contract_key",
            "raw_text",
        ]
        if export_type == "joined":
            fields.extend(
                [
                    "session_id",
                    "snapshot_timestamp",
                    "underlying",
                    "stock_price",
                    "option_contract_key",
                    "option_bid",
                    "option_ask",
                    "option_mid",
                    "option_last",
                    "selected_market_price",
                    "price_source",
                    "lookup_status",
                    "market_price",
                    "price_drift_amount",
                    "price_drift_pct",
                    "drift_amount_threshold",
                    "drift_percent_threshold",
                    "drift_direction",
                    "price_drift_alert",
                ]
            )
        with file_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fields})

        record = ExportRecord(
            export_id=f"export-{uuid4().hex[:12]}",
            created_at=export_time.isoformat(),
            channel_id=channel_meta["channel_id"],
            channel_name=channel_meta["channel_name"],
            format="csv",
            file_path=str(file_path),
            row_count=len(rows),
            filters={"channel_id": channels[0] if len(channels) == 1 else None, "channel_ids": channels, "export_type": export_type},
        )
        await self.insert_export_record(record)
        return record

    async def _joined_alert_rows(self, *, channel_ids: Any = None, include_market: bool = False) -> list[dict[str, Any]]:
        records = await self.joined_alert_records(channel_ids=channel_ids, limit=100000)
        flattened: list[dict[str, Any]] = []
        for record in records:
            message = record["message"]
            alert = record["alert"]
            snapshot = record.get("market_snapshot") or {}
            drift = record.get("price_drift") or {}
            normalized = alert.get("normalized") or {}
            row = {
                **{key: message.get(key) for key in [
                    "discord_timestamp",
                    "engine_received_timestamp",
                    "session_id",
                    "channel_id",
                    "channel_name",
                    "guild_id",
                    "guild_name",
                    "author_id",
                    "author_name",
                    "message_id",
                    "content",
                ]},
                **{key: alert.get(key) for key in [
                    "parse_status",
                    "parse_error",
                    "action",
                    "ticker",
                    "expiration",
                    "strike",
                    "option_type",
                    "alert_price",
                    "sell_percentage",
                    "confidence",
                    "raw_text",
                ]},
                "contract_key": normalized.get("contract_key", ""),
            }
            if include_market:
                row.update(
                    {
                        **{key: snapshot.get(key) for key in [
                            "snapshot_timestamp",
                            "underlying",
                            "stock_price",
                            "option_contract_key",
                            "option_bid",
                            "option_ask",
                            "option_mid",
                            "option_last",
                            "selected_market_price",
                            "price_source",
                            "lookup_status",
                        ]},
                        **{key: drift.get(key) for key in [
                            "market_price",
                            "price_drift_amount",
                            "price_drift_pct",
                            "drift_amount_threshold",
                            "drift_percent_threshold",
                            "drift_direction",
                            "price_drift_alert",
                        ]},
                    }
                )
            flattened.append(row)
        return flattened

    async def insert_export_record(self, record: ExportRecord) -> None:
        async with self._connect() as conn:
            await conn.execute(
                """
                INSERT INTO exports
                (export_id, created_at, channel_id, format, file_path, row_count, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.export_id,
                    record.created_at,
                    record.channel_id,
                    record.format,
                    record.file_path,
                    record.row_count,
                    record.model_dump_json(),
                ),
            )
            await conn.commit()

    async def _list_json(
        self,
        table: str,
        order_col: str,
        limit: int,
        *,
        channel_ids: Any = None,
    ) -> list[dict[str, Any]]:
        if table not in {
            "discord_sources",
            "recording_sessions",
            "discord_messages",
            "parsed_alerts",
            "market_bars",
            "market_snapshots",
            "price_drift_events",
            "exports",
        }:
            raise ValueError("unsupported table")
        if not re.fullmatch(r"[a-z_]+", order_col):
            raise ValueError("unsupported order column")

        channels = normalize_channel_ids(channel_ids)
        channel_filter, params = _channel_filter_sql("channel_id", channels) if table == "discord_messages" else ("", [])
        where = f"WHERE {channel_filter}" if channel_filter else ""
        params.append(int(limit))
        async with self._connect() as conn:
            async with conn.execute(
                f"SELECT data FROM {table} {where} ORDER BY {order_col} DESC LIMIT ?",
                tuple(params),
            ) as cur:
                rows = await cur.fetchall()
        return [json.loads(row["data"]) for row in rows]

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_slug(value: str | None) -> str:
    raw = (value or "unknown").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug or "unknown"


def _channel_filter_sql(column: str, channel_ids: list[str]) -> tuple[str, list[Any]]:
    if not channel_ids:
        return "", []
    placeholders = ", ".join("?" for _ in channel_ids)
    return f"{column} IN ({placeholders})", list(channel_ids)


def _channel_meta(rows: list[dict[str, Any]], channel_ids: list[str]) -> dict[str, str]:
    if len(channel_ids) == 1:
        channel_id = channel_ids[0]
        row = next((item for item in rows if str(item.get("channel_id", "")) == channel_id), None)
        return {
            "channel_id": channel_id,
            "channel_name": str((row or {}).get("channel_name") or "unknown"),
        }

    if len(channel_ids) > 1:
        return {"channel_id": "-".join(channel_ids), "channel_name": "multi-channels"}

    row_channels: list[str] = []
    for row in rows:
        channel_id = str(row.get("channel_id") or "").strip()
        if channel_id and channel_id not in row_channels:
            row_channels.append(channel_id)

    if len(row_channels) == 1:
        first = rows[0]
        return {
            "channel_id": row_channels[0],
            "channel_name": str(first.get("channel_name") or "unknown"),
        }

    return {"channel_id": "all", "channel_name": "all-channels"}
