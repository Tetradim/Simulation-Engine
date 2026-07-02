import asyncio
import csv
from pathlib import Path

from sentinel_archive.recorder_models import (
    DiscordMessageRecord,
    MarketBarRecord,
    MarketSnapshotRecord,
    ParsedAlert,
    PriceDriftEvent,
    RecorderSettings,
    normalize_contract_key,
)


def test_recorder_settings_masks_token():
    settings = RecorderSettings(discord_token="secret-token", discord_channel_ids=["123"])

    masked = settings.masked()

    assert masked.discord_token == "********"
    assert masked.discord_channel_ids == ["123"]


def test_contract_key_normalization():
    assert normalize_contract_key("spy", "6/21/2026", 500, "call") == "SPY|2026-06-21|500|CALL"


def test_parsed_alert_accepts_unparsed_message():
    alert = ParsedAlert(message_id="m1", parse_status="unparsed", raw_text="watching SPY")

    assert alert.parse_status == "unparsed"
    assert alert.ticker is None


def test_store_persists_settings_and_masks_token(tmp_path):
    from sentinel_archive.recording_store import RecordingStore

    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()

        await store.save_settings(RecorderSettings(discord_token="secret", discord_channel_ids=["123"]))
        saved = await store.get_settings(mask_token=False)
        masked = await store.get_settings(mask_token=True)

        assert saved.discord_token == "secret"
        assert masked.discord_token == "********"
        assert masked.discord_channel_ids == ["123"]

    asyncio.run(run())


def test_store_persists_recording_objects(tmp_path):
    from sentinel_archive.recording_store import RecordingStore

    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()

        await store.insert_message(
            DiscordMessageRecord(
                message_id="m1",
                channel_id="123",
                channel_name="alerts",
                author_id="a1",
                author_name="Analyst",
                discord_timestamp="2026-06-19T14:30:00+00:00",
                content="BTO SPY 500C 6/21 @ 1.25",
            )
        )
        await store.insert_parsed_alert(
            ParsedAlert(
                message_id="m1",
                parse_status="parsed",
                raw_text="BTO SPY 500C 6/21 @ 1.25",
                action="buy",
                ticker="SPY",
                expiration="2026-06-21",
                strike=500,
                option_type="CALL",
                alert_price=1.25,
                normalized={"contract_key": "SPY|2026-06-21|500|CALL"},
            )
        )
        await store.insert_market_bars(
            [
                MarketBarRecord(
                    instrument_type="option",
                    timestamp="2026-06-19T14:29:00+00:00",
                    symbol="SPY",
                    underlying="SPY",
                    expiration="2026-06-21",
                    strike=500,
                    option_type="CALL",
                    contract_key="SPY|2026-06-21|500|CALL",
                    close=1.30,
                    last=1.30,
                )
            ]
        )
        await store.insert_market_snapshot(
            MarketSnapshotRecord(
                alert_id="m1",
                snapshot_timestamp="2026-06-19T14:30:00+00:00",
                underlying="SPY",
                option_contract_key="SPY|2026-06-21|500|CALL",
                selected_market_price=1.30,
                price_source="csv",
                lookup_status="matched",
            )
        )
        await store.insert_drift_event(
            PriceDriftEvent(
                alert_id="m1",
                alert_price=1.25,
                market_price=1.30,
                price_drift_amount=0.05,
                price_drift_pct=4.0,
                drift_amount_threshold=0.05,
                drift_percent_threshold=10.0,
                drift_direction="market_above_alert",
                price_drift_alert=True,
            )
        )

        assert len(await store.list_messages(limit=10)) == 1
        assert len(await store.list_alerts(limit=10)) == 1
        assert len(await store.list_market_bars(limit=10)) == 1
        assert len(await store.list_market_snapshots(limit=10)) == 1
        assert len(await store.list_drift_events(limit=10)) == 1
        assert (await store.latest_market_bar(contract_key="SPY|2026-06-21|500|CALL", at_or_before="2026-06-19T14:30:00+00:00"))["close"] == 1.30

    asyncio.run(run())


def test_store_exports_timestamped_channel_aware_alert_csv(tmp_path):
    from sentinel_archive.recording_store import RecordingStore

    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.insert_message(
            DiscordMessageRecord(
                message_id="m1",
                channel_id="123",
                channel_name="Option Alerts",
                guild_id="g1",
                guild_name="Test Guild",
                author_id="a1",
                author_name="Analyst",
                discord_timestamp="2026-06-19T14:30:00+00:00",
                content="BTO SPY 500C 6/21 @ 1.25",
            )
        )
        await store.insert_parsed_alert(
            ParsedAlert(
                message_id="m1",
                parse_status="parsed",
                raw_text="BTO SPY 500C 6/21 @ 1.25",
                action="buy",
                ticker="SPY",
                expiration="2026-06-21",
                strike=500,
                option_type="CALL",
                alert_price=1.25,
                normalized={"contract_key": "SPY|2026-06-21|500|CALL"},
            )
        )

        export = await store.export_alerts(tmp_path / "recordings", channel_id="123", created_at="2026-06-19T14:31:05+00:00")
        path = Path(export.file_path)
        rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))

        assert export.row_count == 1
        assert "2026-06-19" in str(path)
        assert "channel-123-option-alerts" in str(path).lower()
        assert path.name.startswith("20260619-143105")
        assert rows[0]["channel_id"] == "123"
        assert rows[0]["channel_name"] == "Option Alerts"
        assert rows[0]["ticker"] == "SPY"

    asyncio.run(run())


def test_store_lists_alerts_newest_discord_message_first(tmp_path):
    from sentinel_archive.recording_store import RecordingStore

    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        for message_id, timestamp in [("z-old", "2026-06-19T14:30:00+00:00"), ("a-new", "2026-06-19T14:35:00+00:00")]:
            await store.insert_message(
                DiscordMessageRecord(
                    message_id=message_id,
                    channel_id="123",
                    channel_name="alerts",
                    author_id="a1",
                    author_name="Analyst",
                    discord_timestamp=timestamp,
                    content=f"BTO SPY 500C 6/21 @ 1.2 {message_id}",
                )
            )
            await store.insert_parsed_alert(
                ParsedAlert(
                    message_id=message_id,
                    parse_status="parsed",
                    raw_text="BTO SPY 500C 6/21 @ 1.25",
                    action="buy",
                    ticker="SPY",
                    expiration="2026-06-21",
                    strike=500,
                    option_type="CALL",
                    alert_price=1.25,
                    normalized={"contract_key": "SPY|2026-06-21|500|CALL"},
                )
            )

        alerts = await store.list_alerts(limit=10)

        assert [alert["message_id"] for alert in alerts] == ["a-new", "z-old"]

    asyncio.run(run())
