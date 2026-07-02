from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException

from .bot_event_bus import BotEvent, event_bus


EVENT_BUS_SECRET_ENV = "SIMULATION_EVENT_BUS_SECRET"
EVENT_BUS_SECRET_HEADER = "X-Simulation-Event-Bus-Secret"
MIN_EVENT_BUS_SECRET_LENGTH = 32
EVENT_BUS_SECRET_PLACEHOLDERS = {
    "replace_with_a_long_random_event_bus_secret",
    "change_me",
    "changeme",
}


def require_event_bus_secret(
    provided_secret: str | None = Header(default=None, alias=EVENT_BUS_SECRET_HEADER),
) -> None:
    configured_secret = os.environ.get(EVENT_BUS_SECRET_ENV, "").strip()
    if not configured_secret:
        raise HTTPException(status_code=503, detail=f"{EVENT_BUS_SECRET_ENV} is not configured")
    if (
        len(configured_secret) < MIN_EVENT_BUS_SECRET_LENGTH
        or configured_secret.lower() in EVENT_BUS_SECRET_PLACEHOLDERS
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                f"{EVENT_BUS_SECRET_ENV} must be a non-placeholder secret with at least "
                f"{MIN_EVENT_BUS_SECRET_LENGTH} characters"
            ),
        )
    if not provided_secret or not secrets.compare_digest(provided_secret, configured_secret):
        raise HTTPException(status_code=401, detail="Invalid Simulation event bus secret")


def create_bot_event_bus_router() -> APIRouter:
    router = APIRouter(tags=["Cross Bot Event Bus"])

    @router.post("/bus/events")
    async def publish_bus_event(event: BotEvent, _: None = Depends(require_event_bus_secret)):
        accepted = event_bus.publish(event)
        return {"status": "accepted", "event": accepted.model_dump(mode="json")}

    @router.get("/bus/events")
    async def recent_bus_events(
        limit: int = 100,
        event_type: str | None = None,
        _: None = Depends(require_event_bus_secret),
    ):
        return {"events": event_bus.recent(limit=limit, event_type=event_type)}

    return router
