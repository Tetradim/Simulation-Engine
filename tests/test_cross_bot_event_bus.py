import os

from fastapi.testclient import TestClient

from sentinel_archive.api import create_app
from sentinel_archive.bot_event_bus import BotEvent, EventBusStore


EVENT_BUS_SECRET = "test-event-secret-for-simulation-bus-32chars"
EVENT_BUS_HEADER = {"X-Simulation-Event-Bus-Secret": EVENT_BUS_SECRET}


def test_event_bus_store_publishes_and_reads_recent_events(tmp_path):
    store = EventBusStore(tmp_path / "events")
    event = store.publish(
        BotEvent(
            event_type="edge.action",
            source_bot="sentinel-edge",
            target_bots=["sentinel-archive"],
            payload={"contract_version": "edge.action.v1", "action": "stop_buying"},
        )
    )

    recent = store.recent(event_type="edge.action")

    assert recent[0]["event_id"] == event.event_id
    assert recent[0]["payload"]["action"] == "stop_buying"


def test_bus_routes_accept_and_return_events(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    old_event_secret = os.environ.get("SIMULATION_EVENT_BUS_SECRET")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    os.environ["SIMULATION_EVENT_BUS_SECRET"] = EVENT_BUS_SECRET
    try:
        app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
        with TestClient(app) as client:
            response = client.post(
                "/api/bus/events",
                headers=EVENT_BUS_HEADER,
                json={
                    "event_type": "edge.action",
                    "source_bot": "sentinel-edge",
                    "target_bots": ["sentinel-archive"],
                    "payload": {"contract_version": "edge.action.v1", "action": "stop_buying"},
                },
            )
            events = client.get(
                "/api/bus/events?event_type=edge.action",
                headers=EVENT_BUS_HEADER,
            ).json()["events"]

        assert response.status_code == 200
        assert events[0]["payload"]["action"] == "stop_buying"
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir
        if old_event_secret is None:
            os.environ.pop("SIMULATION_EVENT_BUS_SECRET", None)
        else:
            os.environ["SIMULATION_EVENT_BUS_SECRET"] = old_event_secret


def test_bus_routes_fail_closed_without_configured_secret(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    old_event_secret = os.environ.get("SIMULATION_EVENT_BUS_SECRET")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    os.environ.pop("SIMULATION_EVENT_BUS_SECRET", None)
    try:
        app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
        with TestClient(app) as client:
            post_response = client.post(
                "/api/bus/events",
                headers=EVENT_BUS_HEADER,
                json={
                    "event_type": "edge.action",
                    "source_bot": "sentinel-edge",
                    "target_bots": ["sentinel-archive"],
                    "payload": {"contract_version": "edge.action.v1", "action": "stop_buying"},
                },
            )
            get_response = client.get("/api/bus/events?event_type=edge.action", headers=EVENT_BUS_HEADER)

        assert post_response.status_code == 503
        assert get_response.status_code == 503
        assert "SIMULATION_EVENT_BUS_SECRET" in post_response.text
        assert "SIMULATION_EVENT_BUS_SECRET" in get_response.text
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir
        if old_event_secret is None:
            os.environ.pop("SIMULATION_EVENT_BUS_SECRET", None)
        else:
            os.environ["SIMULATION_EVENT_BUS_SECRET"] = old_event_secret


def test_bus_routes_fail_closed_with_weak_configured_secret(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    old_event_secret = os.environ.get("SIMULATION_EVENT_BUS_SECRET")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    os.environ["SIMULATION_EVENT_BUS_SECRET"] = "test-event-secret"
    try:
        app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
        with TestClient(app) as client:
            response = client.post(
                "/api/bus/events",
                headers={"X-Simulation-Event-Bus-Secret": "test-event-secret"},
                json={
                    "event_type": "edge.action",
                    "source_bot": "sentinel-edge",
                    "target_bots": ["sentinel-archive"],
                    "payload": {"contract_version": "edge.action.v1", "action": "stop_buying"},
                },
            )

        assert response.status_code == 503
        assert "SIMULATION_EVENT_BUS_SECRET" in response.text
        assert "at least 32 characters" in response.text
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir
        if old_event_secret is None:
            os.environ.pop("SIMULATION_EVENT_BUS_SECRET", None)
        else:
            os.environ["SIMULATION_EVENT_BUS_SECRET"] = old_event_secret


def test_bus_routes_require_matching_secret_header(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    old_event_secret = os.environ.get("SIMULATION_EVENT_BUS_SECRET")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    os.environ["SIMULATION_EVENT_BUS_SECRET"] = EVENT_BUS_SECRET
    try:
        app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
        request_body = {
            "event_type": "edge.action",
            "source_bot": "sentinel-edge",
            "target_bots": ["sentinel-archive"],
            "payload": {"contract_version": "edge.action.v1", "action": "stop_buying"},
        }
        with TestClient(app) as client:
            missing = client.post("/api/bus/events", json=request_body)
            wrong = client.post(
                "/api/bus/events",
                headers={"X-Simulation-Event-Bus-Secret": "wrong-secret"},
                json=request_body,
            )
            accepted = client.post("/api/bus/events", headers=EVENT_BUS_HEADER, json=request_body)
            unauthenticated_read = client.get("/api/bus/events?event_type=edge.action")
            authenticated_read = client.get("/api/bus/events?event_type=edge.action", headers=EVENT_BUS_HEADER)

        assert missing.status_code == 401
        assert wrong.status_code == 401
        assert accepted.status_code == 200
        assert unauthenticated_read.status_code == 401
        assert authenticated_read.status_code == 200
        assert authenticated_read.json()["events"][0]["payload"]["action"] == "stop_buying"
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir
        if old_event_secret is None:
            os.environ.pop("SIMULATION_EVENT_BUS_SECRET", None)
        else:
            os.environ["SIMULATION_EVENT_BUS_SECRET"] = old_event_secret


def test_recorder_ingest_publishes_discord_message_event(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    old_event_secret = os.environ.get("SIMULATION_EVENT_BUS_SECRET")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    os.environ["SIMULATION_EVENT_BUS_SECRET"] = EVENT_BUS_SECRET
    try:
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
            ingest = client.post(
                "/api/recorder/dev/ingest-message",
                json={
                    "message_id": "bus-m1",
                    "channel_id": "123",
                    "channel_name": "alerts",
                    "author_id": "a1",
                    "author_name": "Analyst",
                    "discord_timestamp": "2026-06-19T14:30:00+00:00",
                    "content": "BTO SPY 500C 6/21 @ 1.25",
                },
            )
            events = client.get(
                "/api/bus/events?event_type=simulation.recording.discord_message",
                headers=EVENT_BUS_HEADER,
            ).json()["events"]

        assert ingest.status_code == 200
        assert ingest.json()["status"] == "recorded"
        assert events[0]["payload"]["message_id"] == "bus-m1"
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir
        if old_event_secret is None:
            os.environ.pop("SIMULATION_EVENT_BUS_SECRET", None)
        else:
            os.environ["SIMULATION_EVENT_BUS_SECRET"] = old_event_secret
