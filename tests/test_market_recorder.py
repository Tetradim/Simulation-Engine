import asyncio

from sentinel_archive.alert_parser import parse_alert_text
from sentinel_archive.market_recorder import (
    calculate_price_drift,
    create_snapshot_for_alert,
    parse_option_csv,
    parse_stock_csv,
)
from sentinel_archive.recorder_models import RecorderSettings
from sentinel_archive.recording_store import RecordingStore


def test_parse_option_csv_normalizes_contract_key_and_prices():
    bars = parse_option_csv(
        """timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume,bid,ask
2026-06-19T14:29:00Z,spy,6/21/2026,500,c,1.2,1.35,1.1,1.30,120,1.28,1.32
"""
    )

    assert len(bars) == 1
    assert bars[0].contract_key == "SPY|2026-06-21|500|CALL"
    assert bars[0].mid == 1.30


def test_parse_stock_csv_normalizes_symbol_rows():
    bars = parse_stock_csv(
        """timestamp,symbol,open,high,low,close,volume
2026-06-19T14:29:00Z,spy,540,541,539,540.5,1000
"""
    )

    assert len(bars) == 1
    assert bars[0].instrument_type == "stock"
    assert bars[0].symbol == "SPY"
    assert bars[0].close == 540.5


def test_price_drift_triggers_on_amount_or_percent_threshold():
    event = calculate_price_drift(
        alert_id="m1",
        alert_price=1.25,
        market_price=1.40,
        amount_threshold=0.10,
        percent_threshold=20.0,
    )

    assert event.price_drift_alert is True
    assert event.price_drift_amount == 0.15
    assert event.drift_direction == "market_above_alert"


def test_create_snapshot_uses_latest_option_bar_before_alert(tmp_path):
    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.insert_market_bars(
            parse_option_csv(
                """timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume
2026-06-19T14:29:00+00:00,SPY,2026-06-21,500,CALL,1.2,1.35,1.1,1.30,120
2026-06-19T14:31:00+00:00,SPY,2026-06-21,500,CALL,1.5,1.6,1.4,1.55,120
"""
            )
        )
        alert = parse_alert_text("BTO SPY 500C 6/21 @ 1.25", message_id="m1")

        snapshot, drift = await create_snapshot_for_alert(
            store,
            alert,
            settings=RecorderSettings(drift_amount_threshold=0.05, drift_percent_threshold=10.0),
            snapshot_timestamp="2026-06-19T14:30:00+00:00",
        )

        assert snapshot.selected_market_price == 1.30
        assert snapshot.lookup_status == "matched"
        assert drift.price_drift_alert is True
        assert len(await store.list_market_snapshots()) == 1
        assert len(await store.list_drift_events()) == 1

    asyncio.run(run())
