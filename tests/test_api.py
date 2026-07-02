from fastapi.testclient import TestClient

from sentinel_archive.api import create_app


def test_edge_and_pulse_contract_endpoints_are_available():
    client = TestClient(create_app())

    live = client.get("/api/live").json()
    ready = client.get("/api/ready").json()
    health = client.get("/api/health").json()

    assert live["status"] == "live"
    assert live["mode"] == "simulation"
    assert live["execution"] == "none"
    assert ready["ready"] is True
    assert ready["mode"] == "simulation"
    assert ready["execution"] == "none"
    assert health["mode"] == "simulation"
    assert health["execution"] == "none"
    assert client.get("/api/edge/status", headers={"X-API-Key": "local-sim-key"}).json()["api_key_configured"] is True
    assert client.get("/api/pulse/handoff/schema").json()["contract_version"] == "edge.pulse.handoff.v1"
    assert client.get("/api/pulse/handoff/schema").json()["semantics"]["live_mode"] == "Sentinel Archive rejects live handoffs; live mode is documented only so consumers know it is unsupported here."


def test_import_start_step_and_handoff_flow_through_http():
    client = TestClient(create_app())
    csv_body = {
        "name": "SPY recorded day",
        "csv_text": "timestamp,symbol,open,high,low,close,volume\n2026-06-09T13:30:00Z,SPY,100,100,100,100,1000\n",
    }

    imported = client.post("/api/simulation/replay/import/csv", json=csv_body).json()
    session_id = imported["session"]["session_id"]
    client.post(f"/api/simulation/replay/sessions/{session_id}/start", json={"speed": 1, "loop": False})
    client.post("/api/simulation/replay/step")

    response = client.post(
        "/api/edge/handoff",
        headers={"X-API-Key": "local-sim-key"},
        json={
            "contract_version": "edge.pulse.handoff.v1",
            "symbol": "SPY",
            "action": "buy",
            "confidence": 0.9,
            "reason": "http test",
            "mode": "paper",
            "orb_session": "market_open",
            "idempotency_key": "edge:SPY:buy:market_open:123:http",
            "source": "sentinel_edge",
            "created_at": 1782000000.0,
            "metadata": {},
        },
    )

    assert response.status_code == 202
    assert response.json()["accepted"] is True
    positions = client.get("/api/edge/account/status", headers={"X-API-Key": "local-sim-key"}).json()["positions"]
    assert positions[0]["symbol"] == "SPY"


def test_pulse_bot_lifecycle_facade_requires_api_key_and_updates_sim_state():
    client = TestClient(create_app())

    unauthenticated = client.post("/api/bot/start", json={"enable_all": False})
    assert unauthenticated.status_code == 401

    started = client.post(
        "/api/bot/start",
        headers={"X-API-Key": "local-sim-key"},
        json={"enable_all": False},
    )

    assert started.status_code == 200
    assert started.json()["running"] is True
    assert started.json()["paused"] is False
    assert started.json()["mode"] == "simulation"
    assert client.get("/api/health").json()["bot_running"] is True

    stopped = client.post(
        "/api/bot/stop",
        headers={"X-API-Key": "local-sim-key"},
        json={"disable_all": False},
    )

    assert stopped.status_code == 200
    assert stopped.json()["running"] is False
    assert stopped.json()["paused"] is False
    assert stopped.json()["mode"] == "simulation"
    assert client.get("/api/health").json()["bot_running"] is False
