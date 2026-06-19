from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from .alert_parser import build_discord_alert_text, parse_alert_text
from .market_recorder import YFinanceMarketProvider, create_snapshot_for_alert
from .recorder_models import DiscordMessageRecord, DiscordSource, RecorderStatus
from .recording_store import RecordingStore


class DiscordRecorder:
    def __init__(self, store: RecordingStore, market_provider: YFinanceMarketProvider | None = None):
        self.store = store
        self.market_provider = market_provider or YFinanceMarketProvider()
        self.bot: Any | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.state = "stopped"
        self.last_error = ""

    async def start(self) -> RecorderStatus:
        settings = await self.store.get_settings(mask_token=False)
        token = settings.discord_token or os.environ.get("DISCORD_BOT_TOKEN", "")
        if not token:
            self.state = "failed"
            self.last_error = "discord_token_missing"
            return await self.status()
        if self.thread and self.thread.is_alive():
            return await self.status()

        self.state = "connecting"
        self.last_error = ""
        self.thread = threading.Thread(target=self._thread_main, args=(token,), daemon=True, name="discord-recorder")
        self.thread.start()
        return await self.status()

    async def stop(self) -> RecorderStatus:
        self.state = "stopping"
        if self.bot is not None and self.loop is not None and self.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.bot.close(), self.loop)
            try:
                future.result(timeout=10)
            except Exception as exc:
                self.last_error = str(exc)
        self.bot = None
        self.state = "stopped"
        return await self.status()

    async def test_connection(self) -> dict[str, Any]:
        settings = await self.store.get_settings(mask_token=False)
        token_configured = bool(settings.discord_token or os.environ.get("DISCORD_BOT_TOKEN"))
        channels = settings.discord_channel_ids
        return {
            "ok": token_configured and (settings.record_all_channels or bool(channels)),
            "token_configured": token_configured,
            "channel_ids": channels,
            "record_all_channels": settings.record_all_channels,
            "state": self.state,
            "last_error": self.last_error,
        }

    async def handle_message(self, message: Any, *, bot_user_id: str | None = None) -> str:
        author = getattr(message, "author", None)
        if bot_user_id and str(getattr(author, "id", "")) == str(bot_user_id):
            return "self_message"

        settings = await self.store.get_settings(mask_token=False)
        channel = getattr(message, "channel", None)
        channel_id = str(getattr(channel, "id", ""))
        source = await self._source_for_channel(channel_id)
        author_id = str(getattr(author, "id", ""))
        filter_result = self._filter_message(channel_id, author_id, settings, source)
        if filter_result != "accepted":
            return filter_result

        guild = getattr(message, "guild", None)
        raw_text = build_discord_alert_text(message)
        record = DiscordMessageRecord(
            message_id=str(getattr(message, "id", "")),
            channel_id=channel_id,
            channel_name=str(getattr(channel, "name", "")),
            guild_id=str(getattr(guild, "id", "")),
            guild_name=str(getattr(guild, "name", "")),
            author_id=author_id,
            author_name=str(getattr(author, "name", "")),
            discord_timestamp=_iso(getattr(message, "created_at", None)),
            content=str(getattr(message, "content", "")),
            embeds=[_to_dict(embed) for embed in getattr(message, "embeds", []) or []],
            attachments=[_to_dict(item) for item in getattr(message, "attachments", []) or []],
            raw_payload={},
        )
        await self.store.upsert_source(
            DiscordSource(
                channel_id=record.channel_id,
                channel_name=record.channel_name,
                guild_id=record.guild_id,
                guild_name=record.guild_name,
                enabled=True,
                allowed_author_ids=source.allowed_author_ids if source else [],
                ignored_author_ids=source.ignored_author_ids if source else [],
                notes=source.notes if source else "",
            )
        )
        await self.store.insert_message(record)

        alert = parse_alert_text(raw_text, message_id=record.message_id)
        await self.store.insert_parsed_alert(alert)
        if alert.parse_status == "parsed":
            if settings.yfinance_enabled and alert.normalized.get("contract_key"):
                live_bar = await self.market_provider.latest_option_bar(alert)
                if live_bar:
                    await self.store.insert_market_bars([live_bar])
            await create_snapshot_for_alert(
                self.store,
                alert,
                settings=settings,
                snapshot_timestamp=record.discord_timestamp,
            )
        return "recorded"

    async def status(self) -> RecorderStatus:
        messages = await self.store.list_messages(limit=10000)
        alerts = await self.store.list_alerts(limit=10000)
        drift_events = await self.store.list_drift_events(limit=10000)
        settings = await self.store.get_settings(mask_token=True)
        last_message_timestamp = messages[0]["engine_received_timestamp"] if messages else None
        return RecorderStatus(
            discord_connected=self.state == "connected",
            discord_state=self.state,
            monitored_channels=settings.discord_channel_ids if not settings.record_all_channels else ["*"],
            messages_recorded=len(messages),
            parsed_alerts=sum(1 for alert in alerts if alert.get("parse_status") == "parsed"),
            unparsed_alerts=sum(1 for alert in alerts if alert.get("parse_status") != "parsed"),
            drift_alerts=sum(1 for event in drift_events if event.get("price_drift_alert")),
            last_message_timestamp=last_message_timestamp,
            last_error=self.last_error,
        )

    async def _source_for_channel(self, channel_id: str) -> DiscordSource | None:
        for source_data in await self.store.list_sources():
            if str(source_data.get("channel_id")) == channel_id:
                return DiscordSource(**source_data)
        return None

    @staticmethod
    def _filter_message(channel_id: str, author_id: str, settings: Any, source: DiscordSource | None) -> str:
        if source and not source.enabled:
            return "channel_disabled"
        configured_channels = set(settings.discord_channel_ids)
        known_source = source is not None and source.enabled
        if not settings.record_all_channels and channel_id not in configured_channels and not known_source:
            return "channel_not_monitored"
        if source and source.ignored_author_ids and author_id in set(source.ignored_author_ids):
            return "author_ignored"
        if source and source.allowed_author_ids and author_id not in set(source.allowed_author_ids):
            return "author_not_allowed"
        return "accepted"

    def _thread_main(self, token: str) -> None:
        try:
            asyncio.run(self._run_client(token))
        except Exception as exc:
            self.state = "failed"
            self.last_error = str(exc)

    async def _run_client(self, token: str) -> None:
        import discord

        self.loop = asyncio.get_running_loop()
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self.bot = client

        @client.event
        async def on_ready() -> None:
            self.state = "connected"
            self.last_error = ""

        @client.event
        async def on_message(message: Any) -> None:
            bot_user = getattr(client, "user", None)
            await self.handle_message(message, bot_user_id=str(getattr(bot_user, "id", "")))

        try:
            await client.start(token)
        except discord.LoginFailure:
            self.state = "failed"
            self.last_error = "invalid_token"
        except Exception as exc:
            self.state = "failed"
            self.last_error = str(exc)
        finally:
            if self.state != "failed":
                self.state = "stopped"


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return {"text": str(value)}
