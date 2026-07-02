from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from types import SimpleNamespace
from typing import Any
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .alert_parser import build_discord_alert_text
from .bot_event_bus import publish_event
from .discord_recorder import DiscordRecorder
from .recorder_models import DiscordSource
from .recording_store import RecordingStore


LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_lock = Lock()
_last_heartbeat: dict[str, Any] | None = None


class ChromeBridgeEmbed(BaseModel):
    author_name: str | None = None
    title: str | None = None
    description: str | None = None
    fields: list[dict[str, Any]] = Field(default_factory=list)
    footer_text: str | None = None


class ChromeBridgeMessage(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=240)
    channel_id: str = Field(default="chrome-visible-discord", max_length=120)
    channel_name: str = Field(default="chrome-visible-discord", max_length=120)
    channel_url: str | None = Field(default=None, max_length=2048)
    author_id: str | None = Field(default=None, max_length=120)
    author_name: str = Field(default="Discord Chrome", max_length=120)
    content: str = Field(default="", max_length=12000)
    embeds: list[ChromeBridgeEmbed] = Field(default_factory=list)
    url: str | None = Field(default=None, max_length=2048)
    observed_at: str | None = Field(default=None, max_length=80)
    source: str = Field(default="chrome-discord-bridge", max_length=80)
    bridge_target_id: str | None = Field(default=None, max_length=120)
    bridge_target_name: str | None = Field(default=None, max_length=120)


class ChromeBridgeHeartbeat(BaseModel):
    status: str = Field(default="ok", max_length=80)
    bridge_enabled: bool = False
    url: str | None = Field(default=None, max_length=2048)
    channel_id: str | None = Field(default=None, max_length=120)
    channel_name: str | None = Field(default=None, max_length=120)
    channel_url: str | None = Field(default=None, max_length=2048)
    observed_at: str | None = Field(default=None, max_length=80)
    last_forward_at: str | None = Field(default=None, max_length=80)
    last_forward_status: str | None = Field(default=None, max_length=120)
    bridge_target_id: str | None = Field(default=None, max_length=120)
    bridge_target_name: str | None = Field(default=None, max_length=120)
    details: dict[str, Any] = Field(default_factory=dict)


def create_chrome_bridge_router(store: RecordingStore, recorder: DiscordRecorder) -> APIRouter:
    router = APIRouter(prefix="/discord/chrome-bridge", tags=["Chrome Discord Bridge"])

    @router.post("/message")
    async def ingest_message(payload: ChromeBridgeMessage, request: Request) -> dict[str, Any]:
        _ensure_local_request(request)
        synthetic_message = _to_message(payload)
        raw_text = build_discord_alert_text(synthetic_message).strip()
        if not raw_text:
            raise HTTPException(status_code=400, detail="message content or embed text is required")

        await store.upsert_source(
            DiscordSource(
                channel_id=payload.channel_id,
                channel_name=payload.channel_name,
                enabled=True,
                notes="Observed from Chrome Discord bridge.",
            )
        )
        recording_status = await recorder.handle_message(synthetic_message, bot_user_id="sentinel-archive")
        event = publish_event(
            "signal.observed",
            source_bot="chrome-discord-bridge",
            payload=_message_payload(payload, raw_text, recording_status=recording_status),
            correlation_id=payload.event_id,
            dedupe_key=f"chrome-discord:{payload.event_id}",
            target_bots=["sentinel-archive"],
        )
        return {
            "status": "accepted",
            "event_id": payload.event_id,
            "raw_text": raw_text,
            "recording_status": recording_status,
            "bus_event_id": event.event_id,
        }

    @router.post("/heartbeat")
    async def ingest_heartbeat(payload: ChromeBridgeHeartbeat, request: Request) -> dict[str, Any]:
        _ensure_local_request(request)
        heartbeat = _heartbeat_payload(payload)
        with _lock:
            global _last_heartbeat
            _last_heartbeat = heartbeat
        publish_event(
            "bridge.health",
            source_bot="chrome-discord-bridge",
            payload=heartbeat,
            dedupe_key=f"chrome-bridge-health:{heartbeat['status']}:{heartbeat['channel_id']}",
            target_bots=["sentinel-archive"],
        )
        return bridge_health()

    @router.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        _ensure_local_request(request)
        return bridge_health()

    return router


def bridge_health() -> dict[str, Any]:
    with _lock:
        heartbeat = dict(_last_heartbeat) if _last_heartbeat else None
    issues: list[str] = []
    if heartbeat is None:
        issues.append("chrome bridge has not sent a heartbeat")
    elif heartbeat.get("status") not in {"ok", "disabled"}:
        issues.append(f"chrome bridge reported {heartbeat.get('status')}")
    elif not heartbeat.get("bridge_enabled", False):
        issues.append("chrome bridge is disabled")
    return {"healthy": not issues, "status": "healthy" if not issues else "unhealthy", "issues": issues, "last_heartbeat": heartbeat}


def _ensure_local_request(request: Request) -> None:
    if os.environ.get("CHROME_BRIDGE_ALLOW_REMOTE", "").lower() in {"1", "true", "yes"}:
        return
    host = request.client.host if request.client else ""
    if host not in LOCAL_CLIENT_HOSTS:
        raise HTTPException(status_code=403, detail="chrome bridge endpoint only accepts local requests")


def _message_payload(payload: ChromeBridgeMessage, raw_text: str, *, recording_status: str) -> dict[str, Any]:
    return {
        "contract_version": "chrome.discord.message.v1",
        "event_id": payload.event_id,
        "source": payload.source,
        "channel_id": payload.channel_id,
        "channel_name": payload.channel_name,
        "channel_url": payload.channel_url,
        "url": payload.url,
        "observed_at": payload.observed_at,
        "bridge_target_id": payload.bridge_target_id,
        "bridge_target_name": payload.bridge_target_name,
        "author_id": payload.author_id,
        "author_name": payload.author_name,
        "raw_text": raw_text,
        "recording_status": recording_status,
    }


def _heartbeat_payload(payload: ChromeBridgeHeartbeat) -> dict[str, Any]:
    heartbeat = payload.model_dump(mode="json")
    heartbeat["observed_at"] = heartbeat.get("observed_at") or datetime.now(timezone.utc).isoformat()
    heartbeat["healthy"] = heartbeat.get("status") == "ok" and bool(heartbeat.get("bridge_enabled"))
    return heartbeat


def _to_message(payload: ChromeBridgeMessage) -> Any:
    embeds = []
    for embed in payload.embeds:
        embeds.append(
            SimpleNamespace(
                author=SimpleNamespace(name=embed.author_name or ""),
                title=embed.title or "",
                description=embed.description or "",
                fields=[
                    SimpleNamespace(name=str(field.get("name", "")), value=str(field.get("value", "")))
                    for field in embed.fields
                ],
                footer=SimpleNamespace(text=embed.footer_text or ""),
            )
        )
    return SimpleNamespace(
        id=payload.event_id,
        content=payload.content,
        embeds=embeds,
        attachments=[],
        created_at=SimpleNamespace(isoformat=lambda: payload.observed_at or datetime.now(timezone.utc).isoformat()),
        author=SimpleNamespace(id=payload.author_id or payload.author_name, name=payload.author_name),
        channel=SimpleNamespace(id=payload.channel_id, name=payload.channel_name),
        guild=SimpleNamespace(id="", name=""),
    )
