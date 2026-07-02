from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from .alert_parser import parse_alert_text
from .bot_event_bus import publish_event
from .discord_recorder import DiscordRecorder
from .market_recorder import parse_option_csv, parse_stock_csv
from .recorder_models import ExportRecord, RecorderSettings, normalize_channel_ids
from .recording_store import RecordingStore


class ParsePreviewRequest(BaseModel):
    raw_text: str = Field(min_length=1)


class CsvImportRequest(BaseModel):
    csv_text: str = Field(min_length=1)


class ExportRequest(BaseModel):
    channel_id: str | None = None
    channel_ids: list[str] = Field(default_factory=list)
    created_at: str | None = None
    export_type: Literal["alerts", "joined"] = "alerts"

    @field_validator("channel_ids", mode="before")
    @classmethod
    def validate_channel_ids(cls, value: Any) -> list[str]:
        return normalize_channel_ids(value)


class RecordingSessionRequest(BaseModel):
    notes: str = ""
    source: str = "manual"


class SentinelEchoTestRunRequest(BaseModel):
    name: str = Field(default="Sentinel Echo replay test", min_length=1, max_length=120)
    channel_id: str | None = None
    channel_ids: list[str] = Field(default_factory=list)
    since: str | None = None
    limit: int = Field(default=1000, ge=1, le=10000)

    @field_validator("channel_ids", mode="before")
    @classmethod
    def validate_channel_ids(cls, value: Any) -> list[str]:
        return normalize_channel_ids(value)


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
        if result == "recorded":
            publish_event(
                "simulation.recording.discord_message",
                payload=body.model_dump(mode="json"),
                correlation_id=body.message_id,
                dedupe_key=f"simulation-discord-message:{body.message_id}",
                target_bots=["sentinel-edge", "sentinel-echo"],
                trace={"recorder_result": result},
            )
        return {"status": result}

    @router.post("/recorder/discord/import-csv")
    async def import_discord_csv(body: CsvImportRequest):
        rows = list(csv.DictReader(io.StringIO(body.csv_text)))
        if not rows:
            raise HTTPException(status_code=400, detail="at least one Discord message row is required")
        inserted = 0
        failures: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            try:
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
            except ValidationError as exc:
                failures.append({"row": index, "error": str(exc)})
                continue
            result = await recorder.handle_message(_fake_message(request), bot_user_id="recorder-api")
            if result == "recorded":
                inserted += 1
            else:
                failures.append({"row": index, "error": result})
        return {"inserted": inserted, "failed": len(failures), "rows": len(rows), "errors": failures[:50]}

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

    @router.post("/recordings/sessions/start")
    async def start_recording_session(body: RecordingSessionRequest):
        session = await recorder.start_recording_session(notes=body.notes, source=body.source)
        return {"active_session_id": session.session_id, "session": session, "status": "active"}

    @router.post("/recordings/sessions/stop")
    async def stop_recording_session():
        session = await recorder.stop_recording_session()
        return {
            "active_session_id": recorder.active_session.session_id if recorder.active_session else None,
            "session": session,
            "status": "stopped" if session else "no_active_session",
        }

    @router.get("/recordings/sessions/active")
    async def active_recording_session():
        return {
            "active_session_id": recorder.active_session.session_id if recorder.active_session else None,
            "session": recorder.active_session,
        }

    @router.get("/recordings/sessions")
    async def list_sessions(limit: int = 100):
        return {"sessions": await store.list_sessions(limit)}

    @router.get("/recordings/messages")
    async def list_messages(limit: int = 100, channel_id: str | None = None, channel_ids: str | None = None):
        channels = normalize_channel_ids([channel_id, channel_ids])
        return {"messages": await store.list_messages(limit, channel_ids=channels)}

    @router.get("/recordings/alerts")
    async def list_alerts(limit: int = 100, channel_id: str | None = None, channel_ids: str | None = None):
        channels = normalize_channel_ids([channel_id, channel_ids])
        return {"alerts": await store.list_alerts(limit, channel_ids=channels)}

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
            return await store.export_alerts(
                export_root,
                channel_id=body.channel_id,
                channel_ids=body.channel_ids,
                created_at=body.created_at,
                export_type=body.export_type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/recordings/exports")
    async def list_exports(limit: int = 100):
        return {"exports": await store.list_exports(limit)}

    @router.get("/replay/events")
    async def replay_events(limit: int = 1000, channel_id: str | None = None, channel_ids: str | None = None):
        channels = normalize_channel_ids([channel_id, channel_ids])
        events = await _replay_events(store, limit=limit, channel_ids=channels)
        return _replay_session_response(events, channel_ids=channels)

    @router.get("/sentinel-echo/replay/events")
    async def sentinel_echo_replay_events(
        limit: int = 1000,
        channel_id: str | None = None,
        channel_ids: str | None = None,
        since: str | None = None,
    ):
        channels = normalize_channel_ids([channel_id, channel_ids])
        return await _sentinel_echo_replay_response(store, limit=limit, channel_ids=channels, since=since)

    @router.post("/sentinel-echo/test-runs")
    async def create_sentinel_echo_test_run(body: SentinelEchoTestRunRequest):
        channels = normalize_channel_ids([body.channel_id, body.channel_ids])
        replay = await _sentinel_echo_replay_response(store, limit=body.limit, channel_ids=channels, since=body.since)
        return await _write_sentinel_echo_test_run(
            store,
            export_root=export_root,
            name=body.name,
            channel_ids=channels,
            since=body.since,
            replay=replay,
        )

    return router


async def _replay_events(store: RecordingStore, *, limit: int, channel_ids: list[str]) -> list[dict[str, Any]]:
    channel_filter = set(channel_ids)
    messages = await store.list_messages(limit=limit, channel_ids=channel_ids)
    message_by_id = {message["message_id"]: message for message in messages}
    alerts = await store.list_alerts(limit=limit, channel_ids=channel_ids)
    snapshots = await store.list_market_snapshots(limit=limit)

    events: list[dict[str, Any]] = []
    for alert in alerts:
        message = message_by_id.get(alert["message_id"], {})
        if channel_filter and message.get("channel_id") not in channel_filter:
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
        if channel_filter and message.get("channel_id") not in channel_filter:
            continue
        if not message and channel_filter:
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


def _replay_source_day(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        timestamp = str(event.get("timestamp") or "")
        if len(timestamp) >= 10:
            return timestamp[:10]
    return None


def _replay_session_response(events: list[dict[str, Any]], *, channel_ids: list[str]) -> dict[str, Any]:
    event_types = sorted({str(event.get("type") or "") for event in events if event.get("type")})
    manifest_sha256 = _manifest_sha256(events)
    return {
        "contract_version": "simulation.replay_session.v1",
        "mode": "simulation",
        "execution": "none",
        "replay_session": {
            "contract_version": "replay_session.v1",
            "session_id": f"recorder-replay-{_replay_source_day(events) or 'empty'}",
            "source_day": _replay_source_day(events),
            "event_count": len(events),
            "event_types": event_types,
            "filters": {"channel_ids": channel_ids},
            "consumer_notes": "read-only replay data; never a live execution signal",
        },
        "manifest_hash_algorithm": "sha256",
        "manifest_sha256": manifest_sha256,
        "events": events,
    }


async def _sentinel_echo_replay_response(
    store: RecordingStore,
    *,
    limit: int,
    channel_ids: list[str],
    since: str | None,
) -> dict[str, Any]:
    records = await store.joined_alert_records(channel_ids=channel_ids, limit=limit, since=since)
    events = []
    for record in records:
        message = record["message"]
        event_id = f"discord_alert:{message.get('message_id', '')}"
        events.append(
            {
                "event_id": event_id,
                "type": "discord_alert",
                "timestamp": record["timestamp"],
                "channel_id": record["channel_id"],
                "payload": {
                    "message": message,
                    "alert": record["alert"],
                    "market_snapshot": record.get("market_snapshot"),
                    "price_drift": record.get("price_drift"),
                },
            }
        )

    next_cursor = events[-1]["timestamp"] if len(events) == limit and events else None
    manifest_sha256 = _manifest_sha256(events)
    return {
        "contract_version": "simulation.sentinel-echo.replay.v1",
        "mode": "simulation",
        "execution": "none",
        "consumer_notes": "read-only replay data; never a live execution signal",
        "event_count": len(events),
        "manifest_hash_algorithm": "sha256",
        "manifest_sha256": manifest_sha256,
        "filters": {"channel_id": channel_ids[0] if len(channel_ids) == 1 else None, "channel_ids": channel_ids, "since": since, "limit": limit},
        "next_cursor": next_cursor,
        "events": events,
    }


async def _write_sentinel_echo_test_run(
    store: RecordingStore,
    *,
    export_root: str | Path,
    name: str,
    channel_ids: list[str],
    since: str | None,
    replay: dict[str, Any],
) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc)
    run_id = f"sentinel-echo-run-{uuid4().hex[:12]}"
    folder = Path(export_root) / created_at.strftime("%Y-%m-%d") / "sentinel-echo-test-runs"
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / f"{created_at.strftime('%Y%m%d-%H%M%S')}-{_safe_slug(name)}-{run_id}.jsonl"
    manifest_bytes = _manifest_bytes(replay["events"])
    file_path.write_bytes(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

    export = ExportRecord(
        export_id=run_id,
        created_at=created_at.isoformat(),
        channel_id="-".join(channel_ids) if channel_ids else "all",
        channel_name="sentinel-echo-test-run",
        format="jsonl",
        file_path=str(file_path),
        row_count=int(replay["event_count"]),
        filters={"channel_id": channel_ids[0] if len(channel_ids) == 1 else None, "channel_ids": channel_ids, "since": since, "contract_version": replay["contract_version"]},
    )
    await store.insert_export_record(export)

    query: dict[str, str] = {}
    if channel_ids:
        query["channel_ids"] = ",".join(channel_ids)
    if since:
        query["since"] = since
    replay_url = "/api/sentinel-echo/replay/events"
    if query:
        replay_url = f"{replay_url}?{urlencode(query)}"

    return {
        "contract_version": "simulation.sentinel-echo.test_run.v1",
        "mode": "simulation",
        "execution": "none",
        "run_id": run_id,
        "name": name,
        "created_at": created_at.isoformat(),
        "execution_mode": "recorded_replay_only",
        "replay_contract_version": replay["contract_version"],
        "event_count": replay["event_count"],
        "file_path": str(file_path),
        "manifest_hash_algorithm": "sha256",
        "manifest_sha256": manifest_sha256,
        "replay_url": replay_url,
        "filters": {"channel_id": channel_ids[0] if len(channel_ids) == 1 else None, "channel_ids": channel_ids, "since": since},
    }


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


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug or "test-run"


def _manifest_bytes(events: list[dict[str, Any]]) -> bytes:
    body = "".join(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n" for event in events)
    return body.encode("utf-8")


def _manifest_sha256(events: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_manifest_bytes(events)).hexdigest()
