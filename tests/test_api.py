from fastapi.testclient import TestClient

from simulation_engine.api import create_app


def test_edge_and_pulse_contract_endpoints_are_available():
    client = TestClient(create_app())

    assert client.get("/api/live").json()["status"] == "live"
    assert client.get("/api/ready").json()["ready"] is True
    assert client.get("/api/edge/status", headers={"X-API-Key": "local-sim-key"}).json()["api_key_configured"] is True
    assert client.get("/api/pulse/handoff/schema").json()["contract_version"] == "edge.pulse.handoff.v1"


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
