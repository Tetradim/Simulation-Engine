from __future__ import annotations

from typing import Any

from .models import PulseHandoffRequest


def pulse_handoff_contract_document() -> dict[str, Any]:
    return {
        "contract_version": "edge.pulse.handoff.v1",
        "endpoint_env": "PULSE_HANDOFF_ENDPOINT",
        "recommended_endpoint": "/api/edge/handoff",
        "request_schema": PulseHandoffRequest.model_json_schema(),
        "transport_headers": {
            "Idempotency-Key": "Same value as request.idempotency_key.",
            "X-Edge-Mode": "Same value as request.mode.",
            "X-Edge-Contract-Version": "Same value as request.contract_version.",
        },
        "response_contract": {
            "accepted_response": {
                "accepted": True,
                "status": "accepted",
                "reason": "pulse_accepted",
                "handoff_id": "idempotency key",
            },
            "rejected_response": {
                "accepted": False,
                "status": "rejected",
                "reason": "risk_limit or price_unavailable",
            },
            "failed_response": {
                "accepted": False,
                "status": "failed",
                "reason": "invalid_handoff_contract",
            },
        },
        "semantics": {
            "global_symbol": "GLOBAL targets portfolio-wide actions such as stop_all and emergency_exit.",
            "trailing_percent": "Percent below high-water mark for trailing_stop and tighten_trailing_stop actions.",
            "idempotency_key": "Duplicate keys return the original handoff id and do not apply side effects twice.",
            "live_mode": "Sentinel Archive rejects live handoffs; live mode is documented only so consumers know it is unsupported here.",
        },
    }
