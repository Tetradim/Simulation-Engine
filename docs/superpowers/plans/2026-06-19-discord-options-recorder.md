# Discord Options Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a recorder-only Discord/options market data collection layer that stores channel alerts, parsed option signals, market snapshots, drift events, and timestamped exports for later bot profitability testing.

**Architecture:** Add focused recorder modules beside the existing simulation core. SQLite owns persisted recordings; Discord and CSV/live market sources feed the same normalized event tables. The existing trading simulation and handoff execution paths remain separate and are not called by the Discord recorder.

**Tech Stack:** FastAPI, Pydantic v2, aiosqlite, discord.py, yfinance, React/Vite, pytest.

---

## File Structure

- Create `sentinel_archive/recorder_models.py`: Pydantic models for recorder settings, sources, messages, parsed alerts, market bars, snapshots, drift events, exports, and status.
- Create `sentinel_archive/recording_store.py`: async SQLite schema, migrations, CRUD, masking helpers, and export metadata persistence.
- Create `sentinel_archive/alert_parser.py`: Sentinel Echo-derived message and embed text normalization plus options alert parser.
- Create `sentinel_archive/market_recorder.py`: stock/options CSV parsers, contract key normalization, latest-at-or-before lookup, yfinance live quote lookup, and drift calculation.
- Create `sentinel_archive/discord_recorder.py`: discord.py lifecycle, channel/user filters, message capture, parser invocation, snapshot creation, and status.
- Create `sentinel_archive/recorder_api.py`: FastAPI routes for recorder settings, Discord start/stop/test/status, parser preview, CSV imports, recordings query, replay events, and exports.
- Modify `sentinel_archive/api.py`: initialize recorder store/service in lifespan and mount recorder routes.
- Modify `sentinel_archive/main.py` only if app creation signature must pass a data path.
- Modify `requirements.txt`: add `aiosqlite`, `discord.py`, `yfinance`, and `pandas`.
- Modify `frontend/src/api.ts`: add recorder API types and client methods.
- Modify `frontend/src/App.tsx`: add recorder panels without calling execution APIs.
- Modify `frontend/src/styles.css`: support recorder layout, inbox, channel table, and drift flags.
- Create `tests/test_alert_parser.py`: parser and embed text tests.
- Create `tests/test_recording_store.py`: SQLite persistence and masking tests.
- Create `tests/test_market_recorder.py`: CSV import, contract lookup, yfinance adapter failure handling, and drift tests.
- Create `tests/test_recorder_api.py`: FastAPI recorder route contract tests.
- Create `tests/test_discord_recorder.py`: pure unit tests for channel/user filtering and message handling with fake messages.

---

### Task 1: Add Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Update dependency file**

Add these lines to `requirements.txt`:

```text
aiosqlite==0.21.0
discord.py==2.5.2
yfinance==0.2.65
pandas==2.3.0
```

- [ ] **Step 2: Install dependencies locally**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Expected: packages install successfully.

- [ ] **Step 3: Commit dependency change**

Run:

```powershell
git add requirements.txt
git commit -m "Add recorder dependencies"
```

Expected: commit succeeds.

---

### Task 2: Recorder Models

**Files:**
- Create: `sentinel_archive/recorder_models.py`
- Test: `tests/test_recording_store.py`

- [ ] **Step 1: Write model smoke test**

Create `tests/test_recording_store.py` with:

```python
from sentinel_archive.recorder_models import RecorderSettings, ParsedAlert, normalize_contract_key


def test_recorder_settings_masks_token():
    settings = RecorderSettings(discord_token="secret-token", discord_channel_ids=["123"])
    assert settings.masked().discord_token == "********"
    assert settings.masked().discord_channel_ids == ["123"]


def test_contract_key_normalization():
    assert normalize_contract_key("spy", "6/21/2026", 500, "call") == "SPY|2026-06-21|500|CALL"


def test_parsed_alert_accepts_unparsed_message():
    alert = ParsedAlert(message_id="m1", parse_status="unparsed", raw_text="watching SPY")
    assert alert.parse_status == "unparsed"
    assert alert.ticker is None
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recording_store.py -q
```

Expected: fail because `sentinel_archive.recorder_models` is missing.

- [ ] **Step 3: Implement recorder models**

Create `sentinel_archive/recorder_models.py`:

```python
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_option_type(value: str | None) -> str | None:
    if value is None:
        return None
    upper = str(value).strip().upper()
    if upper in {"C", "CALL", "CALLS"}:
        return "CALL"
    if upper in {"P", "PUT", "PUTS"}:
        return "PUT"
    raise ValueError("option_type must be CALL or PUT")


def normalize_expiration(value: str) -> str:
    raw = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", raw)
    if not match:
        raise ValueError("expiration must be YYYY-MM-DD or M/D[/YY|YYYY]")
    month = int(match.group(1))
    day = int(match.group(2))
    year_part = match.group(3)
    current_year = datetime.now(timezone.utc).year
    if year_part is None:
        year = current_year
    else:
        year = int(year_part)
        if year < 100:
            year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_contract_key(underlying: str, expiration: str, strike: float, option_type: str) -> str:
    clean_underlying = str(underlying).strip().upper()
    clean_expiration = normalize_expiration(expiration)
    clean_type = normalize_option_type(option_type)
    strike_value = float(strike)
    strike_text = str(int(strike_value)) if strike_value.is_integer() else str(strike_value).rstrip("0").rstrip(".")
    return f"{clean_underlying}|{clean_expiration}|{strike_text}|{clean_type}"


class RecorderSettings(BaseModel):
    discord_token: str = ""
    discord_channel_ids: list[str] = Field(default_factory=list)
    drift_amount_threshold: float = Field(default=0.05, ge=0.0)
    drift_percent_threshold: float = Field(default=10.0, ge=0.0)
    yfinance_enabled: bool = False
    record_all_channels: bool = False

    def masked(self) -> "RecorderSettings":
        data = self.model_dump()
        if data.get("discord_token"):
            data["discord_token"] = "********"
        return RecorderSettings(**data)


class DiscordSource(BaseModel):
    channel_id: str
    channel_name: str = ""
    guild_id: str = ""
    guild_name: str = ""
    enabled: bool = True
    allowed_author_ids: list[str] = Field(default_factory=list)
    ignored_author_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class DiscordMessageRecord(BaseModel):
    message_id: str
    channel_id: str
    channel_name: str = ""
    guild_id: str = ""
    guild_name: str = ""
    author_id: str = ""
    author_name: str = ""
    discord_timestamp: str
    engine_received_timestamp: str = Field(default_factory=utc_now_iso)
    content: str = ""
    embeds: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ParsedAlert(BaseModel):
    message_id: str
    parse_status: Literal["parsed", "unparsed", "error"]
    raw_text: str
    parse_error: str = ""
    action: str | None = None
    ticker: str | None = None
    expiration: str | None = None
    strike: float | None = None
    option_type: str | None = None
    alert_price: float | None = None
    sell_percentage: float | None = None
    confidence: str = "none"
    parser_profile: str = "sentinel_echo_default"
    normalized: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: Any) -> str | None:
        if value is None or value == "":
            return None
        return str(value).strip().upper()

    @field_validator("option_type", mode="before")
    @classmethod
    def validate_option_type(cls, value: Any) -> str | None:
        return normalize_option_type(value)


class MarketBarRecord(BaseModel):
    source: str = "csv"
    instrument_type: Literal["stock", "option"]
    timestamp: str
    symbol: str
    contract_key: str | None = None
    underlying: str | None = None
    expiration: str | None = None
    strike: float | None = None
    option_type: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    last: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MarketSnapshotRecord(BaseModel):
    alert_id: str
    snapshot_timestamp: str
    underlying: str | None = None
    stock_price: float | None = None
    option_contract_key: str | None = None
    option_bid: float | None = None
    option_ask: float | None = None
    option_mid: float | None = None
    option_last: float | None = None
    selected_market_price: float | None = None
    price_source: str = "unavailable"
    lookup_status: str = "market_price_unavailable"


class PriceDriftEvent(BaseModel):
    alert_id: str
    alert_price: float | None = None
    market_price: float | None = None
    price_drift_amount: float | None = None
    price_drift_pct: float | None = None
    drift_amount_threshold: float
    drift_percent_threshold: float
    drift_direction: str = "market_price_unavailable"
    price_drift_alert: bool = False


class RecorderStatus(BaseModel):
    discord_connected: bool = False
    discord_state: str = "stopped"
    monitored_channels: list[str] = Field(default_factory=list)
    messages_recorded: int = 0
    parsed_alerts: int = 0
    unparsed_alerts: int = 0
    drift_alerts: int = 0
    last_message_timestamp: str | None = None
```

- [ ] **Step 4: Run model tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recording_store.py -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit models**

Run:

```powershell
git add sentinel_archive/recorder_models.py tests/test_recording_store.py
git commit -m "Add recorder data models"
```

Expected: commit succeeds.

---

### Task 3: SQLite Recording Store

**Files:**
- Create: `sentinel_archive/recording_store.py`
- Modify: `tests/test_recording_store.py`

- [ ] **Step 1: Add SQLite persistence tests**

Append to `tests/test_recording_store.py`:

```python
import asyncio

from sentinel_archive.recording_store import RecordingStore
from sentinel_archive.recorder_models import DiscordMessageRecord, MarketBarRecord, ParsedAlert, RecorderSettings


def test_store_persists_settings_and_masks_token(tmp_path):
    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.save_settings(RecorderSettings(discord_token="secret", discord_channel_ids=["123"]))
        saved = await store.get_settings(mask_token=False)
        masked = await store.get_settings(mask_token=True)
        assert saved.discord_token == "secret"
        assert masked.discord_token == "********"
    asyncio.run(run())


def test_store_persists_message_alert_and_bar(tmp_path):
    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.insert_message(DiscordMessageRecord(
            message_id="m1",
            channel_id="123",
            channel_name="alerts",
            author_id="a1",
            author_name="Analyst",
            discord_timestamp="2026-06-19T14:30:00+00:00",
            content="BTO SPY 500C 6/21 @ 1.25",
        ))
        await store.insert_parsed_alert(ParsedAlert(
            message_id="m1",
            parse_status="parsed",
            raw_text="BTO SPY 500C 6/21 @ 1.25",
            action="buy",
            ticker="SPY",
            expiration="2026-06-21",
            strike=500,
            option_type="CALL",
            alert_price=1.25,
        ))
        await store.insert_market_bars([MarketBarRecord(
            instrument_type="option",
            timestamp="2026-06-19T14:30:00+00:00",
            symbol="SPY",
            contract_key="SPY|2026-06-21|500|CALL",
            close=1.30,
        )])
        assert len(await store.list_messages(limit=10)) == 1
        assert len(await store.list_alerts(limit=10)) == 1
        assert len(await store.list_market_bars(limit=10)) == 1
    asyncio.run(run())
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recording_store.py -q
```

Expected: fail because `recording_store.py` is missing.

- [ ] **Step 3: Implement SQLite store**

Create `sentinel_archive/recording_store.py` with schema creation and CRUD:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import aiosqlite

from .recorder_models import DiscordMessageRecord, MarketBarRecord, ParsedAlert, RecorderSettings


class RecordingStore:
    def __init__(self, db_path: str | Path = "data/sentinel_archive.sqlite3"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS recorder_settings (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discord_messages (
                    message_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    channel_name TEXT,
                    discord_timestamp TEXT NOT NULL,
                    engine_received_timestamp TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS parsed_alerts (
                    message_id TEXT PRIMARY KEY,
                    parse_status TEXT NOT NULL,
                    ticker TEXT,
                    contract_key TEXT,
                    alert_price REAL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS market_bars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    contract_key TEXT,
                    timestamp TEXT NOT NULL,
                    close REAL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_market_bars_contract_time
                    ON market_bars(contract_key, timestamp);
                CREATE INDEX IF NOT EXISTS idx_market_bars_symbol_time
                    ON market_bars(symbol, timestamp);
                """
            )
            await conn.commit()

    async def get_settings(self, *, mask_token: bool = True) -> RecorderSettings:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT data FROM recorder_settings WHERE id = ?", ("main",)) as cur:
                row = await cur.fetchone()
        settings = RecorderSettings(**json.loads(row["data"])) if row else RecorderSettings()
        return settings.masked() if mask_token else settings

    async def save_settings(self, settings: RecorderSettings) -> RecorderSettings:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO recorder_settings (id, data) VALUES (?, ?)",
                ("main", settings.model_dump_json()),
            )
            await conn.commit()
        return settings.masked()

    async def insert_message(self, message: DiscordMessageRecord) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO discord_messages
                (message_id, channel_id, channel_name, discord_timestamp, engine_received_timestamp, data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    message.channel_id,
                    message.channel_name,
                    message.discord_timestamp,
                    message.engine_received_timestamp,
                    message.model_dump_json(),
                ),
            )
            await conn.commit()

    async def insert_parsed_alert(self, alert: ParsedAlert) -> None:
        contract_key = alert.normalized.get("contract_key") if alert.normalized else None
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO parsed_alerts
                (message_id, parse_status, ticker, contract_key, alert_price, data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (alert.message_id, alert.parse_status, alert.ticker, contract_key, alert.alert_price, alert.model_dump_json()),
            )
            await conn.commit()

    async def insert_market_bars(self, bars: Iterable[MarketBarRecord]) -> int:
        rows = [
            (bar.instrument_type, bar.symbol, bar.contract_key, bar.timestamp, bar.close, bar.model_dump_json())
            for bar in bars
        ]
        if not rows:
            return 0
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executemany(
                """
                INSERT INTO market_bars (instrument_type, symbol, contract_key, timestamp, close, data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await conn.commit()
        return len(rows)

    async def list_messages(self, limit: int = 100) -> list[dict]:
        return await self._list_json("discord_messages", "engine_received_timestamp", limit)

    async def list_alerts(self, limit: int = 100) -> list[dict]:
        return await self._list_json("parsed_alerts", "message_id", limit)

    async def list_market_bars(self, limit: int = 100) -> list[dict]:
        return await self._list_json("market_bars", "timestamp", limit)

    async def _list_json(self, table: str, order_col: str, limit: int) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT data FROM {table} ORDER BY {order_col} DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [json.loads(row["data"]) for row in rows]
```

- [ ] **Step 4: Run store tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recording_store.py -q
```

Expected: all tests in `test_recording_store.py` pass.

- [ ] **Step 5: Commit store**

Run:

```powershell
git add sentinel_archive/recording_store.py tests/test_recording_store.py
git commit -m "Add SQLite recorder store"
```

Expected: commit succeeds.

---

### Task 4: Alert Parser

**Files:**
- Create: `sentinel_archive/alert_parser.py`
- Test: `tests/test_alert_parser.py`

- [ ] **Step 1: Write parser tests**

Create `tests/test_alert_parser.py`:

```python
import types

from sentinel_archive.alert_parser import build_discord_alert_text, parse_alert_text


def test_common_bto_alert_parses():
    parsed = parse_alert_text("BTO SPY 500C 6/21 @ 1.25", message_id="m1")
    assert parsed.parse_status == "parsed"
    assert parsed.action == "buy"
    assert parsed.ticker == "SPY"
    assert parsed.strike == 500
    assert parsed.option_type == "CALL"
    assert parsed.alert_price == 1.25
    assert parsed.normalized["contract_key"] == "SPY|2026-06-21|500|CALL"


def test_sell_alert_parses_percentage():
    parsed = parse_alert_text("SELL 50% SPY 500C 6/21 @ 1.40", message_id="m2")
    assert parsed.action == "sell"
    assert parsed.sell_percentage == 50


def test_unparsed_alert_is_retained():
    parsed = parse_alert_text("watching SPY for later", message_id="m3")
    assert parsed.parse_status == "unparsed"
    assert parsed.raw_text == "watching SPY for later"


def test_embed_text_is_included():
    embed = types.SimpleNamespace(
        title="Trade Alert",
        description="BTO SPY 500C 6/21 @ 1.25",
        fields=[types.SimpleNamespace(name="Notes", value="starter")],
        author=types.SimpleNamespace(name="Analyst"),
        footer=types.SimpleNamespace(text="risk-managed"),
    )
    message = types.SimpleNamespace(content="", embeds=[embed])
    text = build_discord_alert_text(message)
    assert "Trade Alert" in text
    assert parse_alert_text(text, message_id="m4").ticker == "SPY"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_alert_parser.py -q
```

Expected: fail because `alert_parser.py` is missing.

- [ ] **Step 3: Implement parser**

Create `sentinel_archive/alert_parser.py`:

```python
from __future__ import annotations

import re
from typing import Any

from .recorder_models import ParsedAlert, normalize_contract_key, normalize_expiration

BUY_KEYWORDS = ("BTO", "BUY TO OPEN", "BUYING", "BOUGHT", "BUY", "ENTRY", "ENTERING", "LONG", "OPENING")
SELL_KEYWORDS = ("STC", "SELL TO CLOSE", "SELLING", "SOLD", "SELL", "TRIM", "CLOSE", "EXIT", "OUT")
AVG_DOWN_KEYWORDS = ("AVERAGE DOWN", "AVG DOWN", "AVERAGING", "ADD TO", "ADDING")
OPTION_RE = re.compile(
    r"(?:^|\s)\$?(?P<strike>\d+(?:\.\d+)?)(?P<kind>[CP])\b|"
    r"(?:^|\s)\$?(?P<strike_word>\d+(?:\.\d+)?)\s*(?P<kind_word>CALLS?|PUTS?)\b",
    re.IGNORECASE,
)
EXPIRATION_RE = re.compile(r"\b(?P<expiration>\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})\b")
PRICE_PATTERNS = (
    re.compile(r"@\s*\$?(?P<price>\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\b(?:ENTRY|PRICE|AT|FILL)\s*:?\s*\$?(?P<price>\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\$\.(?P<cents>\d{1,2})\b", re.IGNORECASE),
)
ACTION_TICKER_RE = re.compile(
    r"\b(?:BTO|STC|BUY|BOUGHT|SELL|SOLD|TRIM|CLOSE|EXIT|LONG|ENTRY)\s+\$?(?P<ticker>[A-Z]{1,6})\b",
    re.IGNORECASE,
)
CASH_TICKER_RE = re.compile(r"\$(?P<ticker>[A-Z]{1,6})\b")


def build_discord_alert_text(message: Any) -> str:
    parts: list[str] = []
    _append(parts, _get(message, "content"))
    for embed in _get(message, "embeds", []) or []:
        _append(parts, _get(_get(embed, "author"), "name"))
        _append(parts, _get(embed, "title"))
        _append(parts, _get(embed, "description"))
        for field in _get(embed, "fields", []) or []:
            _append(parts, _get(field, "name"))
            _append(parts, _get(field, "value"))
        _append(parts, _get(_get(embed, "footer"), "text"))
    return "\n".join(parts)


def parse_alert_text(raw_text: str, *, message_id: str) -> ParsedAlert:
    text = " ".join(str(raw_text or "").strip().split())
    try:
        result = _parse(text)
        if not result:
            return ParsedAlert(message_id=message_id, parse_status="unparsed", raw_text=raw_text)
        normalized = dict(result)
        if result.get("ticker") and result.get("expiration") and result.get("strike") and result.get("option_type"):
            normalized["expiration"] = normalize_expiration(result["expiration"])
            normalized["contract_key"] = normalize_contract_key(
                result["ticker"], normalized["expiration"], result["strike"], result["option_type"]
            )
        return ParsedAlert(
            message_id=message_id,
            parse_status="parsed",
            raw_text=raw_text,
            action=result.get("alert_type"),
            ticker=result.get("ticker"),
            expiration=normalized.get("expiration"),
            strike=result.get("strike"),
            option_type=result.get("option_type"),
            alert_price=result.get("entry_price"),
            sell_percentage=result.get("sell_percentage"),
            confidence="high",
            normalized=normalized,
        )
    except Exception as exc:
        return ParsedAlert(message_id=message_id, parse_status="error", raw_text=raw_text, parse_error=str(exc))


def _parse(message: str) -> dict | None:
    if _contains_keyword(message, AVG_DOWN_KEYWORDS):
        return _parse_contract_alert(message, "average_down", require_price=False)
    if _contains_keyword(message, SELL_KEYWORDS):
        return _parse_sell_alert(message)
    if _contains_keyword(message, BUY_KEYWORDS):
        return _parse_contract_alert(message, "buy", require_price=True)
    return _parse_contract_alert(message, "buy", require_price=True)


def _parse_sell_alert(message: str) -> dict | None:
    result = _parse_contract_alert(message, "sell", require_price=False, require_contract=False)
    if not result:
        return None
    if _contains_keyword(message, ("TRIM", "TRIMMING")):
        result["alert_type"] = "trim"
    elif _contains_keyword(message, ("CLOSE", "CLOSING", "EXIT", "EXITING")):
        result["alert_type"] = "close"
    result["sell_percentage"] = _extract_sell_percentage(message)
    return result


def _parse_contract_alert(message: str, alert_type: str, *, require_price: bool, require_contract: bool = True) -> dict | None:
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
    return float(strike), "CALL" if kind.upper().startswith("C") else "PUT"


def _extract_expiration(message: str) -> str | None:
    match = EXPIRATION_RE.search(message)
    return match.group("expiration") if match else None


def _extract_price(message: str) -> float | None:
    for pattern in PRICE_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        if match.groupdict().get("cents") is not None:
            return float(f"0.{match.group('cents')}")
        return float(match.group("price"))
    return None


def _extract_sell_percentage(message: str) -> float:
    if _contains_keyword(message, ("ALL", "CLOSE", "CLOSING", "EXIT", "EXITING")):
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


def _keyword_regex(keyword: str) -> re.Pattern:
    parts = [re.escape(part) for part in str(keyword).strip().split()]
    return re.compile(rf"(?<![A-Z0-9]){r'\s+'.join(parts)}(?![A-Z0-9])", re.IGNORECASE)


def _append(parts: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        parts.append(text)


def _get(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)
```

- [ ] **Step 4: Run parser tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_alert_parser.py -q
```

Expected: 4 passed.

- [ ] **Step 5: Commit parser**

Run:

```powershell
git add sentinel_archive/alert_parser.py tests/test_alert_parser.py
git commit -m "Add options alert parser"
```

Expected: commit succeeds.

---

### Task 5: Market Recorder

**Files:**
- Create: `sentinel_archive/market_recorder.py`
- Modify: `sentinel_archive/recording_store.py`
- Test: `tests/test_market_recorder.py`

- [ ] **Step 1: Write market recorder tests**

Create `tests/test_market_recorder.py`:

```python
import asyncio

from sentinel_archive.market_recorder import calculate_price_drift, parse_option_csv
from sentinel_archive.recorder_models import ParsedAlert, RecorderSettings
from sentinel_archive.recording_store import RecordingStore


def test_parse_option_csv_normalizes_contract_key():
    csv_text = "timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume\n2026-06-19T14:30:00Z,SPY,6/21/2026,500,CALL,1.2,1.4,1.1,1.3,100\n"
    bars = parse_option_csv(csv_text)
    assert bars[0].contract_key == "SPY|2026-06-21|500|CALL"
    assert bars[0].close == 1.3


def test_price_drift_triggers_on_amount_or_percent():
    settings = RecorderSettings(drift_amount_threshold=0.05, drift_percent_threshold=10)
    event = calculate_price_drift("a1", 1.00, 1.06, settings)
    assert event.price_drift_alert is True
    assert event.drift_direction == "market_above_alert"
    event = calculate_price_drift("a2", 2.00, 2.15, settings)
    assert event.price_drift_alert is True


def test_latest_market_bar_lookup(tmp_path):
    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.insert_market_bars(parse_option_csv(
            "timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume\n"
            "2026-06-19T14:29:00Z,SPY,6/21/2026,500,CALL,1.0,1.1,0.9,1.05,10\n"
            "2026-06-19T14:31:00Z,SPY,6/21/2026,500,CALL,1.2,1.3,1.1,1.25,10\n"
        ))
        bar = await store.latest_market_bar("SPY|2026-06-21|500|CALL", "2026-06-19T14:30:00Z")
        assert bar["close"] == 1.05
    asyncio.run(run())
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_market_recorder.py -q
```

Expected: fail because `market_recorder.py` and `latest_market_bar` are missing.

- [ ] **Step 3: Implement CSV parsing and drift**

Create `sentinel_archive/market_recorder.py`:

```python
from __future__ import annotations

import csv
from io import StringIO

from .recorder_models import MarketBarRecord, PriceDriftEvent, RecorderSettings, normalize_contract_key


def parse_stock_csv(csv_text: str) -> list[MarketBarRecord]:
    reader = csv.DictReader(StringIO(csv_text.strip()))
    _require(reader.fieldnames, {"timestamp", "symbol", "open", "high", "low", "close", "volume"})
    return [
        MarketBarRecord(
            instrument_type="stock",
            timestamp=row["timestamp"].strip(),
            symbol=row["symbol"].strip().upper(),
            open=_float(row.get("open")),
            high=_float(row.get("high")),
            low=_float(row.get("low")),
            close=_float(row.get("close")),
            volume=_float(row.get("volume")),
            source=row.get("source") or "csv",
        )
        for row in reader
    ]


def parse_option_csv(csv_text: str) -> list[MarketBarRecord]:
    reader = csv.DictReader(StringIO(csv_text.strip()))
    _require(reader.fieldnames, {"timestamp", "underlying", "expiration", "strike", "option_type", "open", "high", "low", "close", "volume"})
    bars: list[MarketBarRecord] = []
    for row in reader:
        contract_key = normalize_contract_key(row["underlying"], row["expiration"], float(row["strike"]), row["option_type"])
        bars.append(MarketBarRecord(
            instrument_type="option",
            timestamp=row["timestamp"].strip(),
            symbol=row["underlying"].strip().upper(),
            underlying=row["underlying"].strip().upper(),
            expiration=row["expiration"].strip(),
            strike=float(row["strike"]),
            option_type=row["option_type"].strip().upper(),
            contract_key=contract_key,
            open=_float(row.get("open")),
            high=_float(row.get("high")),
            low=_float(row.get("low")),
            close=_float(row.get("close")),
            volume=_float(row.get("volume")),
            bid=_optional_float(row.get("bid")),
            ask=_optional_float(row.get("ask")),
            mid=_optional_float(row.get("mid")),
            last=_optional_float(row.get("last")),
            source=row.get("source") or "csv",
        ))
    return bars


def calculate_price_drift(alert_id: str, alert_price: float | None, market_price: float | None, settings: RecorderSettings) -> PriceDriftEvent:
    if alert_price is None or market_price is None or alert_price <= 0:
        return PriceDriftEvent(
            alert_id=alert_id,
            alert_price=alert_price,
            market_price=market_price,
            drift_amount_threshold=settings.drift_amount_threshold,
            drift_percent_threshold=settings.drift_percent_threshold,
        )
    amount = round(market_price - alert_price, 6)
    pct = round((amount / alert_price) * 100, 6)
    direction = "market_above_alert" if amount > 0 else "market_below_alert" if amount < 0 else "no_drift"
    triggered = abs(amount) >= settings.drift_amount_threshold or abs(pct) >= settings.drift_percent_threshold
    return PriceDriftEvent(
        alert_id=alert_id,
        alert_price=alert_price,
        market_price=market_price,
        price_drift_amount=amount,
        price_drift_pct=pct,
        drift_amount_threshold=settings.drift_amount_threshold,
        drift_percent_threshold=settings.drift_percent_threshold,
        drift_direction=direction,
        price_drift_alert=triggered,
    )


def _require(columns: list[str] | None, required: set[str]) -> None:
    present = set(columns or [])
    missing = required - present
    if missing:
        raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")


def _float(value: object) -> float:
    return float(value or 0)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
```

- [ ] **Step 4: Add latest market lookup to store**

Add this method to `RecordingStore` in `sentinel_archive/recording_store.py`:

```python
    async def latest_market_bar(self, contract_key: str, timestamp: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT data FROM market_bars
                WHERE contract_key = ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (contract_key, timestamp),
            ) as cur:
                row = await cur.fetchone()
        return json.loads(row["data"]) if row else None
```

- [ ] **Step 5: Run market recorder tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_market_recorder.py -q
```

Expected: 3 passed.

- [ ] **Step 6: Commit market recorder**

Run:

```powershell
git add sentinel_archive/market_recorder.py sentinel_archive/recording_store.py tests/test_market_recorder.py
git commit -m "Add market recorder CSV and drift logic"
```

Expected: commit succeeds.

---

### Task 6: Discord Recorder Service

**Files:**
- Create: `sentinel_archive/discord_recorder.py`
- Test: `tests/test_discord_recorder.py`

- [ ] **Step 1: Write Discord service unit tests**

Create `tests/test_discord_recorder.py`:

```python
import asyncio
import types

from sentinel_archive.discord_recorder import DiscordRecorder
from sentinel_archive.recording_store import RecordingStore
from sentinel_archive.recorder_models import RecorderSettings


def fake_message(content, channel_id="123", author_id="a1"):
    return types.SimpleNamespace(
        id="m1",
        content=content,
        embeds=[],
        attachments=[],
        created_at=types.SimpleNamespace(isoformat=lambda: "2026-06-19T14:30:00+00:00"),
        author=types.SimpleNamespace(id=author_id, name="Analyst"),
        channel=types.SimpleNamespace(id=channel_id, name="alerts"),
        guild=types.SimpleNamespace(id="g1", name="Guild"),
    )


def test_handle_message_records_configured_channel(tmp_path):
    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.save_settings(RecorderSettings(discord_channel_ids=["123"]))
        recorder = DiscordRecorder(store)
        result = await recorder.handle_message(fake_message("BTO SPY 500C 6/21 @ 1.25"), bot_user_id="bot")
        assert result == "recorded"
        assert len(await store.list_messages()) == 1
        assert (await store.list_alerts())[0]["parse_status"] == "parsed"
    asyncio.run(run())


def test_handle_message_skips_unconfigured_channel(tmp_path):
    async def run():
        store = RecordingStore(tmp_path / "recorder.sqlite3")
        await store.initialize()
        await store.save_settings(RecorderSettings(discord_channel_ids=["999"]))
        recorder = DiscordRecorder(store)
        result = await recorder.handle_message(fake_message("BTO SPY 500C 6/21 @ 1.25"), bot_user_id="bot")
        assert result == "channel_not_monitored"
        assert await store.list_messages() == []
    asyncio.run(run())
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discord_recorder.py -q
```

Expected: fail because `discord_recorder.py` is missing.

- [ ] **Step 3: Implement recorder service**

Create `sentinel_archive/discord_recorder.py`:

```python
from __future__ import annotations

import asyncio
import threading
from typing import Any

from .alert_parser import build_discord_alert_text, parse_alert_text
from .recorder_models import DiscordMessageRecord, RecorderStatus
from .recording_store import RecordingStore


class DiscordRecorder:
    def __init__(self, store: RecordingStore):
        self.store = store
        self.bot = None
        self.thread: threading.Thread | None = None
        self.state = "stopped"
        self.last_error = ""

    async def handle_message(self, message: Any, *, bot_user_id: str | None = None) -> str:
        author = getattr(message, "author", None)
        if bot_user_id and str(getattr(author, "id", "")) == str(bot_user_id):
            return "self_message"
        settings = await self.store.get_settings(mask_token=False)
        channel = getattr(message, "channel", None)
        channel_id = str(getattr(channel, "id", ""))
        if not settings.record_all_channels and settings.discord_channel_ids and channel_id not in settings.discord_channel_ids:
            return "channel_not_monitored"
        guild = getattr(message, "guild", None)
        raw_text = build_discord_alert_text(message)
        record = DiscordMessageRecord(
            message_id=str(getattr(message, "id", "")),
            channel_id=channel_id,
            channel_name=str(getattr(channel, "name", "")),
            guild_id=str(getattr(guild, "id", "")),
            guild_name=str(getattr(guild, "name", "")),
            author_id=str(getattr(author, "id", "")),
            author_name=str(getattr(author, "name", "")),
            discord_timestamp=_iso(getattr(message, "created_at", None)),
            content=str(getattr(message, "content", "")),
            embeds=[_to_dict(embed) for embed in getattr(message, "embeds", []) or []],
            attachments=[_to_dict(item) for item in getattr(message, "attachments", []) or []],
            raw_payload={},
        )
        await self.store.insert_message(record)
        await self.store.insert_parsed_alert(parse_alert_text(raw_text, message_id=record.message_id))
        return "recorded"

    def status(self) -> RecorderStatus:
        return RecorderStatus(discord_connected=self.state == "connected", discord_state=self.state)

    async def stop(self) -> None:
        if self.bot is not None:
            await self.bot.close()
        self.bot = None
        self.state = "stopped"


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _to_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return {"text": str(value)}
```

- [ ] **Step 4: Run Discord recorder tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discord_recorder.py -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit Discord service**

Run:

```powershell
git add sentinel_archive/discord_recorder.py tests/test_discord_recorder.py
git commit -m "Add Discord recorder service"
```

Expected: commit succeeds.

---

### Task 7: Recorder API

**Files:**
- Create: `sentinel_archive/recorder_api.py`
- Modify: `sentinel_archive/api.py`
- Test: `tests/test_recorder_api.py`

- [ ] **Step 1: Write route contract tests**

Create `tests/test_recorder_api.py`:

```python
from fastapi.testclient import TestClient

from sentinel_archive.api import create_app


def test_recorder_settings_masks_token(tmp_path):
    app = create_app(recorder_db_path=tmp_path / "recorder.sqlite3")
    with TestClient(app) as client:
        response = client.put("/api/recorder/discord/settings", json={
            "discord_token": "secret",
            "discord_channel_ids": ["123"],
            "drift_amount_threshold": 0.05,
            "drift_percent_threshold": 10,
        })
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recorder_api.py -q
```

Expected: fail because recorder routes are not mounted.

- [ ] **Step 3: Implement recorder routes**

Create `sentinel_archive/recorder_api.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .alert_parser import parse_alert_text
from .discord_recorder import DiscordRecorder
from .market_recorder import parse_option_csv, parse_stock_csv
from .recorder_models import RecorderSettings
from .recording_store import RecordingStore


class ParsePreviewRequest(BaseModel):
    raw_text: str = Field(min_length=1)


class CsvImportRequest(BaseModel):
    csv_text: str = Field(min_length=1)


def create_recorder_router(store: RecordingStore, recorder: DiscordRecorder) -> APIRouter:
    router = APIRouter(tags=["Recorder"])

    @router.get("/recorder/discord/settings")
    async def get_discord_settings():
        return await store.get_settings(mask_token=True)

    @router.put("/recorder/discord/settings")
    async def put_discord_settings(settings: RecorderSettings):
        existing = await store.get_settings(mask_token=False)
        if settings.discord_token == "********":
            settings.discord_token = existing.discord_token
        return await store.save_settings(settings)

    @router.post("/recorder/discord/parse-preview")
    async def parse_preview(body: ParsePreviewRequest):
        return parse_alert_text(body.raw_text, message_id="preview")

    @router.get("/recorder/discord/status")
    async def discord_status():
        return recorder.status()

    @router.post("/recorder/discord/stop")
    async def stop_discord():
        await recorder.stop()
        return recorder.status()

    @router.post("/recorder/market/import/options-csv")
    async def import_options_csv(body: CsvImportRequest):
        try:
            bars = parse_option_csv(body.csv_text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"inserted": await store.insert_market_bars(bars)}

    @router.post("/recorder/market/import/stocks-csv")
    async def import_stocks_csv(body: CsvImportRequest):
        try:
            bars = parse_stock_csv(body.csv_text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"inserted": await store.insert_market_bars(bars)}

    @router.get("/recordings/messages")
    async def list_messages(limit: int = 100):
        return {"messages": await store.list_messages(limit)}

    @router.get("/recordings/alerts")
    async def list_alerts(limit: int = 100):
        return {"alerts": await store.list_alerts(limit)}

    return router
```

- [ ] **Step 4: Mount routes and initialize store**

Modify `sentinel_archive/api.py`:

```python
from .discord_recorder import DiscordRecorder
from .recorder_api import create_recorder_router
from .recording_store import RecordingStore
```

Change the app factory signature:

```python
def create_app(engine: SentinelArchive | None = None, recorder_db_path: str | Path = "data/sentinel_archive.sqlite3") -> FastAPI:
```

After `engine_instance = ...`, add:

```python
    recorder_store = RecordingStore(recorder_db_path)
    discord_recorder = DiscordRecorder(recorder_store)
```

Inside lifespan before `yield`, add:

```python
        await recorder_store.initialize()
```

After middleware setup, mount:

```python
    app.include_router(create_recorder_router(recorder_store, discord_recorder), prefix="/api")
```

- [ ] **Step 5: Run API tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_recorder_api.py -q
```

Expected: 3 passed.

- [ ] **Step 6: Commit API**

Run:

```powershell
git add sentinel_archive/api.py sentinel_archive/recorder_api.py tests/test_recorder_api.py
git commit -m "Add recorder API routes"
```

Expected: commit succeeds.

---

### Task 8: Frontend Recorder Controls

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Extend frontend API client**

Add recorder types and methods in `frontend/src/api.ts`:

```ts
export type RecorderSettings = {
  discord_token: string;
  discord_channel_ids: string[];
  drift_amount_threshold: number;
  drift_percent_threshold: number;
  yfinance_enabled: boolean;
  record_all_channels: boolean;
};

export type ParsedAlert = {
  message_id: string;
  parse_status: 'parsed' | 'unparsed' | 'error';
  action?: string | null;
  ticker?: string | null;
  expiration?: string | null;
  strike?: number | null;
  option_type?: string | null;
  alert_price?: number | null;
  raw_text: string;
};
```

Add methods to `api`:

```ts
  recorderSettings: () => requestJson<RecorderSettings>('/api/recorder/discord/settings'),
  updateRecorderSettings: (settings: RecorderSettings) =>
    requestJson<RecorderSettings>('/api/recorder/discord/settings', {
      method: 'PUT',
      body: JSON.stringify(settings),
    }),
  parsePreview: (rawText: string) =>
    requestJson<ParsedAlert>('/api/recorder/discord/parse-preview', {
      method: 'POST',
      body: JSON.stringify({ raw_text: rawText }),
    }),
  importOptionsCsv: (csvText: string) =>
    requestJson<{ inserted: number }>('/api/recorder/market/import/options-csv', {
      method: 'POST',
      body: JSON.stringify({ csv_text: csvText }),
    }),
  recorderAlerts: () => requestJson<{ alerts: ParsedAlert[] }>('/api/recordings/alerts'),
```

- [ ] **Step 2: Add recorder panel state to `App.tsx`**

Add state:

```tsx
  const [recorderSettings, setRecorderSettings] = React.useState<RecorderSettings | null>(null);
  const [previewText, setPreviewText] = React.useState('BTO SPY 500C 6/21 @ 1.25');
  const [previewAlert, setPreviewAlert] = React.useState<ParsedAlert | null>(null);
  const [optionsCsvText, setOptionsCsvText] = React.useState('timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume\n');
  const [recorderAlerts, setRecorderAlerts] = React.useState<ParsedAlert[]>([]);
```

Extend `refresh`:

```tsx
    const [next, settings, alerts] = await Promise.all([
      api.state(),
      api.recorderSettings(),
      api.recorderAlerts(),
    ]);
    setSnapshot(next);
    setRecorderSettings(settings);
    setRecorderAlerts(alerts.alerts);
```

- [ ] **Step 3: Add recorder UI sections**

Add a panel titled `Discord Recorder` with token input, channel ID textarea, drift thresholds, save button, and parse preview button. Use labels `Record`, `Capture`, and `Preview`; do not use labels implying trade execution.

Use this JSX block:

```tsx
        <section className="panel wide-panel">
          <PanelHeader icon={<RadioTower size={16} />} title="Discord Recorder" />
          {recorderSettings ? (
            <div className="form-grid compact">
              <label className="field">
                <span>Bot token</span>
                <input
                  type="password"
                  value={recorderSettings.discord_token}
                  onChange={(event) => setRecorderSettings({ ...recorderSettings, discord_token: event.target.value })}
                />
              </label>
              <label className="field">
                <span>Channel IDs</span>
                <textarea
                  value={recorderSettings.discord_channel_ids.join('\n')}
                  onChange={(event) => setRecorderSettings({
                    ...recorderSettings,
                    discord_channel_ids: event.target.value.split(/\s|,/).map((item) => item.trim()).filter(Boolean),
                  })}
                />
              </label>
              <NumberField label="Drift $" value={recorderSettings.drift_amount_threshold} step={0.01} onChange={(value) => setRecorderSettings({ ...recorderSettings, drift_amount_threshold: value })} />
              <NumberField label="Drift %" value={recorderSettings.drift_percent_threshold} onChange={(value) => setRecorderSettings({ ...recorderSettings, drift_percent_threshold: value })} />
              <button className="primary wide" type="button" onClick={() => run('Saving recorder', () => api.updateRecorderSettings(recorderSettings))}>
                <Save size={15} />
                Save Recorder
              </button>
              <label className="field wide">
                <span>Parse preview</span>
                <textarea value={previewText} onChange={(event) => setPreviewText(event.target.value)} />
              </label>
              <button type="button" onClick={() => run('Previewing parser', async () => setPreviewAlert(await api.parsePreview(previewText)))}>
                Preview Alert
              </button>
              <pre className="json-preview">{previewAlert ? JSON.stringify(previewAlert, null, 2) : 'No preview yet'}</pre>
            </div>
          ) : null}
        </section>
```

Add an `Options Price CSV` panel and a `Recorded Alerts` table using `api.importOptionsCsv` and `recorderAlerts`.

- [ ] **Step 4: Add CSS**

In `frontend/src/styles.css`, add:

```css
.json-preview {
  min-height: 120px;
  overflow: auto;
  border: 1px solid var(--border);
  background: var(--muted);
  padding: 10px;
  border-radius: 6px;
  font-size: 12px;
}

.field.wide {
  grid-column: 1 / -1;
}

.drift-flag {
  color: #b42318;
  font-weight: 700;
}
```

- [ ] **Step 5: Build frontend**

Run:

```powershell
npm run build
```

Expected: build succeeds.

- [ ] **Step 6: Commit UI**

Run:

```powershell
git add frontend/src/api.ts frontend/src/App.tsx frontend/src/styles.css
git commit -m "Add recorder controls to UI"
```

Expected: commit succeeds.

---

### Task 9: Full Verification And Docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README section**

Add a `Discord Options Recorder` section to `README.md` explaining:

```markdown
## Discord Options Recorder

The recorder captures Discord option alerts and market observations for later bot testing. It does not execute trades, simulate positions, or connect to brokers.

Recorded data is stored in `data/sentinel_archive.sqlite3`. Exports are written under `data/recordings/` with timestamped channel-aware paths.

Required Discord setup:

- Create a Discord bot in the Discord Developer Portal.
- Enable Message Content Intent.
- Invite the bot to the server with View Channels and Read Message History permissions.
- Add one or more channel IDs in the Recorder panel.

CSV option price imports require:

```csv
timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume
```
```

- [ ] **Step 2: Run backend tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass.

- [ ] **Step 3: Run frontend build**

Run:

```powershell
npm run build
```

Expected: build succeeds.

- [ ] **Step 4: Run launcher smoke test**

Run:

```powershell
.\Launch-Sentinel-Archive.ps1 -SmokeTest
```

Expected: smoke test passes without starting a long-running server.

- [ ] **Step 5: Commit docs**

Run:

```powershell
git add README.md
git commit -m "Document Discord options recorder"
```

Expected: commit succeeds.

---

## Self-Review Checklist

- Spec coverage: storage, Discord settings, parser preview, CSV imports, market snapshots, drift thresholds, SQLite persistence, timestamped channel-aware exports, and recorder-only safety boundary are represented in tasks.
- Placeholder scan: the plan contains no unfinished-work markers, no unspecified error-handling steps, and no references to undefined task outputs.
- Type consistency: `RecorderSettings`, `ParsedAlert`, `MarketBarRecord`, `RecordingStore`, and route names are consistent across backend tests, implementation snippets, and frontend API calls.
- Safety boundary: no task instructs the recorder to place, fill, paper-trade, or call handoff execution from Discord alerts.
