import os

from fastapi.testclient import TestClient

from sentinel_archive.api import create_app


EVENT_BUS_SECRET = "test-event-secret-for-simulation-bus-32chars"
EVENT_BUS_HEADER = {"X-Simulation-Event-Bus-Secret": EVENT_BUS_SECRET}


def test_chrome_bridge_message_records_alert_and_publishes_signal(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    old_event_secret = os.environ.get("SIMULATION_EVENT_BUS_SECRET")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    os.environ["SIMULATION_EVENT_BUS_SECRET"] = EVENT_BUS_SECRET
    try:
        app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
        with TestClient(app) as client:
            response = client.post(
                "/api/discord/chrome-bridge/message",
                json={
                    "event_id": "chrome-spy-1",
                    "channel_id": "123",
                    "channel_name": "mike-alerts",
                    "channel_url": "https://discord.com/channels/1/123",
                    "bridge_target_id": "sentinel-archive",
                    "bridge_target_name": "Sentinel Archive",
                    "author_id": "mike",
                    "author_name": "MikeInvesting [MIKE]",
                    "content": "$SPY\n$744 PUTS\nEXPIRATION 6/22/2026\n$.4 Entry\n@everyone alert",
                    "url": "https://discord.com/channels/1/123/456",
                    "observed_at": "2026-06-22T14:23:00+00:00",
                },
            )
            events = client.get(
                "/api/bus/events?event_type=signal.observed",
                headers=EVENT_BUS_HEADER,
            ).json()["events"]
            messages = client.get("/api/recordings/messages?channel_id=123").json()["messages"]

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "accepted"
        assert body["recording_status"] == "recorded"
        assert body["event_id"] == "chrome-spy-1"
        assert "$SPY" in body["raw_text"]
        assert messages[0]["message_id"] == "chrome-spy-1"
        assert events[0]["payload"]["contract_version"] == "chrome.discord.message.v1"
        assert events[0]["payload"]["bridge_target_id"] == "sentinel-archive"
        assert events[0]["payload"]["channel_url"] == "https://discord.com/channels/1/123"
        assert events[0]["payload"]["raw_text"] == body["raw_text"]
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir
        if old_event_secret is None:
            os.environ.pop("SIMULATION_EVENT_BUS_SECRET", None)
        else:
            os.environ["SIMULATION_EVENT_BUS_SECRET"] = old_event_secret


def test_chrome_bridge_heartbeat_publishes_health_event(tmp_path):
    old_event_dir = os.environ.get("BOT_EVENT_BUS_DIR")
    old_event_secret = os.environ.get("SIMULATION_EVENT_BUS_SECRET")
    os.environ["BOT_EVENT_BUS_DIR"] = str(tmp_path / "events")
    os.environ["SIMULATION_EVENT_BUS_SECRET"] = EVENT_BUS_SECRET
    try:
        app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
        with TestClient(app) as client:
            response = client.post(
                "/api/discord/chrome-bridge/heartbeat",
                json={
                    "status": "ok",
                    "bridge_enabled": True,
                    "channel_id": "123",
                    "channel_name": "mike-alerts",
                    "channel_url": "https://discord.com/channels/1/123",
                    "bridge_target_id": "sentinel-archive",
                    "bridge_target_name": "Sentinel Archive",
                    "observed_at": "2026-06-22T14:23:30+00:00",
                },
            )
            health = client.get("/api/discord/chrome-bridge/health").json()
            events = client.get(
                "/api/bus/events?event_type=bridge.health",
                headers=EVENT_BUS_HEADER,
            ).json()["events"]

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "healthy"
        assert health["healthy"] is True
        assert health["last_heartbeat"]["bridge_target_id"] == "sentinel-archive"
        assert events[0]["payload"]["channel_url"] == "https://discord.com/channels/1/123"
    finally:
        if old_event_dir is None:
            os.environ.pop("BOT_EVENT_BUS_DIR", None)
        else:
            os.environ["BOT_EVENT_BUS_DIR"] = old_event_dir
        if old_event_secret is None:
            os.environ.pop("SIMULATION_EVENT_BUS_SECRET", None)
        else:
            os.environ["SIMULATION_EVENT_BUS_SECRET"] = old_event_secret
