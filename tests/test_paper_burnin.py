from sentinel_archive.paper_burnin import (
    append_monitoring_sample,
    build_report,
    collect_runtime_monitoring_sample,
    evaluate_broker_paper_readiness,
    evaluate_multi_session_monitoring,
    load_monitoring_samples,
    run_alpaca_market_fill_drill,
    run_alpaca_paper_order_drill,
    run_alpaca_mcp_paper_order_drill,
    run_pulse_reconnect_drill,
    run_simulator_burn_in,
    write_burn_in_report,
)

import json

import httpx
import sentinel_archive.paper_burnin as paper_burnin


def test_simulator_burn_in_reports_order_risk_and_recovery_drills():
    report = run_simulator_burn_in()
    checks = {check["check_id"]: check for check in report["checks"]}

    required = {
        "simulator_bot_lifecycle",
        "paper_buy_fill",
        "partial_fill",
        "duplicate_idempotency",
        "low_confidence_rejection",
        "stop_buying_rejection",
        "trailing_stop_exit",
        "restart_state_reload",
        "live_mode_rejected",
    }

    assert required.issubset(checks)
    assert all(checks[check_id]["status"] == "pass" for check_id in required)
    assert checks["paper_buy_fill"]["evidence"]["execution"] == "simulation"
    assert checks["partial_fill"]["evidence"]["fill_ratio"] == 0.5
    assert checks["duplicate_idempotency"]["evidence"]["duplicate_reason"] == "duplicate"


def test_broker_paper_readiness_fails_closed_without_credentials():
    checks = evaluate_broker_paper_readiness(env={})
    by_id = {check["check_id"]: check for check in checks}

    assert by_id["broker_paper_credentials"]["status"] == "blocked"
    assert by_id["broker_paper_order_lifecycle"]["status"] == "blocked"
    assert by_id["broker_partial_fill_cancel_drill"]["status"] == "blocked"
    assert by_id["operator_signoff"]["status"] == "blocked"
    assert by_id["broker_paper_order_lifecycle"]["requires_human"] is True


def test_burn_in_report_writer_outputs_json_and_markdown(tmp_path):
    report = run_simulator_burn_in()
    report["checks"].extend(evaluate_broker_paper_readiness(env={}))

    written = write_burn_in_report(report, tmp_path, stem="paper-burnin-test")

    assert written["json_path"].endswith("paper-burnin-test.json")
    assert written["markdown_path"].endswith("paper-burnin-test.md")
    markdown = (tmp_path / "paper-burnin-test.md").read_text(encoding="utf-8")
    assert "Paper Burn-In Evidence" in markdown
    assert "broker_paper_credentials" in markdown
    assert "blocked" in markdown


def test_runtime_monitoring_sample_reads_broker_pulse_and_edge_without_secrets():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE"}, request=request)
        if path == "/v2/clock":
            return httpx.Response(
                200,
                json={"is_open": True, "timestamp": "2026-06-24T14:45:00Z"},
                request=request,
            )
        if path == "/v2/orders":
            return httpx.Response(200, json=[], request=request)
        if path == "/v2/positions":
            return httpx.Response(200, json=[], request=request)
        if path == "/api/health" and request.url.port == 8001:
            return httpx.Response(
                200,
                json={"status": "online", "running": True, "trading_mode": "paper", "brokers_connected": 1},
                request=request,
            )
        if path == "/api/health" and request.url.port == 8000:
            return httpx.Response(
                200,
                json={"status": "healthy", "running": True, "pulse_available": True},
                request=request,
            )
        return httpx.Response(404, json={"message": path}, request=request)

    sample = collect_runtime_monitoring_sample(
        env={
            "ALPACA_API_KEY": "paper-key",
            "ALPACA_API_SECRET": "paper-secret",
            "ALPACA_PAPER": "true",
            "ALPACA_ENDPOINT": "https://paper-api.alpaca.markets/v2",
            "PULSE_API_URL": "http://127.0.0.1:8001",
            "EDGE_API_URL": "http://127.0.0.1:8000",
        },
        transport=httpx.MockTransport(handler),
    )

    assert sample["critical_ok"] is True
    assert sample["session_date"] == "2026-06-24"
    assert sample["alpaca"]["open_order_count"] == 0
    assert sample["pulse"]["trading_mode"] == "paper"
    assert sample["edge"]["pulse_available"] is True
    assert "paper-secret" not in str(sample)


def test_multi_session_monitoring_accumulates_distinct_clean_market_sessions(tmp_path):
    state_path = tmp_path / "monitoring.jsonl"
    sample_one = {
        "generated_at": "2026-06-24T14:45:00Z",
        "session_date": "2026-06-24",
        "critical_ok": True,
        "alpaca": {"market_open": True},
    }
    sample_two = {
        "generated_at": "2026-06-25T14:45:00Z",
        "session_date": "2026-06-25",
        "critical_ok": True,
        "alpaca": {"market_open": True},
    }

    append_monitoring_sample(sample_one, state_path)
    samples = append_monitoring_sample(sample_two, state_path)

    assert load_monitoring_samples(state_path) == samples
    blocked = evaluate_multi_session_monitoring(samples, minimum_sessions=3)
    passed = evaluate_multi_session_monitoring(samples, minimum_sessions=2)
    assert blocked["status"] == "blocked"
    assert passed["status"] == "pass"
    assert passed["evidence"]["clean_market_sessions"] == ["2026-06-24", "2026-06-25"]


def test_pulse_reconnect_drill_requires_explicit_flag():
    checks = run_pulse_reconnect_drill(
        env={"PULSE_API_URL": "http://127.0.0.1:8001", "PULSE_EDGE_API_KEY": "edge-key"},
        allow_reconnect=False,
    )

    assert checks[0]["check_id"] == "pulse_broker_disconnect_reconnect"
    assert checks[0]["status"] == "blocked"
    assert checks[0]["requires_human"] is True


def test_pulse_reconnect_drill_disconnects_and_reconnects_when_pulse_is_stopped():
    connected = True

    def broker_status() -> dict:
        return {"alpaca": {"connected": connected, "failed": None, "name": "Alpaca"}}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal connected
        path = request.url.path
        if path == "/api/health":
            return httpx.Response(200, json={"status": "online", "running": False}, request=request)
        if path == "/api/edge/brokers/status":
            return httpx.Response(200, json=broker_status(), request=request)
        if path == "/api/edge/brokers/alpaca/disconnect" and request.method == "POST":
            connected = False
            return httpx.Response(200, json={"status": "disconnected", "broker_id": "alpaca"}, request=request)
        if path == "/api/edge/brokers/reconnect" and request.method == "POST":
            connected = True
            return httpx.Response(200, json={"results": {"alpaca": "connected"}}, request=request)
        return httpx.Response(404, json={"message": path, "method": request.method}, request=request)

    checks = run_pulse_reconnect_drill(
        env={"PULSE_API_URL": "http://127.0.0.1:8001", "PULSE_EDGE_API_KEY": "edge-key"},
        allow_reconnect=True,
        transport=httpx.MockTransport(handler),
    )

    assert checks[0]["status"] == "pass"
    assert checks[0]["requires_human"] is False
    assert checks[0]["evidence"]["before_connected"] is True
    assert checks[0]["evidence"]["after_disconnect_connected"] is False
    assert checks[0]["evidence"]["after_reconnect_connected"] is True
    assert "edge-key" not in str(checks)


def test_alpaca_paper_drill_is_blocked_without_explicit_order_flag():
    checks = run_alpaca_paper_order_drill(
        env={
            "ALPACA_API_KEY": "paper-key",
            "ALPACA_API_SECRET": "paper-secret",
            "ALPACA_PAPER": "true",
        },
        allow_orders=False,
    )
    by_id = {check["check_id"]: check for check in checks}

    assert by_id["alpaca_paper_order_cancel"]["status"] == "blocked"
    assert by_id["alpaca_paper_order_cancel"]["requires_human"] is True


def test_alpaca_paper_drill_submits_cancels_and_records_rejection_without_secrets():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "buying_power": "1000.00"}, request=request)
        if path == "/v2/clock":
            return httpx.Response(200, json={"is_open": False, "timestamp": "2026-06-24T00:00:00Z"}, request=request)
        if path == "/v2/orders" and request.method == "GET":
            return httpx.Response(200, json=[], request=request)
        if path == "/v2/orders" and request.method == "POST":
            body = request.read().decode("utf-8")
            if '"qty":"0"' in body or '"qty": "0"' in body:
                return httpx.Response(422, json={"message": "qty must be greater than 0"}, request=request)
            return httpx.Response(
                200,
                json={"id": "paper-order-1", "status": "accepted", "symbol": "SPY"},
                request=request,
            )
        if path == "/v2/orders/paper-order-1" and request.method == "DELETE":
            return httpx.Response(204, request=request)
        if path == "/v2/orders/paper-order-1" and request.method == "GET":
            return httpx.Response(200, json={"id": "paper-order-1", "status": "canceled"}, request=request)
        if path == "/v2/positions":
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(404, json={"message": path}, request=request)

    transport = httpx.MockTransport(handler)
    checks = run_alpaca_paper_order_drill(
        env={
            "ALPACA_API_KEY": "paper-key",
            "ALPACA_API_SECRET": "paper-secret",
            "ALPACA_PAPER": "true",
            "ALPACA_ENDPOINT": "https://paper-api.alpaca.markets/v2",
        },
        allow_orders=True,
        transport=transport,
    )
    by_id = {check["check_id"]: check for check in checks}

    assert by_id["alpaca_account_preflight"]["status"] == "pass"
    assert by_id["alpaca_paper_order_cancel"]["status"] == "pass"
    assert by_id["alpaca_controlled_rejection"]["status"] == "pass"
    assert "paper-secret" not in str(checks)
    assert requests[0].headers["APCA-API-KEY-ID"] == "paper-key"


def test_alpaca_paper_drill_waits_for_cancelled_burnin_order_to_leave_open_orders():
    open_order_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal open_order_reads

        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "buying_power": "1000.00"}, request=request)
        if path == "/v2/clock":
            return httpx.Response(200, json={"is_open": False, "timestamp": "2026-06-24T00:00:00Z"}, request=request)
        if path == "/v2/orders" and request.method == "GET":
            open_order_reads += 1
            if open_order_reads == 2:
                return httpx.Response(
                    200,
                    json=[{"id": "paper-order-1", "client_order_id": "sentinel-burnin-1", "status": "accepted"}],
                    request=request,
                )
            return httpx.Response(200, json=[], request=request)
        if path == "/v2/orders" and request.method == "POST":
            body = request.read().decode("utf-8")
            if '"qty":"0"' in body or '"qty": "0"' in body:
                return httpx.Response(422, json={"message": "qty must be greater than 0"}, request=request)
            return httpx.Response(
                200,
                json={"id": "paper-order-1", "status": "accepted", "client_order_id": "sentinel-burnin-1"},
                request=request,
            )
        if path == "/v2/orders/paper-order-1" and request.method == "DELETE":
            return httpx.Response(204, request=request)
        if path == "/v2/orders/paper-order-1" and request.method == "GET":
            return httpx.Response(200, json={"id": "paper-order-1", "status": "canceled"}, request=request)
        if path == "/v2/positions":
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(404, json={"message": path}, request=request)

    checks = run_alpaca_paper_order_drill(
        env={
            "ALPACA_API_KEY": "paper-key",
            "ALPACA_API_SECRET": "paper-secret",
            "ALPACA_PAPER": "true",
            "ALPACA_ENDPOINT": "https://paper-api.alpaca.markets/v2",
        },
        allow_orders=True,
        transport=httpx.MockTransport(handler),
    )
    by_id = {check["check_id"]: check for check in checks}

    assert open_order_reads == 3
    assert by_id["alpaca_reconciliation_snapshot"]["status"] == "pass"
    assert by_id["alpaca_reconciliation_snapshot"]["evidence"]["burnin_order_open"] is False


def test_alpaca_market_fill_drill_requires_explicit_fill_flag():
    checks = run_alpaca_market_fill_drill(
        env={
            "ALPACA_API_KEY": "paper-key",
            "ALPACA_API_SECRET": "paper-secret",
            "ALPACA_PAPER": "true",
        },
        allow_fills=False,
    )
    by_id = {check["check_id"]: check for check in checks}

    assert by_id["alpaca_market_fill_round_trip"]["status"] == "blocked"
    assert by_id["alpaca_market_fill_round_trip"]["requires_human"] is True


def test_alpaca_market_fill_drill_buys_and_sells_one_share_without_secrets():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "buying_power": "1000.00"}, request=request)
        if path == "/v2/clock":
            return httpx.Response(200, json={"is_open": True, "timestamp": "2026-06-24T14:45:00Z"}, request=request)
        if path == "/v2/orders" and request.method == "POST":
            body = json.loads(request.read().decode("utf-8"))
            if body["side"] == "buy":
                return httpx.Response(
                    200,
                    json={
                        "id": "buy-order-1",
                        "status": "filled",
                        "symbol": body["symbol"],
                        "side": "buy",
                        "filled_qty": "1",
                        "filled_avg_price": "10.00",
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "id": "sell-order-1",
                    "status": "filled",
                    "symbol": body["symbol"],
                    "side": "sell",
                    "filled_qty": "1",
                    "filled_avg_price": "10.01",
                },
                request=request,
            )
        if path == "/v2/orders/buy-order-1":
            return httpx.Response(200, json={"id": "buy-order-1", "status": "filled", "filled_qty": "1"}, request=request)
        if path == "/v2/orders/sell-order-1":
            return httpx.Response(200, json={"id": "sell-order-1", "status": "filled", "filled_qty": "1"}, request=request)
        if path == "/v2/positions":
            return httpx.Response(200, json=[], request=request)
        if path == "/v2/orders" and request.method == "GET":
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(404, json={"message": path}, request=request)

    checks = run_alpaca_market_fill_drill(
        env={
            "ALPACA_API_KEY": "paper-key",
            "ALPACA_API_SECRET": "paper-secret",
            "ALPACA_PAPER": "true",
            "ALPACA_FILL_SYMBOL": "SOUN",
        },
        allow_fills=True,
        transport=httpx.MockTransport(handler),
    )
    by_id = {check["check_id"]: check for check in checks}

    assert by_id["alpaca_market_fill_round_trip"]["status"] == "pass"
    assert by_id["alpaca_market_fill_reconciliation"]["status"] == "pass"
    assert "paper-secret" not in str(checks)
    submitted_bodies = [
        json.loads(request.content.decode("utf-8"))
        for request in requests
        if request.url.path == "/v2/orders" and request.method == "POST"
    ]
    assert [body["side"] for body in submitted_bodies] == ["buy", "sell"]
    assert {body["symbol"] for body in submitted_bodies} == {"SOUN"}


def test_alpaca_market_fill_drill_reconciles_existing_symbol_position():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "buying_power": "1000.00"}, request=request)
        if path == "/v2/clock":
            return httpx.Response(200, json={"is_open": True, "timestamp": "2026-06-24T14:45:00Z"}, request=request)
        if path == "/v2/orders" and request.method == "POST":
            body = json.loads(request.read().decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": f"{body['side']}-order-1",
                    "status": "filled",
                    "symbol": body["symbol"],
                    "side": body["side"],
                    "filled_qty": body["qty"],
                    "filled_avg_price": "10.00",
                },
                request=request,
            )
        if path in {"/v2/orders/buy-order-1", "/v2/orders/sell-order-1"}:
            order_id = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"id": order_id, "status": "filled", "filled_qty": "1"}, request=request)
        if path == "/v2/positions":
            return httpx.Response(200, json=[{"symbol": "SOUN", "qty": "3.9557"}], request=request)
        if path == "/v2/orders" and request.method == "GET":
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(404, json={"message": path}, request=request)

    checks = run_alpaca_market_fill_drill(
        env={
            "ALPACA_API_KEY": "paper-key",
            "ALPACA_API_SECRET": "paper-secret",
            "ALPACA_PAPER": "true",
            "ALPACA_FILL_SYMBOL": "SOUN",
        },
        allow_fills=True,
        transport=httpx.MockTransport(handler),
    )
    by_id = {check["check_id"]: check for check in checks}

    assert by_id["alpaca_market_fill_round_trip"]["status"] == "pass"
    reconciliation = by_id["alpaca_market_fill_reconciliation"]
    assert reconciliation["status"] == "pass"
    assert reconciliation["evidence"]["pre_symbol_position_qty"] == 3.9557
    assert reconciliation["evidence"]["symbol_position_qty"] == 3.9557
    assert reconciliation["evidence"]["symbol_position_delta"] == 0.0


def test_build_report_promotes_broker_lifecycle_after_fill_cancel_reject_and_reconcile(monkeypatch):
    def fake_order_drill(**_kwargs):
        return [
            {"check_id": "alpaca_paper_order_cancel", "status": "pass", "detail": "", "requires_human": False, "evidence": {}},
            {"check_id": "alpaca_controlled_rejection", "status": "pass", "detail": "", "requires_human": False, "evidence": {}},
            {"check_id": "alpaca_reconciliation_snapshot", "status": "pass", "detail": "", "requires_human": False, "evidence": {}},
        ]

    def fake_fill_drill(**_kwargs):
        return [
            {"check_id": "alpaca_market_fill_round_trip", "status": "pass", "detail": "", "requires_human": False, "evidence": {}},
            {"check_id": "alpaca_market_fill_reconciliation", "status": "pass", "detail": "", "requires_human": False, "evidence": {}},
        ]

    monkeypatch.setattr(paper_burnin, "run_alpaca_paper_order_drill", fake_order_drill)
    monkeypatch.setattr(paper_burnin, "run_alpaca_market_fill_drill", fake_fill_drill)

    report = paper_burnin.build_report(
        env={
            "ALPACA_API_KEY": "paper-key",
            "ALPACA_API_SECRET": "paper-secret",
            "ALPACA_PAPER": "true",
            "PULSE_API_URL": "http://127.0.0.1:8001",
            "EDGE_OPERATOR_ACTION_SECRET": "operator-secret",
        },
        run_broker_paper=True,
        allow_paper_orders=True,
        allow_paper_fills=True,
    )
    by_id = {check["check_id"]: check for check in report["checks"]}

    assert by_id["broker_paper_order_lifecycle"]["status"] == "pass"
    assert by_id["broker_paper_order_lifecycle"]["requires_human"] is False


def test_alpaca_mcp_paper_drill_classifies_structured_broker_rejections():
    canceled = False
    submitted_client_order_id = None

    def sse(payload: dict, *, session_id: str | None = None) -> httpx.Response:
        headers = {"content-type": "text/event-stream"}
        if session_id:
            headers["mcp-session-id"] = session_id
        return httpx.Response(200, text=f"event: message\ndata: {json.dumps(payload)}\n\n", headers=headers)

    def tool_result(data: dict) -> dict:
        return {"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": data, "isError": False}}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal canceled, submitted_client_order_id

        payload = json.loads(request.read().decode("utf-8"))
        method = payload.get("method")
        if method == "initialize":
            return sse(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "serverInfo": {"name": "Alpaca MCP Server", "version": "3.4.2"},
                    },
                },
                session_id="test-session",
            )
        if method == "notifications/initialized":
            return httpx.Response(202)

        assert method == "tools/call"
        name = payload["params"]["name"]
        args = payload["params"].get("arguments", {})

        if name == "get_clock":
            return sse(tool_result({"data": {"is_open": False}}))
        if name == "get_orders":
            return sse(tool_result({"data": {"result": []}}))
        if name == "get_all_positions":
            return sse(tool_result({"data": {"result": []}}))
        if name == "get_account_activities":
            return sse(tool_result({"data": {"result": []}}))
        if name == "place_stock_order":
            if args.get("qty") == "0":
                return sse(tool_result({"data": {"error": {"http_status": 422, "detail": {"message": "qty must be > 0"}}}}))
            if submitted_client_order_id == args.get("client_order_id"):
                return sse(
                    tool_result(
                        {
                            "data": {
                                "error": {
                                    "http_status": 422,
                                    "detail": {"message": "client_order_id must be unique"},
                                }
                            }
                        }
                    )
                )
            submitted_client_order_id = args.get("client_order_id")
            return sse(
                tool_result(
                    {
                        "data": {
                            "id": "mcp-order-1",
                            "client_order_id": submitted_client_order_id,
                            "symbol": "SPY",
                            "status": "accepted",
                            "filled_qty": "0",
                        }
                    }
                )
            )
        if name == "get_order_by_id":
            return sse(
                tool_result(
                    {
                        "data": {
                            "id": "mcp-order-1",
                            "client_order_id": submitted_client_order_id,
                            "symbol": "SPY",
                            "status": "canceled" if canceled else "accepted",
                            "filled_qty": "0",
                        }
                    }
                )
            )
        if name == "get_order_by_client_id":
            return sse(
                tool_result(
                    {
                        "data": {
                            "id": "mcp-order-1",
                            "client_order_id": submitted_client_order_id,
                            "symbol": "SPY",
                            "status": "accepted",
                            "filled_qty": "0",
                        }
                    }
                )
            )
        if name == "cancel_order_by_id":
            canceled = True
            return sse(tool_result({"data": {"text": ""}}))
        raise AssertionError(name)

    checks = run_alpaca_mcp_paper_order_drill(
        env={"ALPACA_MCP_URL": "http://127.0.0.1:8765/mcp"},
        allow_orders=True,
        transport=httpx.MockTransport(handler),
    )
    by_id = {check["check_id"]: check for check in checks}

    assert by_id["alpaca_mcp_session"]["status"] == "pass"
    assert by_id["alpaca_mcp_paper_order_cancel"]["status"] == "pass"
    assert by_id["alpaca_mcp_duplicate_rejection"]["status"] == "pass"
    assert by_id["alpaca_mcp_invalid_order_rejection"]["status"] == "pass"
    assert by_id["alpaca_mcp_reconciliation"]["status"] == "pass"
    assert by_id["alpaca_mcp_duplicate_rejection"]["evidence"]["http_status"] == 422
    assert "client_order_id must be unique" in by_id["alpaca_mcp_duplicate_rejection"]["detail"]


def test_alpaca_mcp_reconciliation_allows_recent_fills_when_no_open_state_remains():
    canceled = False

    def sse(payload: dict, *, session_id: str | None = None) -> httpx.Response:
        headers = {"content-type": "text/event-stream"}
        if session_id:
            headers["mcp-session-id"] = session_id
        return httpx.Response(200, text=f"event: message\ndata: {json.dumps(payload)}\n\n", headers=headers)

    def tool_result(data: dict) -> dict:
        return {"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": data, "isError": False}}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal canceled

        payload = json.loads(request.read().decode("utf-8"))
        method = payload.get("method")
        if method == "initialize":
            return sse({"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-06-18"}}, session_id="test-session")
        if method == "notifications/initialized":
            return httpx.Response(202)

        name = payload["params"]["name"]
        args = payload["params"].get("arguments", {})
        if name == "get_clock":
            return sse(tool_result({"data": {"is_open": True}}))
        if name == "get_orders":
            return sse(tool_result({"data": {"result": []}}))
        if name == "get_all_positions":
            return sse(tool_result({"data": {"result": []}}))
        if name == "get_account_activities":
            return sse(tool_result({"data": {"result": [{"symbol": "SOUN", "activity_type": "FILL"}]}}))
        if name == "place_stock_order":
            if args.get("qty") == "0":
                return sse(tool_result({"data": {"error": {"http_status": 422, "detail": {"message": "qty must be > 0"}}}}))
            if args.get("client_order_id", "").endswith("-duplicate"):
                return sse(tool_result({"data": {"error": {"http_status": 422, "detail": {"message": "client_order_id must be unique"}}}}))
            return sse(
                tool_result(
                    {
                        "data": {
                            "id": "mcp-order-1",
                            "client_order_id": args.get("client_order_id"),
                            "symbol": "SPY",
                            "status": "accepted",
                            "filled_qty": "0",
                        }
                    }
                )
            )
        if name == "get_order_by_id":
            return sse(tool_result({"data": {"id": "mcp-order-1", "symbol": "SPY", "status": "canceled" if canceled else "accepted", "filled_qty": "0"}}))
        if name == "get_order_by_client_id":
            return sse(tool_result({"data": {"id": "mcp-order-1", "symbol": "SPY", "status": "accepted", "filled_qty": "0"}}))
        if name == "cancel_order_by_id":
            canceled = True
            return sse(tool_result({"data": {"text": ""}}))
        raise AssertionError(name)

    checks = run_alpaca_mcp_paper_order_drill(
        env={"ALPACA_MCP_URL": "http://127.0.0.1:8765/mcp"},
        allow_orders=True,
        transport=httpx.MockTransport(handler),
    )
    by_id = {check["check_id"]: check for check in checks}

    assert by_id["alpaca_mcp_reconciliation"]["status"] == "pass"
    assert by_id["alpaca_mcp_reconciliation"]["evidence"]["fill_count"] == 1


def test_alpaca_mcp_paper_drill_requires_explicit_order_flag():
    checks = run_alpaca_mcp_paper_order_drill(
        env={"ALPACA_MCP_URL": "http://127.0.0.1:8765/mcp"},
        allow_orders=False,
    )
    by_id = {check["check_id"]: check for check in checks}

    assert by_id["alpaca_mcp_paper_order_cancel"]["status"] == "blocked"
    assert by_id["alpaca_mcp_paper_order_cancel"]["requires_human"] is True


def test_build_report_can_include_alpaca_mcp_paper_gate():
    report = build_report(
        env={"ALPACA_MCP_URL": "http://127.0.0.1:8765/mcp"},
        run_alpaca_mcp=True,
        allow_paper_orders=False,
    )
    by_id = {check["check_id"]: check for check in report["checks"]}

    assert by_id["alpaca_mcp_session"]["status"] == "blocked"
    assert by_id["alpaca_mcp_paper_order_cancel"]["status"] == "blocked"
    assert report["summary"]["blocked"] >= 2
