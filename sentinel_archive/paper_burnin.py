from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

from .core import SentinelArchive
from .models import MarketBar, SimulationConfig


PORTFOLIO_TICKERS = [
    "SPY",
    "QQQ",
    "IWM",
    "QSI",
    "TSLA",
    "NASA",
    "SOUN",
    "QBTS",
    "QBT",
    "AAPL",
    "MSFT",
    "NFLX",
    "NVDA",
    "AMZN",
    "PLTR",
    "AMD",
    "RKLB",
    "ASTS",
    "META",
    "UWM",
]

EDGE_DISCOVERY_TICKERS = ["LNR", "MU", "SNDK", "INTC", "IRDM", "VSAT", "FLY", "VPG"]
DEFAULT_ALPACA_PAPER_ENDPOINT = "https://paper-api.alpaca.markets/v2"
DEFAULT_ALPACA_MCP_URL = "http://127.0.0.1:8765/mcp"
DEFAULT_PULSE_API_URL = "http://127.0.0.1:8001"
DEFAULT_EDGE_API_URL = "http://127.0.0.1:8000"
DEFAULT_MONITORING_STATE_PATH = "outputs/multi-session-monitoring.jsonl"
BURNIN_CLIENT_ORDER_PREFIX = "sentinel-burnin-"


def _check(
    check_id: str,
    status: str,
    detail: str,
    *,
    evidence: dict[str, Any] | None = None,
    requires_human: bool = False,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": status,
        "detail": detail,
        "requires_human": requires_human,
        "evidence": evidence or {},
    }


def _handoff(symbol: str, action: str, *, key: str, confidence: float = 0.9, **extra: Any) -> dict[str, Any]:
    payload = {
        "contract_version": "edge.pulse.handoff.v1",
        "symbol": symbol,
        "action": action,
        "confidence": confidence,
        "reason": f"paper burn-in drill {key}",
        "mode": "paper",
        "orb_session": "market_open",
        "idempotency_key": f"edge:{symbol}:{action}:market_open:{key}",
        "source": "sentinel_edge",
        "created_at": time.time(),
        "metadata": {},
    }
    payload.update(extra)
    return payload


def _import_restart_bars(engine: SentinelArchive) -> str:
    session = engine.import_bars(
        "paper burn-in restart drill",
        [
            MarketBar(timestamp="2026-06-22T13:30:00Z", symbol="SPY", open=100, high=101, low=99, close=100, volume=1000),
            MarketBar(timestamp="2026-06-22T13:31:00Z", symbol="SPY", open=100, high=102, low=99, close=101, volume=1000),
        ],
        source="burn_in_fixture",
    )
    return session.session_id


def run_simulator_burn_in() -> dict[str, Any]:
    """Run broker-free drills that exercise the Edge/Pulse handoff contract.

    This deliberately uses Sentinel Archive only. It creates evidence for
    order/risk semantics and fail-safe behavior without touching broker APIs.
    """

    checks: list[dict[str, Any]] = []

    lifecycle = SentinelArchive()
    lifecycle.bot_running = True
    lifecycle.bot_paused = False
    lifecycle.bot_running = False
    checks.append(
        _check(
            "simulator_bot_lifecycle",
            "pass",
            "Simulation bot lifecycle state can be started and stopped without broker execution.",
            evidence={"started": True, "stopped": True, "execution": "simulation"},
        )
    )

    fill_engine = SentinelArchive(SimulationConfig(fill_ratio=1.0, max_allocation_pct=10.0, slippage_bps=5.0))
    fill_engine.current_prices["SPY"] = 100.0
    buy = fill_engine.process_handoff(
        _handoff("SPY", "buy", key="buy-fill", metadata={"price": 100.0, "quantity": 5.0})
    )
    position = fill_engine.account.positions.get("SPY")
    checks.append(
        _check(
            "paper_buy_fill",
            "pass" if buy["accepted"] and position else "fail",
            "Paper buy handoff opens a simulated position and records fill evidence.",
            evidence={
                "accepted": buy["accepted"],
                "status": buy["status"],
                "execution": "simulation",
                "quantity": position.quantity if position else 0.0,
                "avg_entry": position.avg_entry if position else None,
            },
        )
    )

    partial_engine = SentinelArchive(SimulationConfig(fill_ratio=0.5, max_allocation_pct=10.0))
    partial_engine.current_prices["SPY"] = 100.0
    partial = partial_engine.process_handoff(
        _handoff("SPY", "buy", key="partial-fill", metadata={"price": 100.0, "quantity": 10.0})
    )
    partial_position = partial_engine.account.positions.get("SPY")
    checks.append(
        _check(
            "partial_fill",
            "pass" if partial["accepted"] and partial_position and partial_position.quantity == 5.0 else "fail",
            "Simulation fill ratio can force a partial fill for paper burn-in evidence.",
            evidence={
                "accepted": partial["accepted"],
                "fill_ratio": 0.5,
                "requested_quantity": 10.0,
                "filled_quantity": partial_position.quantity if partial_position else 0.0,
            },
        )
    )

    duplicate_first = fill_engine.process_handoff(
        _handoff("SPY", "buy", key="duplicate", metadata={"price": 100.0, "quantity": 1.0})
    )
    duplicate_second = fill_engine.process_handoff(
        _handoff("SPY", "buy", key="duplicate", metadata={"price": 100.0, "quantity": 1.0})
    )
    checks.append(
        _check(
            "duplicate_idempotency",
            "pass" if duplicate_first["handoff_id"] == duplicate_second["handoff_id"] and duplicate_second["reason"] == "duplicate" else "fail",
            "Duplicate idempotency keys do not apply side effects twice.",
            evidence={
                "first_status": duplicate_first["status"],
                "duplicate_reason": duplicate_second["reason"],
                "handoff_id": duplicate_second["handoff_id"],
            },
        )
    )

    confidence_engine = SentinelArchive(SimulationConfig(reject_below_confidence=0.8))
    confidence_engine.current_prices["SPY"] = 100.0
    low_confidence = confidence_engine.process_handoff(
        _handoff("SPY", "buy", key="low-confidence", confidence=0.3, metadata={"price": 100.0, "quantity": 1.0})
    )
    checks.append(
        _check(
            "low_confidence_rejection",
            "pass" if low_confidence["reason"] == "confidence_below_threshold" else "fail",
            "Low-confidence handoffs are rejected before simulated execution.",
            evidence=low_confidence,
        )
    )

    stop_engine = SentinelArchive(SimulationConfig(max_allocation_pct=10.0))
    stop_engine.current_prices["SPY"] = 100.0
    stop = stop_engine.process_handoff(_handoff("SPY", "stop_buying", key="stop-buying"))
    blocked_buy = stop_engine.process_handoff(
        _handoff("SPY", "buy", key="stop-buying-buy", metadata={"price": 100.0, "quantity": 1.0})
    )
    checks.append(
        _check(
            "stop_buying_rejection",
            "pass" if stop["accepted"] and blocked_buy["reason"] == "ticker_disabled" else "fail",
            "Stop-buying disables future buys for the ticker.",
            evidence={"stop": stop, "blocked_buy": blocked_buy},
        )
    )

    trailing_engine = SentinelArchive(SimulationConfig(default_trailing_percent=5.0, max_allocation_pct=10.0))
    session = trailing_engine.import_bars(
        "paper burn-in trailing drill",
        [
            MarketBar(timestamp="2026-06-22T13:30:00Z", symbol="SPY", open=100, high=100, low=100, close=100, volume=1000),
            MarketBar(timestamp="2026-06-22T13:31:00Z", symbol="SPY", open=100, high=110, low=109, close=110, volume=1000),
            MarketBar(timestamp="2026-06-22T13:32:00Z", symbol="SPY", open=110, high=110, low=104, close=104, volume=1000),
        ],
        source="burn_in_fixture",
    )
    trailing_engine.start_replay(session.session_id, speed=1.0, loop=False)
    trailing_engine.step()
    trailing_engine.process_handoff(_handoff("SPY", "buy", key="trail-buy", metadata={"quantity": 1.0}))
    trailing_engine.process_handoff(
        _handoff("SPY", "trailing_stop", key="trail-enable", stop_type="trailing", trailing_percent=5.0)
    )
    trailing_engine.step()
    trailing_engine.step()
    checks.append(
        _check(
            "trailing_stop_exit",
            "pass" if "SPY" not in trailing_engine.account.positions else "fail",
            "Trailing stop exits the simulated position when low crosses the high-water floor.",
            evidence={"open_positions": sorted(trailing_engine.account.positions), "decisions": trailing_engine.decisions[:3]},
        )
    )

    restart_engine = SentinelArchive()
    restart_session_id = _import_restart_bars(restart_engine)
    restart_engine.start_replay(restart_session_id, speed=1.0, loop=False)
    restart_engine.step()
    saved_index = restart_engine.replay.index
    restored = SentinelArchive()
    restored.sessions = dict(restart_engine.sessions)
    restored.bars = dict(restart_engine.bars)
    restored.replay = restart_engine.replay.model_copy(deep=True)
    restored.current_prices = dict(restart_engine.current_prices)
    restored.step()
    checks.append(
        _check(
            "restart_state_reload",
            "pass" if saved_index == 1 and restored.replay.index == 2 else "fail",
            "Replay state can be copied into a fresh engine and continue stepping.",
            evidence={"saved_index": saved_index, "restored_index": restored.replay.index},
        )
    )

    live_mode = fill_engine.process_handoff(
        {
            **_handoff("SPY", "buy", key="live-reject", metadata={"price": 100.0, "quantity": 1.0}),
            "mode": "live",
        }
    )
    checks.append(
        _check(
            "live_mode_rejected",
            "pass" if live_mode["reason"] == "live_mode_not_supported" else "fail",
            "Sentinel Archive rejects live handoffs so test runs cannot leak into broker execution.",
            evidence=live_mode,
        )
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "simulation",
        "portfolio_tickers": PORTFOLIO_TICKERS,
        "edge_discovery_tickers": EDGE_DISCOVERY_TICKERS,
        "summary": _summarize(checks),
        "checks": checks,
    }


def evaluate_broker_paper_readiness(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Return broker-paper checks that fail closed until credentials are present.

    The function never submits an order. It only determines whether the
    environment has enough operator-supplied material for a later broker-paper
    drill to be run deliberately.
    """

    env = env if env is not None else os.environ
    alpaca_key = str(env.get("ALPACA_API_KEY") or env.get("APCA_API_KEY_ID") or "").strip()
    alpaca_secret = str(env.get("ALPACA_API_SECRET") or env.get("APCA_API_SECRET_KEY") or "").strip()
    paper_value = str(env.get("ALPACA_PAPER") or env.get("APCA_API_PAPER") or "true").strip().lower()
    pulse_url = str(env.get("PULSE_API_URL") or "").strip()
    edge_secret = str(env.get("EDGE_OPERATOR_ACTION_SECRET") or "").strip()
    operator_signoff = str(env.get("SENTINEL_OPERATOR_SIGNOFF") or "").strip()
    has_credentials = bool(alpaca_key and alpaca_secret and paper_value in {"1", "true", "yes", "on"})

    checks = [
        _check(
            "broker_paper_credentials",
            "pass" if has_credentials else "blocked",
            "Alpaca paper credentials are present and explicitly marked paper." if has_credentials else "Missing Alpaca paper credentials or paper flag.",
            evidence={
                "alpaca_key_present": bool(alpaca_key),
                "alpaca_secret_present": bool(alpaca_secret),
                "paper_flag": paper_value or None,
            },
            requires_human=not has_credentials,
        ),
        _check(
            "pulse_paper_stack_target",
            "pass" if pulse_url else "blocked",
            "Pulse API target is configured." if pulse_url else "PULSE_API_URL is not configured for exact-stack paper burn-in.",
            evidence={"pulse_api_url_present": bool(pulse_url)},
            requires_human=not bool(pulse_url),
        ),
        _check(
            "edge_operator_secret",
            "pass" if edge_secret else "blocked",
            "Edge operator secret is configured for protected control routes." if edge_secret else "EDGE_OPERATOR_ACTION_SECRET is missing.",
            evidence={"edge_operator_secret_present": bool(edge_secret)},
            requires_human=not bool(edge_secret),
        ),
        _check(
            "broker_paper_order_lifecycle",
            "blocked",
            "Requires a deliberate broker-paper drill with credentials; this automation will not place orders implicitly.",
            evidence={"planned_events": ["submit", "reject", "fill", "cancel", "reconcile"]},
            requires_human=True,
        ),
        _check(
            "broker_partial_fill_cancel_drill",
            "blocked",
            "Partial-fill/cancel behavior depends on broker paper market conditions and must be run only after operator authorization.",
            evidence={"safe_without_credentials": True},
            requires_human=True,
        ),
        _check(
            "multi_session_monitoring",
            "blocked",
            "Automation can append evidence across sessions, but real burn-in requires elapsed market sessions.",
            evidence={"minimum_sessions_recommended": 5},
            requires_human=True,
        ),
        _check(
            "operator_signoff",
            "pass" if operator_signoff else "blocked",
            "Operator signoff token is present." if operator_signoff else "Human operator has not signed off on live readiness.",
            evidence={"signoff_present": bool(operator_signoff)},
            requires_human=not bool(operator_signoff),
        ),
    ]
    return checks


def _alpaca_credentials(env: Mapping[str, str]) -> dict[str, str]:
    key = str(env.get("ALPACA_API_KEY") or env.get("APCA_API_KEY_ID") or "").strip()
    secret = str(env.get("ALPACA_API_SECRET") or env.get("APCA_API_SECRET_KEY") or "").strip()
    paper = str(env.get("ALPACA_PAPER") or env.get("APCA_API_PAPER") or "true").strip().lower()
    endpoint = str(env.get("ALPACA_ENDPOINT") or env.get("APCA_API_BASE_URL") or DEFAULT_ALPACA_PAPER_ENDPOINT).strip()
    return {"key": key, "secret": secret, "paper": paper, "endpoint": endpoint}


def _alpaca_base_and_prefix(endpoint: str) -> tuple[str, str]:
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/v2"):
        return normalized[: -len("/v2")], "/v2"
    return normalized, "/v2"


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _burnin_open_orders(orders: Any) -> list[dict[str, Any]]:
    if not isinstance(orders, list):
        return []
    return [
        order
        for order in orders
        if isinstance(order, dict) and str(order.get("client_order_id", "")).startswith(BURNIN_CLIENT_ORDER_PREFIX)
    ]


def _read_open_orders_until_burnin_clears(
    client: httpx.Client,
    prefix: str,
    *,
    transport: httpx.BaseTransport | None = None,
    attempts: int = 3,
) -> tuple[httpx.Response, Any, list[dict[str, Any]], int]:
    last_response: httpx.Response | None = None
    last_orders: Any = []
    burnin_orders: list[dict[str, Any]] = []
    reads = 0

    for attempt in range(attempts):
        reads += 1
        last_response = client.get(f"{prefix}/orders", params={"status": "open", "limit": 100})
        last_orders = last_response.json() if last_response.status_code == 200 else []
        burnin_orders = _burnin_open_orders(last_orders)
        if last_response.status_code != 200 or not burnin_orders:
            break
        if transport is None and attempt < attempts - 1:
            time.sleep(0.5)

    assert last_response is not None
    return last_response, last_orders, burnin_orders, reads


def _wait_for_order(
    client: httpx.Client,
    prefix: str,
    order_id: str,
    *,
    transport: httpx.BaseTransport | None = None,
    attempts: int = 6,
) -> dict[str, Any]:
    order: dict[str, Any] = {"id": order_id}
    for attempt in range(attempts):
        response = client.get(f"{prefix}/orders/{order_id}")
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict):
                order = payload
                if str(order.get("status", "")).lower() in {"filled", "canceled", "cancelled", "rejected", "expired"}:
                    break
        if transport is None and attempt < attempts - 1:
            time.sleep(1)
    return order


def run_alpaca_paper_order_drill(
    env: Mapping[str, str] | None = None,
    *,
    allow_orders: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Run a small Alpaca paper-order drill when explicitly allowed.

    The drill avoids marketable orders. It submits a tiny far-away limit order,
    cancels it, verifies cancellation, and separately submits an invalid order
    to prove rejection handling. Evidence intentionally excludes credentials.
    """

    env = env if env is not None else os.environ
    credentials = _alpaca_credentials(env)
    paper_flag = credentials["paper"] in {"1", "true", "yes", "on"}
    has_credentials = bool(credentials["key"] and credentials["secret"])
    checks: list[dict[str, Any]] = []

    if not has_credentials or not paper_flag:
        return [
            _check(
                "alpaca_account_preflight",
                "blocked",
                "Alpaca paper credentials or paper flag are missing.",
                evidence={
                    "key_present": bool(credentials["key"]),
                    "secret_present": bool(credentials["secret"]),
                    "paper_flag": credentials["paper"] or None,
                },
                requires_human=True,
            ),
            _check(
                "alpaca_paper_order_cancel",
                "blocked",
                "Paper order/cancel drill is blocked until credentials are present.",
                requires_human=True,
            ),
            _check(
                "alpaca_controlled_rejection",
                "blocked",
                "Controlled rejection drill is blocked until credentials are present.",
                requires_human=True,
            ),
        ]

    base_url, prefix = _alpaca_base_and_prefix(credentials["endpoint"])
    headers = {
        "APCA-API-KEY-ID": credentials["key"],
        "APCA-API-SECRET-KEY": credentials["secret"],
    }

    with httpx.Client(base_url=base_url, headers=headers, timeout=15.0, transport=transport) as client:
        account_response = client.get(f"{prefix}/account")
        account_ok = account_response.status_code == 200
        account = account_response.json() if account_ok else {}
        buying_power = _safe_float(account.get("buying_power"))
        checks.append(
            _check(
                "alpaca_account_preflight",
                "pass" if account_ok else "blocked",
                "Alpaca paper account endpoint responded." if account_ok else "Alpaca paper account endpoint did not respond successfully.",
                evidence={
                    "endpoint": credentials["endpoint"],
                    "paper": True,
                    "http_status": account_response.status_code,
                    "account_status": account.get("status"),
                    "buying_power": buying_power,
                },
                requires_human=not account_ok,
            )
        )

        clock_response = client.get(f"{prefix}/clock")
        clock = clock_response.json() if clock_response.status_code == 200 else {}
        checks.append(
            _check(
                "alpaca_clock_preflight",
                "pass" if clock_response.status_code == 200 else "blocked",
                "Alpaca paper clock endpoint responded." if clock_response.status_code == 200 else "Alpaca paper clock endpoint failed.",
                evidence={
                    "http_status": clock_response.status_code,
                    "market_open": bool(clock.get("is_open", False)),
                    "timestamp": clock.get("timestamp"),
                },
                requires_human=clock_response.status_code != 200,
            )
        )

        open_orders_response = client.get(f"{prefix}/orders", params={"status": "open", "limit": 100})
        open_orders = open_orders_response.json() if open_orders_response.status_code == 200 else []
        checks.append(
            _check(
                "alpaca_open_orders_preflight",
                "pass" if open_orders_response.status_code == 200 else "blocked",
                "Open paper orders were read before the drill." if open_orders_response.status_code == 200 else "Could not read open paper orders.",
                evidence={
                    "http_status": open_orders_response.status_code,
                    "open_order_count": len(open_orders) if isinstance(open_orders, list) else None,
                },
                requires_human=open_orders_response.status_code != 200,
            )
        )

        if not allow_orders:
            checks.extend(
                [
                    _check(
                        "alpaca_paper_order_cancel",
                        "blocked",
                        "Explicit allow_orders flag is required before submitting even paper orders.",
                        evidence={"allow_orders": False},
                        requires_human=True,
                    ),
                    _check(
                        "alpaca_controlled_rejection",
                        "blocked",
                        "Explicit allow_orders flag is required before submitting controlled paper rejection orders.",
                        evidence={"allow_orders": False},
                        requires_human=True,
                    ),
                    _check(
                        "alpaca_partial_fill_drill",
                        "blocked",
                        "Partial fills cannot be forced deterministically through Alpaca paper REST.",
                        requires_human=True,
                    ),
                ]
            )
            return checks

        if buying_power < 0.05:
            checks.append(
                _check(
                    "alpaca_paper_order_cancel",
                    "blocked",
                    "Paper account buying power is below the tiny non-marketable order notional.",
                    evidence={"buying_power": buying_power, "minimum_required": 0.05},
                    requires_human=True,
                )
            )
        else:
            client_order_id = f"{BURNIN_CLIENT_ORDER_PREFIX}{int(time.time())}"
            order_response = client.post(
                f"{prefix}/orders",
                json={
                    "symbol": "SPY",
                    "qty": "1",
                    "side": "buy",
                    "type": "limit",
                    "time_in_force": "day",
                    "limit_price": "0.01",
                    "client_order_id": client_order_id,
                },
            )
            order_body = order_response.json() if order_response.content else {}
            order_id = str(order_body.get("id") or "")
            cancel_status = None
            verified_status = None
            if 200 <= order_response.status_code < 300 and order_id:
                cancel_response = client.delete(f"{prefix}/orders/{order_id}")
                cancel_status = cancel_response.status_code
                verify_response = client.get(f"{prefix}/orders/{order_id}")
                if verify_response.status_code == 200:
                    verified_status = verify_response.json().get("status")
            checks.append(
                _check(
                    "alpaca_paper_order_cancel",
                    "pass" if order_id and cancel_status in {200, 202, 204} else "blocked",
                    "Tiny non-marketable paper limit order was submitted and canceled."
                    if order_id and cancel_status in {200, 202, 204}
                    else "Tiny paper order was not accepted or could not be canceled.",
                    evidence={
                        "submit_http_status": order_response.status_code,
                        "order_id_present": bool(order_id),
                        "initial_order_status": order_body.get("status"),
                        "cancel_http_status": cancel_status,
                        "verified_order_status": verified_status,
                    },
                    requires_human=not (order_id and cancel_status in {200, 202, 204}),
                )
            )

        rejection_response = client.post(
            f"{prefix}/orders",
            json={
                "symbol": "SPY",
                "qty": "0",
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
            },
        )
        rejected = rejection_response.status_code >= 400
        checks.append(
            _check(
                "alpaca_controlled_rejection",
                "pass" if rejected else "warn",
                "Invalid paper order was rejected by Alpaca." if rejected else "Invalid paper order was not rejected as expected.",
                evidence={"http_status": rejection_response.status_code},
            )
        )

        positions_response = client.get(f"{prefix}/positions")
        positions = positions_response.json() if positions_response.status_code == 200 else []
        final_orders_response, final_orders, burnin_orders, open_order_reads = _read_open_orders_until_burnin_clears(
            client,
            prefix,
            transport=transport,
        )
        burnin_order_open = bool(burnin_orders)
        reconciliation_ok = (
            positions_response.status_code == 200
            and final_orders_response.status_code == 200
            and not burnin_order_open
        )
        checks.append(
            _check(
                "alpaca_reconciliation_snapshot",
                "pass" if reconciliation_ok else "blocked",
                "Positions and open orders were read after the paper drill, and no burn-in order remains open."
                if reconciliation_ok
                else "Positions or open orders could not be reconciled after the paper drill.",
                evidence={
                    "positions_http_status": positions_response.status_code,
                    "open_orders_http_status": final_orders_response.status_code,
                    "position_count": len(positions) if isinstance(positions, list) else None,
                    "open_order_count": len(final_orders) if isinstance(final_orders, list) else None,
                    "burnin_order_open": burnin_order_open,
                    "burnin_open_order_count": len(burnin_orders),
                    "open_order_reads": open_order_reads,
                },
                requires_human=not reconciliation_ok,
            )
        )

        checks.append(
            _check(
                "alpaca_partial_fill_drill",
                "blocked",
                "Partial fills cannot be forced deterministically through Alpaca paper REST; requires market-session observation.",
                evidence={"paper_order_cancel_drill_completed": any(check["check_id"] == "alpaca_paper_order_cancel" and check["status"] == "pass" for check in checks)},
                requires_human=True,
            )
        )

    return checks


def run_alpaca_market_fill_drill(
    env: Mapping[str, str] | None = None,
    *,
    allow_fills: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Run an explicitly authorized Alpaca paper market fill round trip.

    The drill buys one share in the paper account and immediately sells the
    filled quantity. It is disabled by default because it intentionally creates
    fills, even though those fills are paper-only.
    """

    env = env if env is not None else os.environ
    credentials = _alpaca_credentials(env)
    paper_flag = credentials["paper"] in {"1", "true", "yes", "on"}
    has_credentials = bool(credentials["key"] and credentials["secret"])
    symbol = str(env.get("ALPACA_FILL_SYMBOL") or "SOUN").strip().upper()

    if not has_credentials or not paper_flag:
        return [
            _check(
                "alpaca_market_fill_round_trip",
                "blocked",
                "Alpaca paper credentials or paper flag are missing.",
                evidence={
                    "key_present": bool(credentials["key"]),
                    "secret_present": bool(credentials["secret"]),
                    "paper_flag": credentials["paper"] or None,
                },
                requires_human=True,
            ),
            _check(
                "alpaca_market_fill_reconciliation",
                "blocked",
                "Market-fill reconciliation is blocked until credentials are present.",
                evidence={"symbol": symbol},
                requires_human=True,
            ),
        ]

    if not allow_fills:
        return [
            _check(
                "alpaca_market_fill_round_trip",
                "blocked",
                "Explicit allow_fills flag is required before creating paper market fills.",
                evidence={"allow_fills": False, "symbol": symbol},
                requires_human=True,
            ),
            _check(
                "alpaca_market_fill_reconciliation",
                "blocked",
                "Market-fill reconciliation is blocked because no fill drill was authorized.",
                evidence={"allow_fills": False, "symbol": symbol},
                requires_human=True,
            ),
        ]

    base_url, prefix = _alpaca_base_and_prefix(credentials["endpoint"])
    headers = {
        "APCA-API-KEY-ID": credentials["key"],
        "APCA-API-SECRET-KEY": credentials["secret"],
    }
    checks: list[dict[str, Any]] = []

    with httpx.Client(base_url=base_url, headers=headers, timeout=20.0, transport=transport) as client:
        account_response = client.get(f"{prefix}/account")
        account = account_response.json() if account_response.status_code == 200 else {}
        buying_power = _safe_float(account.get("buying_power"))
        clock_response = client.get(f"{prefix}/clock")
        clock = clock_response.json() if clock_response.status_code == 200 else {}
        market_open = bool(clock.get("is_open"))

        if account_response.status_code != 200 or clock_response.status_code != 200 or not market_open:
            checks.append(
                _check(
                    "alpaca_market_fill_round_trip",
                    "blocked",
                    "Alpaca account and open market clock are required before creating paper fills.",
                    evidence={
                        "account_http_status": account_response.status_code,
                        "clock_http_status": clock_response.status_code,
                        "market_open": market_open,
                        "clock_timestamp": clock.get("timestamp"),
                    },
                    requires_human=True,
                )
            )
            checks.append(
                _check(
                    "alpaca_market_fill_reconciliation",
                    "blocked",
                    "No market-fill drill was run.",
                    evidence={"symbol": symbol},
                    requires_human=True,
                )
            )
            return checks

        if buying_power <= 0:
            checks.append(
                _check(
                    "alpaca_market_fill_round_trip",
                    "blocked",
                    "Paper account buying power is not positive.",
                    evidence={"buying_power": buying_power, "symbol": symbol},
                    requires_human=True,
                )
            )
            checks.append(
                _check(
                    "alpaca_market_fill_reconciliation",
                    "blocked",
                    "No market-fill drill was run.",
                    evidence={"symbol": symbol},
                    requires_human=True,
                )
            )
            return checks

        pre_positions_response = client.get(f"{prefix}/positions")
        pre_positions = pre_positions_response.json() if pre_positions_response.status_code == 200 else []
        pre_symbol_position_qty = 0.0
        if isinstance(pre_positions, list):
            for position in pre_positions:
                if isinstance(position, dict) and str(position.get("symbol", "")).upper() == symbol:
                    pre_symbol_position_qty += _safe_float(position.get("qty"))

        stamp = int(time.time())
        buy_client_order_id = f"{BURNIN_CLIENT_ORDER_PREFIX}fill-buy-{stamp}"
        buy_response = client.post(
            f"{prefix}/orders",
            json={
                "symbol": symbol,
                "qty": "1",
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "client_order_id": buy_client_order_id,
            },
        )
        buy_body = buy_response.json() if buy_response.content else {}
        buy_order_id = str(buy_body.get("id") or "")
        buy_order = _wait_for_order(client, prefix, buy_order_id, transport=transport) if buy_order_id else buy_body
        buy_status = str(buy_order.get("status") or buy_body.get("status") or "").lower()
        filled_qty = str(buy_order.get("filled_qty") or buy_body.get("filled_qty") or "0")
        filled_quantity = _safe_float(filled_qty)

        sell_body: dict[str, Any] = {}
        sell_order: dict[str, Any] = {}
        sell_order_id = ""
        sell_status = ""
        if buy_status == "filled" and filled_quantity > 0:
            sell_response = client.post(
                f"{prefix}/orders",
                json={
                    "symbol": symbol,
                    "qty": str(filled_quantity),
                    "side": "sell",
                    "type": "market",
                    "time_in_force": "day",
                    "client_order_id": f"{BURNIN_CLIENT_ORDER_PREFIX}fill-sell-{stamp}",
                },
            )
            sell_body = sell_response.json() if sell_response.content else {}
            sell_order_id = str(sell_body.get("id") or "")
            sell_order = _wait_for_order(client, prefix, sell_order_id, transport=transport) if sell_order_id else sell_body
            sell_status = str(sell_order.get("status") or sell_body.get("status") or "").lower()

        round_trip_ok = buy_status == "filled" and sell_status == "filled"
        checks.append(
            _check(
                "alpaca_market_fill_round_trip",
                "pass" if round_trip_ok else "blocked",
                "One-share Alpaca paper market buy filled and matching sell filled."
                if round_trip_ok
                else "Alpaca paper market buy/sell fill round trip did not complete.",
                evidence={
                    "symbol": symbol,
                    "buy_submit_http_status": buy_response.status_code,
                    "buy_order_id_present": bool(buy_order_id),
                    "buy_status": buy_status,
                    "buy_filled_qty": filled_qty,
                    "sell_order_id_present": bool(sell_order_id),
                    "sell_status": sell_status,
                    "buy_filled_avg_price": buy_order.get("filled_avg_price") or buy_body.get("filled_avg_price"),
                    "sell_filled_avg_price": sell_order.get("filled_avg_price") or sell_body.get("filled_avg_price"),
                },
                requires_human=not round_trip_ok,
            )
        )

        positions_response = client.get(f"{prefix}/positions")
        positions = positions_response.json() if positions_response.status_code == 200 else []
        orders_response, open_orders, burnin_orders, open_order_reads = _read_open_orders_until_burnin_clears(
            client,
            prefix,
            transport=transport,
        )
        symbol_position_qty = 0.0
        if isinstance(positions, list):
            for position in positions:
                if isinstance(position, dict) and str(position.get("symbol", "")).upper() == symbol:
                    symbol_position_qty += _safe_float(position.get("qty"))
        reconciliation_ok = (
            round_trip_ok
            and pre_positions_response.status_code == 200
            and positions_response.status_code == 200
            and orders_response.status_code == 200
            and abs(symbol_position_qty - pre_symbol_position_qty) < 1e-9
            and not burnin_orders
        )
        checks.append(
            _check(
                "alpaca_market_fill_reconciliation",
                "pass" if reconciliation_ok else "blocked",
                "Post-fill reconciliation returned to the pre-drill position and found no open burn-in orders."
                if reconciliation_ok
                else "Post-fill reconciliation found residual paper state or could not verify cleanup.",
                evidence={
                    "symbol": symbol,
                    "pre_positions_http_status": pre_positions_response.status_code,
                    "positions_http_status": positions_response.status_code,
                    "open_orders_http_status": orders_response.status_code,
                    "pre_symbol_position_qty": pre_symbol_position_qty,
                    "symbol_position_qty": symbol_position_qty,
                    "symbol_position_delta": symbol_position_qty - pre_symbol_position_qty,
                    "open_order_count": len(open_orders) if isinstance(open_orders, list) else None,
                    "burnin_open_order_count": len(burnin_orders),
                    "open_order_reads": open_order_reads,
                },
                requires_human=not reconciliation_ok,
            )
        )

    return checks


def run_pulse_reconnect_drill(
    env: Mapping[str, str] | None = None,
    *,
    allow_reconnect: bool = False,
    broker_id: str = "alpaca",
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Run a controlled Pulse broker disconnect/reconnect drill.

    The drill is intentionally blocked unless explicitly allowed and Pulse is
    stopped, because disconnecting a broker while the trading loop is running
    can create false failures or interrupt order placement.
    """

    env = env if env is not None else os.environ
    pulse_url = str(env.get("PULSE_API_URL") or DEFAULT_PULSE_API_URL).rstrip("/")
    edge_key = str(env.get("PULSE_EDGE_API_KEY") or env.get("EDGE_API_KEY") or "").strip()
    broker_id = broker_id.strip().lower()

    if not allow_reconnect:
        return [
            _check(
                "pulse_broker_disconnect_reconnect",
                "blocked",
                "Explicit allow_reconnect flag is required before disconnecting a Pulse broker.",
                evidence={"pulse_url": pulse_url, "broker_id": broker_id, "allow_reconnect": False},
                requires_human=True,
            )
        ]

    if not edge_key:
        return [
            _check(
                "pulse_broker_disconnect_reconnect",
                "blocked",
                "Pulse Edge API key is required for the broker reconnect drill.",
                evidence={"pulse_url": pulse_url, "broker_id": broker_id, "edge_key_present": False},
                requires_human=True,
            )
        ]

    headers = {"X-API-Key": edge_key}
    with httpx.Client(headers=headers, timeout=15.0, transport=transport) as client:
        health = _get_runtime_json(client, f"{pulse_url}/api/health")
        health_payload = health.get("payload") if isinstance(health.get("payload"), Mapping) else {}
        if not health.get("ok") or health_payload.get("running") is not False:
            return [
                _check(
                    "pulse_broker_disconnect_reconnect",
                    "blocked",
                    "Pulse must be reachable and stopped before the broker reconnect drill.",
                    evidence={
                        "pulse_url": pulse_url,
                        "health_http_status": health.get("http_status"),
                        "running": health_payload.get("running"),
                    },
                    requires_human=True,
                )
            ]

        before = _get_runtime_json(client, f"{pulse_url}/api/edge/brokers/status")
        disconnect = _get_runtime_json(client, f"{pulse_url}/api/edge/brokers/{broker_id}/disconnect", method="POST")
        after_disconnect = _get_runtime_json(client, f"{pulse_url}/api/edge/brokers/status")
        reconnect = _get_runtime_json(client, f"{pulse_url}/api/edge/brokers/reconnect", method="POST")
        after_reconnect = _get_runtime_json(client, f"{pulse_url}/api/edge/brokers/status")

    before_payload = before.get("payload") if isinstance(before.get("payload"), Mapping) else {}
    after_disconnect_payload = after_disconnect.get("payload") if isinstance(after_disconnect.get("payload"), Mapping) else {}
    reconnect_payload = reconnect.get("payload") if isinstance(reconnect.get("payload"), Mapping) else {}
    after_reconnect_payload = after_reconnect.get("payload") if isinstance(after_reconnect.get("payload"), Mapping) else {}

    before_connected = bool((before_payload.get(broker_id) or {}).get("connected"))
    disconnected = not bool((after_disconnect_payload.get(broker_id) or {}).get("connected"))
    reconnect_result = str((reconnect_payload.get("results") or {}).get(broker_id) or "")
    reconnected = bool((after_reconnect_payload.get(broker_id) or {}).get("connected"))
    passed = (
        before.get("ok")
        and disconnect.get("ok")
        and after_disconnect.get("ok")
        and reconnect.get("ok")
        and after_reconnect.get("ok")
        and before_connected
        and disconnected
        and reconnect_result == "connected"
        and reconnected
    )

    return [
        _check(
            "pulse_broker_disconnect_reconnect",
            "pass" if passed else "blocked",
            "Pulse broker disconnect/reconnect drill completed and broker returned connected."
            if passed
            else "Pulse broker disconnect/reconnect drill did not complete cleanly.",
            evidence={
                "pulse_url": pulse_url,
                "broker_id": broker_id,
                "health_http_status": health.get("http_status"),
                "status_before_http_status": before.get("http_status"),
                "disconnect_http_status": disconnect.get("http_status"),
                "status_after_disconnect_http_status": after_disconnect.get("http_status"),
                "reconnect_http_status": reconnect.get("http_status"),
                "status_after_reconnect_http_status": after_reconnect.get("http_status"),
                "before_connected": before_connected,
                "after_disconnect_connected": not disconnected,
                "reconnect_result": reconnect_result,
                "after_reconnect_connected": reconnected,
            },
            requires_human=not passed,
        )
    ]


def _check_status(checks: list[dict[str, Any]], check_id: str) -> str | None:
    for check in reversed(checks):
        if check.get("check_id") == check_id:
            return str(check.get("status"))
    return None


def _promote_broker_order_lifecycle(checks: list[dict[str, Any]]) -> None:
    required = {
        "alpaca_paper_order_cancel",
        "alpaca_controlled_rejection",
        "alpaca_reconciliation_snapshot",
        "alpaca_market_fill_round_trip",
        "alpaca_market_fill_reconciliation",
    }
    if not all(_check_status(checks, check_id) == "pass" for check_id in required):
        return

    for check in checks:
        if check.get("check_id") == "broker_paper_order_lifecycle":
            check.update(
                {
                    "status": "pass",
                    "detail": "Alpaca paper submit/cancel/reject/reconcile and market fill/sell drills passed.",
                    "evidence": {"satisfied_by": sorted(required)},
                    "requires_human": False,
                }
            )
            return


def _json_or_empty(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10] or datetime.now(timezone.utc).date().isoformat()


def _get_runtime_json(client: httpx.Client, url: str, *, method: str = "GET") -> dict[str, Any]:
    try:
        response = client.request(method, url)
        payload = _json_or_empty(response)
        return {
            "http_status": response.status_code,
            "ok": 200 <= response.status_code < 300,
            "payload": payload,
        }
    except Exception as exc:
        return {"http_status": None, "ok": False, "error": str(exc), "payload": {}}


def collect_runtime_monitoring_sample(
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Collect one read-only paper burn-in monitoring sample."""

    env = env if env is not None else os.environ
    credentials = _alpaca_credentials(env)
    base_url, prefix = _alpaca_base_and_prefix(credentials["endpoint"])
    headers = {
        "APCA-API-KEY-ID": credentials["key"],
        "APCA-API-SECRET-KEY": credentials["secret"],
    }
    has_credentials = bool(credentials["key"] and credentials["secret"])
    paper_flag = credentials["paper"] in {"1", "true", "yes", "on"}
    pulse_url = str(env.get("PULSE_API_URL") or DEFAULT_PULSE_API_URL).rstrip("/")
    edge_url = str(env.get("EDGE_API_URL") or DEFAULT_EDGE_API_URL).rstrip("/")

    sample: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "runtime_monitoring_sample",
        "alpaca": {"paper": paper_flag, "credentials_present": has_credentials},
        "pulse": {"base_url": pulse_url},
        "edge": {"base_url": edge_url},
    }

    with httpx.Client(headers=headers if has_credentials else {}, timeout=10.0, transport=transport) as client:
        if has_credentials and paper_flag:
            account = _get_runtime_json(client, f"{base_url}{prefix}/account")
            clock = _get_runtime_json(client, f"{base_url}{prefix}/clock")
            orders = _get_runtime_json(client, f"{base_url}{prefix}/orders?status=open&limit=100")
            positions = _get_runtime_json(client, f"{base_url}{prefix}/positions")

            clock_payload = clock.get("payload") if isinstance(clock.get("payload"), Mapping) else {}
            account_payload = account.get("payload") if isinstance(account.get("payload"), Mapping) else {}
            order_payload = orders.get("payload") if isinstance(orders.get("payload"), list) else []
            position_payload = positions.get("payload") if isinstance(positions.get("payload"), list) else []
            sample["alpaca"].update(
                {
                    "account_http_status": account.get("http_status"),
                    "clock_http_status": clock.get("http_status"),
                    "orders_http_status": orders.get("http_status"),
                    "positions_http_status": positions.get("http_status"),
                    "account_status": account_payload.get("status"),
                    "market_open": bool(clock_payload.get("is_open")),
                    "clock_timestamp": clock_payload.get("timestamp"),
                    "open_order_count": len(order_payload),
                    "position_count": len(position_payload),
                    "burnin_open_order_count": len(_burnin_open_orders(order_payload)),
                    "ok": all(item.get("ok") for item in (account, clock, orders, positions)),
                }
            )
        else:
            sample["alpaca"].update(
                {
                    "ok": False,
                    "blocked_reason": "missing_paper_credentials_or_paper_flag",
                }
            )

        pulse = _get_runtime_json(client, f"{pulse_url}/api/health")
        pulse_payload = pulse.get("payload") if isinstance(pulse.get("payload"), Mapping) else {}
        sample["pulse"].update(
            {
                "http_status": pulse.get("http_status"),
                "ok": pulse.get("ok"),
                "status": pulse_payload.get("status"),
                "running": pulse_payload.get("running"),
                "trading_mode": pulse_payload.get("trading_mode"),
                "brokers_connected": pulse_payload.get("brokers_connected"),
            }
        )

        edge = _get_runtime_json(client, f"{edge_url}/api/health")
        edge_payload = edge.get("payload") if isinstance(edge.get("payload"), Mapping) else {}
        sample["edge"].update(
            {
                "http_status": edge.get("http_status"),
                "ok": edge.get("ok"),
                "status": edge_payload.get("status"),
                "running": edge_payload.get("running"),
                "pulse_available": edge_payload.get("pulse_available"),
                "position_tracking_mode": edge_payload.get("position_tracking_mode"),
            }
        )

    sample["session_date"] = _iso_date(sample["alpaca"].get("clock_timestamp"))
    sample["critical_ok"] = bool(
        sample["alpaca"].get("ok")
        and sample["pulse"].get("ok")
        and sample["edge"].get("ok")
        and sample["alpaca"].get("burnin_open_order_count", 0) == 0
    )
    return sample


def load_monitoring_samples(state_path: str | Path) -> list[dict[str, Any]]:
    path = Path(state_path)
    if not path.exists():
        return []
    samples: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            samples.append(payload)
    return samples


def append_monitoring_sample(sample: dict[str, Any], state_path: str | Path) -> list[dict[str, Any]]:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = load_monitoring_samples(path)
    samples.append(sample)
    with path.open("w", encoding="utf-8") as handle:
        for item in samples:
            handle.write(json.dumps(item, sort_keys=True) + "\n")
    return samples


def evaluate_multi_session_monitoring(
    samples: list[dict[str, Any]],
    *,
    minimum_sessions: int = 5,
) -> dict[str, Any]:
    clean_market_sessions = {
        str(sample.get("session_date") or "")
        for sample in samples
        if sample.get("critical_ok") and (sample.get("alpaca") or {}).get("market_open")
    }
    clean_market_sessions.discard("")
    latest = samples[-1] if samples else {}
    passed = len(clean_market_sessions) >= minimum_sessions
    return _check(
        "multi_session_monitoring",
        "pass" if passed else "blocked",
        "Clean monitoring samples cover the required number of distinct open market sessions."
        if passed
        else "Clean monitoring samples have not yet covered enough distinct open market sessions.",
        evidence={
            "minimum_sessions_required": minimum_sessions,
            "clean_market_sessions": sorted(clean_market_sessions),
            "clean_market_session_count": len(clean_market_sessions),
            "sample_count": len(samples),
            "latest_sample_at": latest.get("generated_at"),
            "latest_critical_ok": latest.get("critical_ok"),
            "latest_market_open": (latest.get("alpaca") or {}).get("market_open") if latest else None,
        },
        requires_human=not passed,
    )


def _replace_check(checks: list[dict[str, Any]], replacement: dict[str, Any]) -> None:
    for index, check in enumerate(checks):
        if check.get("check_id") == replacement.get("check_id"):
            checks[index] = replacement
            return
    checks.append(replacement)


class _McpToolRejected(RuntimeError):
    def __init__(self, message: str, evidence: dict[str, Any]) -> None:
        super().__init__(message)
        self.evidence = evidence


def _parse_mcp_sse_json(content: str) -> dict[str, Any]:
    data = "\n".join(line[5:].strip() for line in content.splitlines() if line.startswith("data:"))
    return json.loads(data or content or "{}")


def _mcp_result_text(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list) and content and isinstance(content[0], Mapping):
        text = content[0].get("text")
        if text:
            return str(text)
    return json.dumps(result, default=str)


def _mcp_broker_rejection(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    error = data.get("error")
    if not isinstance(error, Mapping):
        return None

    detail = error.get("detail")
    message = error.get("message")
    if isinstance(detail, Mapping):
        message = detail.get("message") or message
    return {
        "http_status": error.get("http_status"),
        "message": str(message or "broker rejected MCP tool call"),
        "error": dict(error),
    }


def _mcp_tool_data_or_raise(result: Mapping[str, Any]) -> Any:
    if result.get("isError"):
        text = _mcp_result_text(result)
        raise _McpToolRejected(text, {"message": text, "mcp_is_error": True})

    payload: Any = result.get("structuredContent")
    if payload is None:
        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], Mapping):
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = {"text": text}
    if payload is None:
        payload = dict(result)

    rejection = _mcp_broker_rejection(payload)
    if rejection:
        raise _McpToolRejected(rejection["message"], rejection)
    return payload


def _mcp_initialize(client: httpx.Client, endpoint: str) -> dict[str, str]:
    headers = {"Accept": "application/json, text/event-stream"}
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "sentinel-paper-burnin", "version": "1.0"},
        },
    }
    response = client.post(endpoint, json=initialize, headers=headers)
    response.raise_for_status()
    session_id = response.headers.get("mcp-session-id") or response.headers.get("Mcp-Session-Id")
    if not session_id:
        raise RuntimeError("MCP initialize response did not include a session id")
    headers["Mcp-Session-Id"] = session_id
    client.post(
        endpoint,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=headers,
    )
    return headers


def _mcp_call_tool(
    client: httpx.Client,
    endpoint: str,
    headers: Mapping[str, str],
    rpc_id: int,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    response = client.post(
        endpoint,
        json={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        headers=dict(headers),
    )
    response.raise_for_status()
    payload = _parse_mcp_sse_json(response.text)
    if payload.get("error"):
        raise RuntimeError(json.dumps(payload["error"], default=str))
    return rpc_id + 1, _mcp_tool_data_or_raise(payload.get("result") or {})


def _mcp_data_result(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        data = payload.get("data")
        if isinstance(data, Mapping) and "result" in data:
            return data["result"]
        if data is not None:
            return data
    return payload


def _summarize_mcp_order(order: Any) -> dict[str, Any]:
    data = _mcp_data_result(order)
    if not isinstance(data, Mapping):
        return {}
    return {
        "id": data.get("id"),
        "client_order_id": data.get("client_order_id"),
        "symbol": data.get("symbol"),
        "status": data.get("status"),
        "filled_qty": data.get("filled_qty"),
    }


def run_alpaca_mcp_paper_order_drill(
    env: Mapping[str, str] | None = None,
    *,
    allow_orders: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    """Run the Alpaca MCP paper order drill through a local MCP endpoint.

    The MCP server wraps broker API failures as structured tool payloads in
    some cases. This runner treats those `data.error` payloads as rejections
    rather than successful calls.
    """

    env = env if env is not None else os.environ
    endpoint = str(env.get("ALPACA_MCP_URL") or DEFAULT_ALPACA_MCP_URL).strip()
    checks: list[dict[str, Any]] = []

    if not allow_orders:
        return [
            _check(
                "alpaca_mcp_session",
                "blocked",
                "Alpaca MCP drill was not run because paper order permission was not granted.",
                evidence={"endpoint": endpoint, "allow_orders": False},
                requires_human=True,
            ),
            _check(
                "alpaca_mcp_paper_order_cancel",
                "blocked",
                "Explicit allow_orders flag is required before submitting even paper orders through MCP.",
                evidence={"allow_orders": False},
                requires_human=True,
            ),
        ]

    with httpx.Client(timeout=15.0, transport=transport) as client:
        try:
            headers = _mcp_initialize(client, endpoint)
            rpc_id = 2
            checks.append(
                _check(
                    "alpaca_mcp_session",
                    "pass",
                    "Alpaca MCP endpoint initialized a session.",
                    evidence={"endpoint": endpoint},
                )
            )
        except Exception as exc:
            return [
                _check(
                    "alpaca_mcp_session",
                    "blocked",
                    "Alpaca MCP endpoint could not initialize a session.",
                    evidence={"endpoint": endpoint, "error": str(exc)},
                    requires_human=True,
                )
            ]

        try:
            rpc_id, clock = _mcp_call_tool(client, endpoint, headers, rpc_id, "get_clock")
            rpc_id, open_orders_before = _mcp_call_tool(
                client,
                endpoint,
                headers,
                rpc_id,
                "get_orders",
                {"status": "open", "limit": 100, "symbols": "SPY"},
            )
            rpc_id, positions_before = _mcp_call_tool(client, endpoint, headers, rpc_id, "get_all_positions")
            checks.append(
                _check(
                    "alpaca_mcp_preflight",
                    "pass",
                    "Clock, open orders, and positions were read through Alpaca MCP before the drill.",
                    evidence={
                        "clock": _mcp_data_result(clock),
                        "open_order_count": len(_mcp_data_result(open_orders_before) or []),
                        "position_count": len(_mcp_data_result(positions_before) or []),
                    },
                )
            )
        except Exception as exc:
            checks.append(
                _check(
                    "alpaca_mcp_preflight",
                    "blocked",
                    "Alpaca MCP preflight reads failed.",
                    evidence={"error": str(exc)},
                    requires_human=True,
                )
            )

        client_order_id = f"{BURNIN_CLIENT_ORDER_PREFIX}mcp-{int(time.time())}"
        order_id = ""
        try:
            rpc_id, order = _mcp_call_tool(
                client,
                endpoint,
                headers,
                rpc_id,
                "place_stock_order",
                {
                    "symbol": "SPY",
                    "side": "buy",
                    "qty": "1",
                    "type": "limit",
                    "time_in_force": "day",
                    "limit_price": "1.00",
                    "client_order_id": client_order_id,
                },
            )
            order_summary = _summarize_mcp_order(order)
            order_id = str(order_summary.get("id") or "")
        except Exception as exc:
            order_summary = {"client_order_id": client_order_id, "error": str(exc)}

        duplicate_rejection: dict[str, Any] | None = None
        if order_id:
            try:
                rpc_id, _ = _mcp_call_tool(
                    client,
                    endpoint,
                    headers,
                    rpc_id,
                    "place_stock_order",
                    {
                        "symbol": "SPY",
                        "side": "buy",
                        "qty": "1",
                        "type": "limit",
                        "time_in_force": "day",
                        "limit_price": "1.00",
                        "client_order_id": client_order_id,
                    },
                )
            except _McpToolRejected as exc:
                duplicate_rejection = exc.evidence

        fetched_status = None
        fetched_by_client_status = None
        cancel_ok = False
        final_status = None
        if order_id:
            try:
                rpc_id, fetched = _mcp_call_tool(
                    client,
                    endpoint,
                    headers,
                    rpc_id,
                    "get_order_by_id",
                    {"order_id": order_id, "nested": False},
                )
                fetched_status = _summarize_mcp_order(fetched).get("status")
                rpc_id, fetched_by_client = _mcp_call_tool(
                    client,
                    endpoint,
                    headers,
                    rpc_id,
                    "get_order_by_client_id",
                    {"client_order_id": client_order_id},
                )
                fetched_by_client_status = _summarize_mcp_order(fetched_by_client).get("status")
                rpc_id, _ = _mcp_call_tool(
                    client,
                    endpoint,
                    headers,
                    rpc_id,
                    "cancel_order_by_id",
                    {"order_id": order_id},
                )
                cancel_ok = True
                if transport is None:
                    time.sleep(2)
                rpc_id, final_order = _mcp_call_tool(
                    client,
                    endpoint,
                    headers,
                    rpc_id,
                    "get_order_by_id",
                    {"order_id": order_id, "nested": False},
                )
                final_status = _summarize_mcp_order(final_order).get("status")
            except Exception as exc:
                order_summary["cancel_error"] = str(exc)

        checks.append(
            _check(
                "alpaca_mcp_paper_order_cancel",
                "pass" if order_id and cancel_ok and final_status == "canceled" else "blocked",
                "Tiny non-marketable paper order was submitted through MCP, fetched, canceled, and verified canceled."
                if order_id and cancel_ok and final_status == "canceled"
                else "MCP paper order was not fully submitted, canceled, and verified.",
                evidence={
                    "order": order_summary,
                    "fetched_status": fetched_status,
                    "fetched_by_client_status": fetched_by_client_status,
                    "final_status": final_status,
                },
                requires_human=not (order_id and cancel_ok and final_status == "canceled"),
            )
        )

        duplicate_message = str((duplicate_rejection or {}).get("message", ""))
        checks.append(
            _check(
                "alpaca_mcp_duplicate_rejection",
                "pass" if duplicate_rejection else "fail",
                duplicate_message or "Duplicate MCP paper order was not rejected as expected.",
                evidence=duplicate_rejection or {"client_order_id": client_order_id},
            )
        )

        invalid_rejection: dict[str, Any] | None = None
        try:
            rpc_id, _ = _mcp_call_tool(
                client,
                endpoint,
                headers,
                rpc_id,
                "place_stock_order",
                {
                    "symbol": "SPY",
                    "side": "buy",
                    "qty": "0",
                    "type": "limit",
                    "time_in_force": "day",
                    "limit_price": "1.00",
                    "client_order_id": f"{client_order_id}-invalid",
                },
            )
        except _McpToolRejected as exc:
            invalid_rejection = exc.evidence
        checks.append(
            _check(
                "alpaca_mcp_invalid_order_rejection",
                "pass" if invalid_rejection else "fail",
                str((invalid_rejection or {}).get("message", "Invalid MCP paper order was not rejected as expected.")),
                evidence=invalid_rejection or {},
            )
        )

        try:
            rpc_id, open_orders_after = _mcp_call_tool(
                client,
                endpoint,
                headers,
                rpc_id,
                "get_orders",
                {"status": "open", "limit": 100, "symbols": "SPY"},
            )
            rpc_id, positions_after = _mcp_call_tool(client, endpoint, headers, rpc_id, "get_all_positions")
            rpc_id, fills_after = _mcp_call_tool(
                client,
                endpoint,
                headers,
                rpc_id,
                "get_account_activities",
                {"activity_types": ["FILL"], "page_size": 10, "direction": "desc"},
            )
            open_order_count = len(_mcp_data_result(open_orders_after) or [])
            position_count = len(_mcp_data_result(positions_after) or [])
            fill_count = len(_mcp_data_result(fills_after) or [])
            reconciliation_ok = open_order_count == 0 and position_count == 0
            checks.append(
                _check(
                    "alpaca_mcp_reconciliation",
                    "pass" if reconciliation_ok else "blocked",
                    "MCP post-drill reconciliation found no open SPY orders or positions."
                    if reconciliation_ok
                    else "MCP post-drill reconciliation found residual broker state.",
                    evidence={
                        "open_order_count": open_order_count,
                        "position_count": position_count,
                        "fill_count": fill_count,
                    },
                    requires_human=not reconciliation_ok,
                )
            )
        except Exception as exc:
            checks.append(
                _check(
                    "alpaca_mcp_reconciliation",
                    "blocked",
                    "MCP post-drill reconciliation failed.",
                    evidence={"error": str(exc)},
                    requires_human=True,
                )
            )

    return checks


def _summarize(checks: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"pass": 0, "fail": 0, "blocked": 0, "warn": 0}
    for check in checks:
        status = str(check.get("status", "warn"))
        summary[status] = summary.get(status, 0) + 1
    return summary


def write_burn_in_report(report: dict[str, Any], output_dir: str | Path, *, stem: str | None = None) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stem = stem or f"paper-burnin-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    json_path = output / f"{stem}.json"
    markdown_path = output / f"{stem}.md"

    report = dict(report)
    report["summary"] = _summarize(list(report.get("checks", [])))
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return {"json_path": str(json_path), "markdown_path": str(markdown_path)}


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Paper Burn-In Evidence",
        "",
        f"Generated: `{report.get('generated_at', '')}`",
        f"Mode: `{report.get('mode', '')}`",
        "",
        "## Summary",
        "",
    ]
    summary = report.get("summary", {})
    for status in ("pass", "fail", "blocked", "warn"):
        lines.append(f"- `{status}`: {summary.get(status, 0)}")
    lines.extend(["", "## Checks", "", "| Check | Status | Detail |", "| --- | --- | --- |"])
    for check in report.get("checks", []):
        detail = str(check.get("detail", "")).replace("|", "\\|")
        lines.append(f"| `{check.get('check_id')}` | `{check.get('status')}` | {detail} |")
    lines.extend(
        [
            "",
            "## Readiness Boundary",
            "",
            "This report is evidence for simulator and paper-readiness automation. It is not live-money approval.",
            "Broker-paper order submission, multi-session burn-in, and live trading still require operator authorization and signoff.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_report(
    env: Mapping[str, str] | None = None,
    *,
    run_broker_paper: bool = False,
    run_alpaca_mcp: bool = False,
    run_pulse_reconnect: bool = False,
    allow_paper_orders: bool = False,
    allow_paper_fills: bool = False,
    allow_pulse_reconnect: bool = False,
    record_monitoring_sample: bool = False,
    monitoring_state_path: str | Path = DEFAULT_MONITORING_STATE_PATH,
    minimum_monitoring_sessions: int = 5,
) -> dict[str, Any]:
    report = run_simulator_burn_in()
    report["checks"].extend(evaluate_broker_paper_readiness(env=env))
    if run_broker_paper:
        report["checks"].extend(run_alpaca_paper_order_drill(env=env, allow_orders=allow_paper_orders))
        report["checks"].extend(
            run_alpaca_market_fill_drill(
                env=env,
                allow_fills=allow_paper_orders and allow_paper_fills,
            )
        )
    if run_alpaca_mcp:
        report["checks"].extend(run_alpaca_mcp_paper_order_drill(env=env, allow_orders=allow_paper_orders))
    if run_pulse_reconnect:
        report["checks"].extend(
            run_pulse_reconnect_drill(
                env=env,
                allow_reconnect=allow_pulse_reconnect,
            )
        )
    if record_monitoring_sample:
        sample = collect_runtime_monitoring_sample(env=env)
        samples = append_monitoring_sample(sample, monitoring_state_path)
        report["monitoring_state_path"] = str(monitoring_state_path)
        report["latest_monitoring_sample"] = sample
        _replace_check(
            report["checks"],
            evaluate_multi_session_monitoring(
                samples,
                minimum_sessions=minimum_monitoring_sessions,
            ),
        )
    elif Path(monitoring_state_path).exists():
        samples = load_monitoring_samples(monitoring_state_path)
        _replace_check(
            report["checks"],
            evaluate_multi_session_monitoring(
                samples,
                minimum_sessions=minimum_monitoring_sessions,
            ),
        )
    _promote_broker_order_lifecycle(report["checks"])
    report["summary"] = _summarize(report["checks"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Sentinel paper burn-in automation that is safe without broker credentials.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for JSON and Markdown evidence.")
    parser.add_argument("--stem", default=None, help="Optional output filename stem.")
    parser.add_argument("--run-broker-paper", action="store_true", help="Run Alpaca paper account/order drills when credentials are present.")
    parser.add_argument("--run-alpaca-mcp", action="store_true", help="Run Alpaca paper order drills through a local Alpaca MCP endpoint.")
    parser.add_argument("--run-pulse-reconnect", action="store_true", help="Run the Pulse broker disconnect/reconnect drill when explicitly allowed.")
    parser.add_argument("--alpaca-mcp-url", default=None, help=f"Alpaca MCP endpoint URL. Defaults to {DEFAULT_ALPACA_MCP_URL}.")
    parser.add_argument("--allow-paper-orders", action="store_true", help="Permit tiny Alpaca paper order submission/cancel drills.")
    parser.add_argument("--allow-paper-fills", action="store_true", help="Permit one-share Alpaca paper market buy/sell fill drills during an open market session.")
    parser.add_argument("--allow-pulse-reconnect", action="store_true", help="Permit a controlled Pulse broker disconnect/reconnect drill while Pulse is stopped.")
    parser.add_argument("--record-monitoring-sample", action="store_true", help="Append one read-only Alpaca/Pulse/Edge monitoring sample for multi-session burn-in evidence.")
    parser.add_argument("--monitoring-state-path", default=DEFAULT_MONITORING_STATE_PATH, help="JSONL path for accumulated multi-session monitoring samples.")
    parser.add_argument("--minimum-monitoring-sessions", type=int, default=5, help="Distinct open market sessions required before multi-session monitoring passes.")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    if args.alpaca_mcp_url:
        env["ALPACA_MCP_URL"] = args.alpaca_mcp_url
    report = build_report(
        env=env,
        run_broker_paper=args.run_broker_paper,
        run_alpaca_mcp=args.run_alpaca_mcp,
        run_pulse_reconnect=args.run_pulse_reconnect,
        allow_paper_orders=args.allow_paper_orders,
        allow_paper_fills=args.allow_paper_fills,
        allow_pulse_reconnect=args.allow_pulse_reconnect,
        record_monitoring_sample=args.record_monitoring_sample,
        monitoring_state_path=args.monitoring_state_path,
        minimum_monitoring_sessions=args.minimum_monitoring_sessions,
    )
    written = write_burn_in_report(report, args.output_dir, stem=args.stem)
    print(json.dumps({"summary": report["summary"], **written}, indent=2))
    return 0 if report["summary"].get("fail", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
