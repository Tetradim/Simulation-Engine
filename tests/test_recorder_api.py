from pathlib import Path

from fastapi.testclient import TestClient

from simulation_engine.api import create_app


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

        events = client.get("/api/replay/events").json()["events"]

        assert [event["type"] for event in events] == ["discord_alert", "market_snapshot"]
        assert events[0]["timestamp"] <= events[1]["timestamp"]
