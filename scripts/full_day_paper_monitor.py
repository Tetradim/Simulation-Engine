"""Full-day Sentinel paper monitor for Alpaca paper burn-in evidence.

The monitor is intentionally conservative:
- read-only samples run every interval;
- the broker fill drill runs at most once, only after Alpaca reports open;
- Pulse is started through its Edge API if it is not already running;
- all evidence is appended as JSONL for later review.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2").rstrip("/")
EDGE_BASE_URL = os.getenv("EDGE_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
PULSE_BASE_URL = os.getenv("PULSE_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
SENTINEL_CORE_BASE_URL = os.getenv("SENTINEL_CORE_BASE_URL", "http://127.0.0.1:8005").rstrip("/")
PULSE_EDGE_API_KEY = os.getenv("PULSE_EDGE_API_KEY", "")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code >= 400:
            return {"ok": False, "status": response.status_code, "error": response.text[:500]}
        data = response.json()
        return {"ok": True, "status": response.status_code, "data": data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if response.status_code >= 400:
            return {"ok": False, "status": response.status_code, "error": response.text[:500]}
        return {"ok": True, "status": response.status_code, "data": response.json()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _alpaca_headers() -> dict[str, str]:
    key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY are required")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _alpaca_get(path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = requests.get(
            f"{ALPACA_BASE_URL}{path}",
            headers=_alpaca_headers(),
            params=params,
            timeout=12,
        )
        if response.status_code >= 400:
            return {"ok": False, "status": response.status_code, "error": response.text[:500]}
        return {"ok": True, "status": response.status_code, "data": response.json()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _compact_sentinel_core(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not snapshot.get("ok"):
        return snapshot
    data = snapshot.get("data") or {}
    keys = [
        "edgeLive",
        "edgeHealth",
        "edgeReady",
        "pulseHealth",
        "pulseEdgeStatus",
        "pulseBotStatus",
        "pulseBrokers",
        "pulseBrokerStatus",
        "pulseOrders",
        "pulseRiskStatus",
    ]
    return {
        "ok": True,
        "status": snapshot.get("status"),
        "checks": {
            key: {
                "ok": bool((data.get(key) or {}).get("ok")),
                "status": (data.get(key) or {}).get("status"),
                "error": (data.get(key) or {}).get("error"),
            }
            for key in keys
            if key in data
        },
    }


def sample_stack() -> dict[str, Any]:
    pulse_headers = {"X-API-Key": PULSE_EDGE_API_KEY} if PULSE_EDGE_API_KEY else {}
    clock = _alpaca_get("/clock")
    orders = _alpaca_get("/orders", params={"status": "open", "limit": 500})
    positions = _alpaca_get("/positions")
    account = _alpaca_get("/account")
    edge_stats = _get_json(f"{EDGE_BASE_URL}/api/stats")
    automation = _get_json(f"{EDGE_BASE_URL}/api/automation")
    sentinel_core = _compact_sentinel_core(_get_json(f"{SENTINEL_CORE_BASE_URL}/api/sentinel-core/snapshot", timeout=20))

    order_data = orders.get("data") if orders.get("ok") else []
    position_data = positions.get("data") if positions.get("ok") else []
    account_data = account.get("data") if account.get("ok") else {}

    return {
        "timestamp": _utc_now(),
        "alpaca": {
            "clock": clock,
            "open_order_count": len(order_data) if isinstance(order_data, list) else None,
            "open_order_symbols": sorted({str(order.get("symbol")) for order in order_data[:200]})
            if isinstance(order_data, list)
            else [],
            "position_count": len(position_data) if isinstance(position_data, list) else None,
            "position_symbols": sorted({str(position.get("symbol")) for position in position_data})
            if isinstance(position_data, list)
            else [],
            "account": {
                "ok": account.get("ok"),
                "status": account_data.get("status"),
                "trading_blocked": account_data.get("trading_blocked"),
                "account_blocked": account_data.get("account_blocked"),
                "buying_power": account_data.get("buying_power"),
            },
        },
        "pulse": {
            "health": _get_json(f"{PULSE_BASE_URL}/api/health"),
            "bot": _get_json(f"{PULSE_BASE_URL}/api/edge/bot/status", headers=pulse_headers),
            "orders": _get_json(f"{PULSE_BASE_URL}/api/edge/orders", headers=pulse_headers),
            "risk": _get_json(f"{PULSE_BASE_URL}/api/edge/risk/status", headers=pulse_headers),
        },
        "edge": {
            "health": _get_json(f"{EDGE_BASE_URL}/api/health"),
            "ready": _get_json(f"{EDGE_BASE_URL}/api/ready"),
            "stats": edge_stats,
            "automation": automation,
        },
        "sentinel_core": sentinel_core,
    }


def _market_is_open(sample: dict[str, Any]) -> bool:
    clock = (((sample.get("alpaca") or {}).get("clock") or {}).get("data") or {})
    return bool(clock.get("is_open"))


def _next_close(sample: dict[str, Any]) -> datetime | None:
    clock = (((sample.get("alpaca") or {}).get("clock") or {}).get("data") or {})
    raw = clock.get("next_close")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).astimezone(timezone.utc)
    except ValueError:
        return None


def ensure_pulse_running() -> dict[str, Any]:
    headers = {"X-API-Key": PULSE_EDGE_API_KEY} if PULSE_EDGE_API_KEY else {}
    status = _get_json(f"{PULSE_BASE_URL}/api/edge/bot/status", headers=headers)
    data = status.get("data") if status.get("ok") else {}
    if data.get("running"):
        return {"already_running": True, "status": status}
    start = _post_json(f"{PULSE_BASE_URL}/api/edge/bot/start", {"enable_all": False}, headers=headers)
    return {"already_running": False, "start": start}


def run_market_fill_drill(output_dir: Path, monitoring_state_path: Path) -> dict[str, Any]:
    stem = f"paper-burnin-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-market-fill"
    cmd = [
        sys.executable,
        "-m",
        "sentinel_archive.paper_burnin",
        "--run-broker-paper",
        "--allow-paper-orders",
        "--allow-paper-fills",
        "--record-monitoring-sample",
        "--monitoring-state-path",
        str(monitoring_state_path),
        "--stem",
        stem,
        "--output-dir",
        str(output_dir),
    ]
    env = os.environ.copy()
    env.setdefault("EDGE_OPERATOR_ACTION_SECRET", "edge-paper-operator-local")
    env.setdefault("PULSE_API_URL", PULSE_BASE_URL)
    env.setdefault("EDGE_API_URL", EDGE_BASE_URL)
    env.setdefault("ALPACA_ENDPOINT", ALPACA_BASE_URL)
    env.setdefault("ALPACA_PAPER", "true")
    env.setdefault("APCA_API_PAPER", "true")
    started = _utc_now()
    proc = subprocess.run(cmd, cwd=Path.cwd(), env=env, text=True, capture_output=True, timeout=240)
    return {
        "started_at": started,
        "finished_at": _utc_now(),
        "command": [part if "KEY" not in part and "SECRET" not in part else "<redacted>" for part in cmd],
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-2000:],
        "stem": stem,
    }


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor a full Sentinel paper session.")
    parser.add_argument("--output", default="outputs/full-day-stack-monitor-2026-06-25.jsonl")
    parser.add_argument("--monitoring-state", default="outputs/multi-session-monitoring-2026-06-25.jsonl")
    parser.add_argument("--interval-seconds", type=int, default=180)
    parser.add_argument("--post-close-minutes", type=int, default=20)
    parser.add_argument("--max-hours", type=float, default=10.0)
    args = parser.parse_args()

    output_path = Path(args.output)
    monitoring_state_path = Path(args.monitoring_state)
    deadline = datetime.now(timezone.utc) + timedelta(hours=max(args.max_hours, 0.1))
    fill_drill_ran = False
    target_close = None

    while datetime.now(timezone.utc) < deadline:
        sample = sample_stack()
        sample["pulse_start_attempt"] = ensure_pulse_running()

        if _market_is_open(sample):
            target_close = target_close or _next_close(sample)
            if not fill_drill_ran:
                sample["market_fill_drill"] = run_market_fill_drill(
                    output_dir=output_path.parent,
                    monitoring_state_path=monitoring_state_path,
                )
                fill_drill_ran = True

        append_jsonl(output_path, sample)
        print(
            json.dumps(
                {
                    "timestamp": sample["timestamp"],
                    "market_open": _market_is_open(sample),
                    "open_orders": sample["alpaca"]["open_order_count"],
                    "positions": sample["alpaca"]["position_count"],
                    "fill_drill_ran": fill_drill_ran,
                    "output": str(output_path),
                },
                sort_keys=True,
            ),
            flush=True,
        )

        if target_close and datetime.now(timezone.utc) >= target_close + timedelta(minutes=args.post_close_minutes):
            break
        time.sleep(max(30, int(args.interval_seconds)))

    final_sample = sample_stack()
    final_sample["final"] = True
    append_jsonl(output_path, final_sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
