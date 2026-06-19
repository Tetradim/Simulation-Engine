from __future__ import annotations

import csv
import io
import types
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .alert_parser import parse_alert_text
from .discord_recorder import DiscordRecorder
from .market_recorder import parse_option_csv, parse_stock_csv
from .recorder_models import RecorderSettings
from .recording_store import RecordingStore


class ParsePreviewRequest(BaseModel):
    raw_text: str = Field(min_length=1)


class CsvImportRequest(BaseModel):
    csv_text: str = Field(min_length=1)


class ExportRequest(BaseModel):
    channel_id: str | None = None
    created_at: str | None = None


class IngestMessageRequest(BaseModel):
    message_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    channel_name: str = ""
    guild_id: str = ""
    guild_name: str = ""
    author_id: str = ""
    author_name: str = ""
    discord_timestamp: str = Field(min_length=1)
    content: str = ""
    embeds: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


def create_recorder_router(
    store: RecordingStore,
    recorder: DiscordRecorder,
    *,
    export_root: str | Path = "data/recordings",
) -> APIRouter:
    router = APIRouter(tags=["Recorder"])

    @router.get("/recorder/discord/settings")
    async def get_discord_settings():
        return await store.get_settings(mask_token=True)

    @router.put("/recorder/discord/settings")
    async def put_discord_settings(settings: RecorderSettings):
        existing = await store.get_settings(mask_token=False)
        if settings.discord_token == "********":
            settings.discord_token = existing.discord_token
        return await store.save_settings(settings)

    @router.post("/recorder/discord/test")
    async def test_discord_connection():
        return await recorder.test_connection()

    @router.post("/recorder/discord/start")
    async def start_discord():
        return await recorder.start()

    @router.post("/recorder/discord/stop")
    async def stop_discord():
        return await recorder.stop()

    @router.get("/recorder/discord/status")
    async def discord_status():
        return await recorder.status()

    @router.post("/recorder/discord/parse-preview")
    async def parse_preview(body: ParsePreviewRequest):
        return parse_alert_text(body.raw_text, message_id="preview")

    @router.post("/recorder/discord/ingest-message")
    @router.post("/recorder/dev/ingest-message")
    async def ingest_message(body: IngestMessageRequest):
        result = await recorder.handle_message(_fake_message(body), bot_user_id="recorder-api")
        return {"status": result}

    @router.post("/recorder/discord/import-csv")
    async def import_discord_csv(body: CsvImportRequest):
        rows = list(csv.DictReader(io.StringIO(body.csv_text)))
        if not rows:
            raise HTTPException(status_code=400, detail="at least one Discord message row is required")
        inserted = 0
        for index, row in enumerate(rows, start=1):
            request = IngestMessageRequest(
                message_id=str(row.get("message_id") or row.get("id") or f"csv-{index}"),
                channel_id=str(row.get("channel_id") or ""),
                channel_name=str(row.get("channel_name") or ""),
                guild_id=str(row.get("guild_id") or ""),
                guild_name=str(row.get("guild_name") or ""),
                author_id=str(row.get("author_id") or ""),
                author_name=str(row.get("author_name") or row.get("author") or ""),
                discord_timestamp=str(row.get("discord_timestamp") or row.get("timestamp") or ""),
                content=str(row.get("content") or row.get("message") or row.get("raw_text") or ""),
            )
            result = await recorder.handle_message(_fake_message(request), bot_user_id="recorder-api")
            if result == "recorded":
                inserted += 1
        return {"inserted": inserted, "rows": len(rows)}

    @router.post("/recorder/market/import/options-csv")
    async def import_options_csv(body: CsvImportRequest):
        try:
            bars = parse_option_csv(body.csv_text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"inserted": await store.insert_market_bars(bars)}

    @router.post("/recorder/market/import/stocks-csv")
    async def import_stocks_csv(body: CsvImportRequest):
        try:
            bars = parse_stock_csv(body.csv_text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"inserted": await store.insert_market_bars(bars)}

    @router.get("/recordings/sessions")
    async def list_sessions(limit: int = 100):
        return {"sessions": await store.list_sessions(limit)}

    @router.get("/recordings/messages")
    async def list_messages(limit: int = 100, channel_id: str | None = None):
        return {"messages": await store.list_messages(limit, channel_id=channel_id)}

    @router.get("/recordings/alerts")
    async def list_alerts(limit: int = 100, channel_id: str | None = None):
        return {"alerts": await store.list_alerts(limit, channel_id=channel_id)}

    @router.get("/recordings/market-bars")
    async def list_market_bars(limit: int = 100):
        return {"market_bars": await store.list_market_bars(limit)}

    @router.get("/recordings/market-snapshots")
    async def list_market_snapshots(limit: int = 100):
        return {"market_snapshots": await store.list_market_snapshots(limit)}

    @router.get("/recordings/drift-events")
    async def list_drift_events(limit: int = 100):
        return {"drift_events": await store.list_drift_events(limit)}

    @router.post("/recordings/export")
    async def export_recordings(body: ExportRequest):
        try:
            return await store.export_alerts(export_root, channel_id=body.channel_id, created_at=body.created_at)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/recordings/exports")
    async def list_exports(limit: int = 100):
        return {"exports": await store.list_exports(limit)}

    @router.get("/replay/events")
    async def replay_events(limit: int = 1000, channel_id: str | None = None):
        return {"events": await _replay_events(store, limit=limit, channel_id=channel_id)}

    return router


async def _replay_events(store: RecordingStore, *, limit: int, channel_id: str | None) -> list[dict[str, Any]]:
    messages = await store.list_messages(limit=limit, channel_id=channel_id)
    message_by_id = {message["message_id"]: message for message in messages}
    alerts = await store.list_alerts(limit=limit, channel_id=channel_id)
    snapshots = await store.list_market_snapshots(limit=limit)

    events: list[dict[str, Any]] = []
    for alert in alerts:
        message = message_by_id.get(alert["message_id"], {})
        if channel_id and message.get("channel_id") != channel_id:
            continue
        events.append(
            {
                "type": "discord_alert",
                "timestamp": message.get("discord_timestamp") or message.get("engine_received_timestamp") or "",
                "channel_id": message.get("channel_id", ""),
                "payload": {"message": message, "alert": alert},
            }
        )

    for snapshot in snapshots:
        message = message_by_id.get(snapshot["alert_id"], {})
        if channel_id and message.get("channel_id") != channel_id:
            continue
        if not message and channel_id:
            continue
        events.append(
            {
                "type": "market_snapshot",
                "timestamp": snapshot.get("snapshot_timestamp") or "",
                "channel_id": message.get("channel_id", ""),
                "payload": {"message": message, "snapshot": snapshot},
            }
        )

    events.sort(key=lambda item: (item["timestamp"], item["type"]))
    return events[:limit]


def _fake_message(body: IngestMessageRequest) -> Any:
    return types.SimpleNamespace(
        id=body.message_id,
        content=body.content,
        embeds=body.embeds,
        attachments=body.attachments,
        created_at=types.SimpleNamespace(isoformat=lambda: body.discord_timestamp),
        author=types.SimpleNamespace(id=body.author_id, name=body.author_name),
        channel=types.SimpleNamespace(id=body.channel_id, name=body.channel_name),
        guild=types.SimpleNamespace(id=body.guild_id, name=body.guild_name),
    )
