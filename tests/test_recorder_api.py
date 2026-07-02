import csv
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from sentinel_archive.api import create_app


def test_recorder_settings_masks_token(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    with TestClient(app) as client:
        response = client.put(
            "/api/recorder/discord/settings",
            json={
                "discord_token": "secret",
                "discord_channel_ids": ["123"],
                "drift_amount_threshold": 0.05,
                "drift_percent_threshold": 10,
                "yfinance_enabled": False,
                "record_all_channels": False,
            },
        )

        assert response.status_code == 200
        assert response.json()["discord_token"] == "********"
        assert client.get("/api/recorder/discord/settings").json()["discord_channel_ids"] == ["123"]


def test_recorder_settings_accepts_pasted_multi_channel_ids(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    with TestClient(app) as client:
        response = client.put(
            "/api/recorder/discord/settings",
            json={
                "discord_token": "secret",
                "discord_channel_ids": "123\n456, 789 123",
                "drift_amount_threshold": 0.05,
                "drift_percent_threshold": 10,
                "yfinance_enabled": False,
                "record_all_channels": False,
            },
        )

        assert response.status_code == 200
        assert response.json()["discord_channel_ids"] == ["123", "456", "789"]
        assert client.get("/api/recorder/discord/settings").json()["discord_channel_ids"] == ["123", "456", "789"]


def test_parse_preview_endpoint(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    with TestClient(app) as client:
        response = client.post("/api/recorder/discord/parse-preview", json={"raw_text": "BTO SPY 500C 6/21 @ 1.25"})

        assert response.status_code == 200
        assert response.json()["parse_status"] == "parsed"
        assert response.json()["ticker"] == "SPY"


def test_option_csv_import_endpoint(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    csv_text = "timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume\n2026-06-19T14:30:00Z,SPY,6/21/2026,500,CALL,1,1.1,0.9,1.05,10\n"
    with TestClient(app) as client:
        response = client.post("/api/recorder/market/import/options-csv", json={"csv_text": csv_text})

        assert response.status_code == 200
        assert response.json()["inserted"] == 1


def test_discord_csv_import_reports_invalid_rows_without_failing_file(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    csv_text = (
        "message_id,channel_id,channel_name,author_id,author_name,discord_timestamp,content\n"
        "m1,123,alerts,a1,Analyst,2026-06-19T14:30:00+00:00,BTO SPY 500C 6/21 @ 1.25\n"
        "m2,,alerts,a1,Analyst,2026-06-19T14:31:00+00:00,BTO SPY 500C 6/21 @ 1.25\n"
    )
    with TestClient(app) as client:
        client.put(
            "/api/recorder/discord/settings",
            json={
                "discord_token": "",
                "discord_channel_ids": ["123"],
                "drift_amount_threshold": 0.05,
                "drift_percent_threshold": 10,
                "yfinance_enabled": False,
                "record_all_channels": False,
            },
        )

        response = client.post("/api/recorder/discord/import-csv", json={"csv_text": csv_text})

        assert response.status_code == 200
        assert response.json()["inserted"] == 1
        assert response.json()["failed"] == 1
        assert response.json()["errors"][0]["row"] == 2


def test_export_endpoint_writes_channel_aware_file(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3", recorder_export_root=tmp_path / "recordings")
    with TestClient(app) as client:
        client.put(
            "/api/recorder/discord/settings",
            json={
                "discord_token": "",
                "discord_channel_ids": ["123"],
                "drift_amount_threshold": 0.05,
                "drift_percent_threshold": 10,
                "yfinance_enabled": False,
                "record_all_channels": False,
            },
        )
        client.post(
            "/api/recorder/dev/ingest-message",
            json={
                "message_id": "m1",
                "channel_id": "123",
                "channel_name": "alerts",
                "author_id": "a1",
                "author_name": "Analyst",
                "discord_timestamp": "2026-06-19T14:30:00+00:00",
                "content": "BTO SPY 500C 6/21 @ 1.25",
            },
        )

        response = client.post("/api/recordings/export", json={"channel_id": "123", "created_at": "2026-06-19T14:31:05+00:00"})

        assert response.status_code == 200
        path = Path(response.json()["file_path"])
        assert path.exists()
        assert "channel-123-alerts" in str(path).lower()


def test_replay_events_endpoint_returns_chronological_truth_stream(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    with TestClient(app) as client:
        client.put(
            "/api/recorder/discord/settings",
            json={
                "discord_token": "",
                "discord_channel_ids": ["123"],
                "drift_amount_threshold": 0.05,
                "drift_percent_threshold": 10,
                "yfinance_enabled": False,
                "record_all_channels": False,
            },
        )
        client.post(
            "/api/recorder/market/import/options-csv",
            json={
                "csv_text": "timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume\n2026-06-19T14:29:00+00:00,SPY,2026-06-21,500,CALL,1,1.1,0.9,1.05,10\n"
            },
        )
        client.post(
            "/api/recorder/dev/ingest-message",
            json={
                "message_id": "m1",
                "channel_id": "123",
                "channel_name": "alerts",
                "author_id": "a1",
                "author_name": "Analyst",
                "discord_timestamp": "2026-06-19T14:30:00+00:00",
                "content": "BTO SPY 500C 6/21 @ 1.25",
            },
        )

        payload = client.get("/api/replay/events").json()
        events = payload["events"]

        assert payload["contract_version"] == "simulation.replay_session.v1"
        assert payload["mode"] == "simulation"
        assert payload["execution"] == "none"
        assert payload["replay_session"]["contract_version"] == "replay_session.v1"
        assert payload["replay_session"]["source_day"] == "2026-06-19"
        assert payload["replay_session"]["event_count"] == 2
        assert payload["replay_session"]["event_types"] == ["discord_alert", "market_snapshot"]
        assert payload["replay_session"]["consumer_notes"] == "read-only replay data; never a live execution signal"
        assert [event["type"] for event in events] == ["discord_alert", "market_snapshot"]
        assert events[0]["timestamp"] <= events[1]["timestamp"]


def test_recording_session_start_stop_tags_ingested_messages(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    with TestClient(app) as client:
        client.put(
            "/api/recorder/discord/settings",
            json={
                "discord_token": "",
                "discord_channel_ids": ["123"],
                "drift_amount_threshold": 0.05,
                "drift_percent_threshold": 10,
                "yfinance_enabled": False,
                "record_all_channels": False,
            },
        )

        start = client.post("/api/recordings/sessions/start", json={"notes": "Sentinel Echo smoke capture"})

        assert start.status_code == 200
        session_id = start.json()["session"]["session_id"]
        assert start.json()["active_session_id"] == session_id

        client.post(
            "/api/recorder/dev/ingest-message",
            json={
                "message_id": "session-m1",
                "channel_id": "123",
                "channel_name": "alerts",
                "author_id": "a1",
                "author_name": "Analyst",
                "discord_timestamp": "2026-06-19T14:30:00+00:00",
                "content": "BTO SPY 500C 6/21 @ 1.25",
            },
        )
        stop = client.post("/api/recordings/sessions/stop", json={})
        messages = client.get("/api/recordings/messages?limit=10&channel_id=123").json()["messages"]
        sessions = client.get("/api/recordings/sessions").json()["sessions"]

        assert stop.status_code == 200
        assert stop.json()["session"]["session_id"] == session_id
        assert stop.json()["session"]["stopped_at"]
        assert messages[0]["session_id"] == session_id
        assert messages[0]["raw_payload"]["recording_session_id"] == session_id
        assert sessions[0]["session_id"] == session_id


def test_joined_export_includes_market_snapshot_and_price_drift(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3", recorder_export_root=tmp_path / "recordings")
    with TestClient(app) as client:
        _seed_spy_option_alert_with_market_drift(client)

        response = client.post(
            "/api/recordings/export",
            json={"channel_id": "123", "created_at": "2026-06-19T14:31:05+00:00", "export_type": "joined"},
        )

        assert response.status_code == 200
        path = Path(response.json()["file_path"])
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        assert response.json()["row_count"] == 1
        assert rows[0]["message_id"] == "m-drift"
        assert rows[0]["selected_market_price"] == "1.05"
        assert rows[0]["price_drift_alert"] == "True"
        assert rows[0]["price_drift_amount"] == "-0.2"


def test_joined_export_filters_multiple_channels(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3", recorder_export_root=tmp_path / "recordings")
    with TestClient(app) as client:
        _seed_multi_channel_alerts(client)

        response = client.post(
            "/api/recordings/export",
            json={
                "channel_ids": ["111", "222"],
                "created_at": "2026-06-19T14:31:05+00:00",
                "export_type": "joined",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["row_count"] == 2
        assert "channel-111-222-multi-channels" in str(payload["file_path"]).lower()
        path = Path(payload["file_path"])
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert {row["channel_id"] for row in rows} == {"111", "222"}
        assert payload["filters"]["channel_ids"] == ["111", "222"]


def test_sentinel_echo_replay_endpoint_returns_joined_alert_events(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    with TestClient(app) as client:
        _seed_spy_option_alert_with_market_drift(client)

        payload = client.get("/api/sentinel-echo/replay/events?limit=10&channel_id=123").json()

        assert payload["contract_version"] == "simulation.sentinel-echo.replay.v1"
        assert payload["mode"] == "simulation"
        assert payload["execution"] == "none"
        assert payload["consumer_notes"] == "read-only replay data; never a live execution signal"
        assert payload["event_count"] == 1
        event = payload["events"][0]
        assert event["event_id"] == "discord_alert:m-drift"
        assert event["type"] == "discord_alert"
        assert event["payload"]["message"]["content"] == "BTO SPY 500C 6/21 @ 1.25"
        assert event["payload"]["alert"]["ticker"] == "SPY"
        assert event["payload"]["market_snapshot"]["selected_market_price"] == 1.05
        assert event["payload"]["price_drift"]["price_drift_alert"] is True
        expected_hash = hashlib.sha256(
            "".join(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n" for event in payload["events"]).encode("utf-8")
        ).hexdigest()
        assert payload["manifest_hash_algorithm"] == "sha256"
        assert payload["manifest_sha256"] == expected_hash


def test_sentinel_echo_replay_endpoint_filters_multiple_channel_ids(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    with TestClient(app) as client:
        _seed_multi_channel_alerts(client)

        payload = client.get("/api/sentinel-echo/replay/events?limit=10&channel_ids=111,222").json()

        assert payload["event_count"] == 2
        assert {event["channel_id"] for event in payload["events"]} == {"111", "222"}
        assert payload["filters"]["channel_ids"] == ["111", "222"]


def test_sentinel_echo_test_run_writes_replay_manifest_without_executing(tmp_path):
    export_root = tmp_path / "recordings"
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3", recorder_export_root=export_root)
    with TestClient(app) as client:
        _seed_spy_option_alert_with_market_drift(client)

        response = client.post(
            "/api/sentinel-echo/test-runs",
            json={"name": "Sentinel Echo smoke", "channel_id": "123", "limit": 10},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["contract_version"] == "simulation.sentinel-echo.test_run.v1"
        assert payload["mode"] == "simulation"
        assert payload["execution"] == "none"
        assert payload["execution_mode"] == "recorded_replay_only"
        assert payload["event_count"] == 1
        path = Path(payload["file_path"])
        assert path.exists()
        with path.open(encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        assert lines[0]["event_id"] == "discord_alert:m-drift"
        assert payload["manifest_hash_algorithm"] == "sha256"
        assert payload["manifest_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert len(payload["manifest_sha256"]) == 64


def test_sentinel_echo_test_run_filters_multiple_channels(tmp_path):
    export_root = tmp_path / "recordings"
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3", recorder_export_root=export_root)
    with TestClient(app) as client:
        _seed_multi_channel_alerts(client)

        response = client.post(
            "/api/sentinel-echo/test-runs",
            json={"name": "Multi-channel replay", "channel_ids": ["111", "222"], "limit": 10},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_count"] == 2
        assert payload["filters"]["channel_ids"] == ["111", "222"]
        assert "channel_ids=111%2C222" in payload["replay_url"] or "channel_ids=111,222" in payload["replay_url"]
        path = Path(payload["file_path"])
        with path.open(encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        assert {event["channel_id"] for event in lines} == {"111", "222"}


def _seed_spy_option_alert_with_market_drift(client: TestClient) -> None:
    client.put(
        "/api/recorder/discord/settings",
        json={
            "discord_token": "",
            "discord_channel_ids": ["123"],
            "drift_amount_threshold": 0.05,
            "drift_percent_threshold": 10,
            "yfinance_enabled": False,
            "record_all_channels": False,
        },
    )
    client.post(
        "/api/recorder/market/import/options-csv",
        json={
            "csv_text": (
                "timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume\n"
                "2026-06-19T14:29:00+00:00,SPY,2026-06-21,500,CALL,1,1.1,0.9,1.05,10\n"
            )
        },
    )
    client.post(
        "/api/recorder/dev/ingest-message",
        json={
            "message_id": "m-drift",
            "channel_id": "123",
            "channel_name": "alerts",
            "author_id": "a1",
            "author_name": "Analyst",
            "discord_timestamp": "2026-06-19T14:30:00+00:00",
            "content": "BTO SPY 500C 6/21 @ 1.25",
        },
    )


def _seed_multi_channel_alerts(client: TestClient) -> None:
    client.put(
        "/api/recorder/discord/settings",
        json={
            "discord_token": "",
            "discord_channel_ids": ["111", "222", "333"],
            "drift_amount_threshold": 0.05,
            "drift_percent_threshold": 10,
            "yfinance_enabled": False,
            "record_all_channels": False,
        },
    )
    for channel_id, message_id in [("111", "multi-1"), ("222", "multi-2"), ("333", "multi-3")]:
        client.post(
            "/api/recorder/dev/ingest-message",
            json={
                "message_id": message_id,
                "channel_id": channel_id,
                "channel_name": f"alerts-{channel_id}",
                "author_id": "a1",
                "author_name": "Analyst",
                "discord_timestamp": f"2026-06-19T14:3{channel_id[-1]}:00+00:00",
                "content": "BTO SPY 500C 6/21 @ 1.25",
            },
        )
