from __future__ import annotations

import re
from typing import Any

from .recorder_models import ParsedAlert, normalize_contract_key, normalize_expiration

BUY_KEYWORDS = (
    "BTO",
    "BUY TO OPEN",
    "BUYING",
    "BOUGHT",
    "BUY",
    "ENTRY",
    "ENTERING",
    "LONG",
    "OPENING",
)
SELL_KEYWORDS = (
    "STC",
    "SELL TO CLOSE",
    "SELLING",
    "SOLD",
    "SELL",
    "TRIM",
    "CLOSE",
    "EXIT",
    "OUT",
)
AVG_DOWN_KEYWORDS = (
    "AVERAGE DOWN",
    "AVG DOWN",
    "AVERAGING",
    "ADD TO",
    "ADDING",
)

OPTION_RE = re.compile(
    r"(?:^|\s)\$?(?P<strike>\d+(?:\.\d+)?)(?P<kind>[CP])\b|"
    r"(?:^|\s)\$?(?P<strike_word>\d+(?:\.\d+)?)\s*(?P<kind_word>CALLS?|PUTS?)\b",
    re.IGNORECASE,
)
EXPIRATION_RE = re.compile(r"\b(?P<expiration>\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})\b")
PRICE_PATTERNS = (
    re.compile(r"@\s*\$?(?P<price>\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\b(?:ENTRY|PRICE|AT|FILL|FILLED)\s*:?\s*\$?(?P<price>\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\$\.(?P<cents>\d{1,2})\b", re.IGNORECASE),
)
ACTION_TICKER_RE = re.compile(
    r"\b(?:BTO|STC|BUY|BOUGHT|SELL|SOLD|TRIM|CLOSE|EXIT|LONG|ENTRY|ENTERING)\s+\$?(?P<ticker>[A-Z]{1,6})\b",
    re.IGNORECASE,
)
CASH_TICKER_RE = re.compile(r"\$(?P<ticker>[A-Z]{1,6})\b")


def build_discord_alert_text(message: Any) -> str:
    parts: list[str] = []
    _append(parts, _get(message, "content"))

    for embed in _get(message, "embeds", []) or []:
        author = _get(embed, "author")
        footer = _get(embed, "footer")
        _append(parts, _get(author, "name"))
        _append(parts, _get(embed, "title"))
        _append(parts, _get(embed, "description"))
        for field in _get(embed, "fields", []) or []:
            _append(parts, _get(field, "name"))
            _append(parts, _get(field, "value"))
        _append(parts, _get(footer, "text"))

    return "\n".join(parts)


def parse_alert_text(raw_text: str, *, message_id: str) -> ParsedAlert:
    text = " ".join(str(raw_text or "").strip().split())
    if not text:
        return ParsedAlert(message_id=message_id, parse_status="unparsed", raw_text="", parse_error="empty_message")

    try:
        if _contains_keyword(text, AVG_DOWN_KEYWORDS):
            parsed = _parse_contract_alert(text, "average_down", require_price=False)
        elif _contains_keyword(text, SELL_KEYWORDS):
            parsed = _parse_sell_alert(text)
        elif _contains_keyword(text, BUY_KEYWORDS):
            parsed = _parse_contract_alert(text, "buy", require_price=True)
        else:
            parsed = _parse_contract_alert(text, "buy", require_price=True)
    except Exception as exc:
        return ParsedAlert(message_id=message_id, parse_status="error", raw_text=text, parse_error=str(exc))

    if not parsed:
        return ParsedAlert(message_id=message_id, parse_status="unparsed", raw_text=text)

    normalized: dict[str, Any] = {}
    expiration = normalize_expiration(parsed["expiration"]) if parsed.get("expiration") else None
    if parsed.get("ticker") and expiration and parsed.get("strike") is not None and parsed.get("option_type"):
        normalized["contract_key"] = normalize_contract_key(
            parsed["ticker"],
            expiration,
            parsed["strike"],
            parsed["option_type"],
        )

    return ParsedAlert(
        message_id=message_id,
        parse_status="parsed",
        raw_text=text,
        action=parsed["alert_type"],
        ticker=parsed["ticker"],
        expiration=expiration,
        strike=parsed.get("strike"),
        option_type=parsed.get("option_type"),
        alert_price=parsed.get("entry_price"),
        sell_percentage=parsed.get("sell_percentage"),
        confidence=_confidence(parsed),
        normalized=normalized,
    )


def _parse_sell_alert(message: str) -> dict[str, Any] | None:
    result = _parse_contract_alert(message, "sell", require_price=False, require_contract=False)
    if not result:
        return None
    if _contains_keyword(message, ("TRIM", "TRIMMING")):
        result["alert_type"] = "trim"
    elif _contains_keyword(message, ("CLOSE", "CLOSING", "EXIT", "EXITING", "OUT")):
        result["alert_type"] = "close"
    result["sell_percentage"] = _extract_sell_percentage(message)
    return result


def _parse_contract_alert(
    message: str,
    alert_type: str,
    *,
    require_price: bool,
    require_contract: bool = True,
) -> dict[str, Any] | None:
    ticker = _extract_ticker(message)
    strike, option_type = _extract_option_contract(message)
    expiration = _extract_expiration(message)
    price = _extract_price(message)

    if not ticker:
        return None
    if require_contract and (strike is None or option_type is None or not expiration):
        return None
    if require_price and price is None:
        return None

    return {
        "alert_type": alert_type,
        "ticker": ticker,
        "strike": strike,
        "option_type": option_type,
        "expiration": expiration,
        "entry_price": price,
        "sell_percentage": None,
    }


def _extract_ticker(message: str) -> str | None:
    cash_match = CASH_TICKER_RE.search(message)
    if cash_match:
        return cash_match.group("ticker").upper()

    action_match = ACTION_TICKER_RE.search(message)
    if action_match:
        return action_match.group("ticker").upper()

    option_match = OPTION_RE.search(message)
    if option_match:
        prefix = message[: option_match.start()].strip()
        tokens = re.findall(r"\b[A-Z]{1,6}\b", prefix.upper())
        ignored = set(BUY_KEYWORDS + SELL_KEYWORDS + AVG_DOWN_KEYWORDS)
        for token in reversed(tokens):
            if token not in ignored:
                return token
    return None


def _extract_option_contract(message: str) -> tuple[float | None, str | None]:
    match = OPTION_RE.search(message)
    if not match:
        return None, None
    strike = match.group("strike") or match.group("strike_word")
    kind = match.group("kind") or match.group("kind_word")
    option_type = "CALL" if kind.upper().startswith("C") else "PUT"
    return float(strike), option_type


def _extract_expiration(message: str) -> str | None:
    match = EXPIRATION_RE.search(message)
    return match.group("expiration") if match else None


def _extract_price(message: str) -> float | None:
    for pattern in PRICE_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        if "cents" in match.groupdict() and match.group("cents") is not None:
            return float(f"0.{match.group('cents')}")
        return float(match.group("price"))
    return None


def _extract_sell_percentage(message: str) -> float:
    if _contains_keyword(message, ("ALL", "CLOSE", "CLOSING", "EXIT", "EXITING", "OUT")):
        return 100.0

    match = re.search(r"\b(?:SELL|TRIM|STC)?\s*(\d{1,3})\s*%", message.upper())
    if match:
        return min(100.0, max(1.0, float(match.group(1))))

    if _contains_keyword(message, ("HALF", "1/2", "ONE HALF")):
        return 50.0
    if _contains_keyword(message, ("QUARTER", "1/4")):
        return 25.0
    return 100.0


def _contains_keyword(message: str, keywords: tuple[str, ...]) -> bool:
    return any(_keyword_regex(keyword).search(message) for keyword in keywords)


def _keyword_regex(keyword: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in str(keyword).strip().split()]
    body = r"\s+".join(parts)
    return re.compile(rf"(?<![A-Z0-9]){body}(?![A-Z0-9])", re.IGNORECASE)


def _confidence(parsed: dict[str, Any]) -> str:
    has_contract = parsed.get("strike") is not None and parsed.get("option_type") and parsed.get("expiration")
    has_price = parsed.get("entry_price") is not None
    if parsed.get("ticker") and has_contract and has_price:
        return "high"
    if parsed.get("ticker") and has_contract:
        return "medium"
    return "low"


def _append(parts: list[str], value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if text:
        parts.append(text)


def _get(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)
