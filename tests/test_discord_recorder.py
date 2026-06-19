import asyncio
import types

from simulation_engine.discord_recorder import DiscordRecorder
from simulation_engine.market_recorder import parse_option_csv
from simulation_engine.recorder_models import DiscordSource, RecorderSettings
from simulation_engine.recording_store import RecordingStore


def fake_message(content, channel_id="123", author_id="a1", message_id="m1"):
    return types.SimpleNamespace(
        id=message_id,
        content=content,
        embeds=[],
        attachments=[],
        created_at=types.SimpleNamespace(isoformat=lambda: "2026-06-19T14:30:00+00:00"),
        author=types.SimpleNamespace(id=author_id, name="Analyst"),
        channel=types.SimpleNamespace(id=channel_id, name="alerts"),
        guild=types.SimpleNamespace(id="g1", name="Guild"),
    )


def test_handle_message_records_configured_channel_and_drift(tmp_path):
    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.save_settings(RecorderSettings(discord_channel_ids=["123"], drift_amount_threshold=0.05))
        await store.insert_market_bars(
            parse_option_csv(
                """timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume
2026-06-19T14:29:00+00:00,SPY,2026-06-21,500,CALL,1.2,1.35,1.1,1.30,120
"""
            )
        )
        recorder = DiscordRecorder(store)

        result = await recorder.handle_message(fake_message("BTO SPY 500C 6/21 @ 1.25"), bot_user_id="bot")

        assert result == "recorded"
        assert len(await store.list_messages()) == 1
        assert (await store.list_alerts())[0]["parse_status"] == "parsed"
        assert (await store.list_drift_events())[0]["price_drift_alert"] is True

    asyncio.run(run())


def test_handle_message_skips_unconfigured_channel(tmp_path):
    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.save_settings(RecorderSettings(discord_channel_ids=["999"]))
        recorder = DiscordRecorder(store)

        result = await recorder.handle_message(fake_message("BTO SPY 500C 6/21 @ 1.25"), bot_user_id="bot")

        assert result == "channel_not_monitored"
        assert await store.list_messages() == []

    asyncio.run(run())


def test_handle_message_respects_author_filters(tmp_path):
    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.save_settings(RecorderSettings(discord_channel_ids=["123"]))
        await store.upsert_source(DiscordSource(channel_id="123", ignored_author_ids=["a1"]))
        recorder = DiscordRecorder(store)

        result = await recorder.handle_message(fake_message("BTO SPY 500C 6/21 @ 1.25"), bot_user_id="bot")

        assert result == "author_ignored"
        assert await store.list_messages() == []

    asyncio.run(run())
