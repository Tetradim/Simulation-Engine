# Sentinel Archive

Local recorded-market simulation, Edge/Pulse contract testing, Discord options alert recording, and Sentinel Echo replay data generation.

This project is intentionally local-first. It can stand in for Sentinel Edge, Sentinel Pulse, or both at the same time, while also recording Discord options alerts and market observations for later bot testing.

## Safety Boundary

The Sentinel Archive does not connect to brokers and does not place live orders.

The Discord recorder inside this project is also recorder-only. It listens to Discord, parses alerts, stores market context, calculates drift, exports data, and publishes replay events. It does not execute paper trades, simulate option positions, or replace the Sentinel Echo trading bot.

Use this project to answer:

- What did a Discord alert say?
- When did it arrive?
- Which channel and author did it come from?
- What did the parser extract?
- What market price did the engine see at that time?
- Was there meaningful alert-price drift?
- What replay event stream should another bot consume for testing?

## Repository

```text
C:\Users\Lite OS\Documents\Codex\2026-06-12\c-users-lite-os-openclaw-workspace\work\Sentinel-Archive
```

## Current Capability Map

| Area | Implemented capability |
| --- | --- |
| Market replay | Imports OHLCV CSV rows, sorts by timestamp, groups same-timestamp bars, and advances deterministically. |
| Market state | Maintains current replay prices per symbol and exposes quote/price endpoints. |
| Simulated account | Tracks cash, equity, buying power, open positions, average entry, current price, P&L, trailing state, and day P&L. |
| Execution assumptions | Configurable starting cash, quantity, allocation cap, fill ratio, slippage, commission, confidence threshold, regular stop, trailing stop, and take-profit rules. |
| Handoff contract | Accepts `edge.pulse.handoff.v1` through native and Pulse-compatible endpoints. |
| Action coverage | Handles buy, sell, DCA, regular stop, trailing stop, tighten trailing stop, opening trailing stop, stop all, emergency exit, and stop buying. |
| Risk exits | Applies replay-bar high/low checks for regular stop, take profit, and trailing stop exits. |
| Idempotency | Reuses prior handoff results when the same `idempotency_key` arrives again. |
| Edge facade | Serves Edge-style liveness, automation, decisions, Pulse account, Pulse positions, and handoff schema endpoints. |
| Pulse facade | Serves Pulse-style health, Edge status, account, tickers, positions, handoff, legacy decision, legacy trailing, and signal scoring endpoints. |
| Discord recorder | Stores Discord messages, embeds, attachments, parsed alerts, source metadata, sessions, market bars, snapshots, drift events, and exports in SQLite. |
| Discord diagnostics | Tests saved token or `DISCORD_BOT_TOKEN` against Discord REST and verifies configured channel access without returning secrets. |
| Alert parser | Parses buy/open, sell/trim/close, and average-down option alerts from plain text and embed text. |
| Market capture | Imports option and stock price CSVs, optionally enriches with yfinance, snapshots alert-time option and stock prices, and calculates drift. |
| Sentinel Echo replay | Publishes joined replay events for Sentinel Echo and writes JSONL test-run manifests. |
| Control panel | React/Vite dashboard for replay, simulated handoffs, recorder setup, imports, exports, Sentinel Echo replay, positions, and event tape. |
| Windows launcher | Starts FastAPI, optionally rebuilds UI, opens a dedicated browser profile, and stops the process when the browser closes. |

## Architecture

```text
CSV market data                  Discord alerts
      |                                |
      v                                v
SentinelArchive              DiscordRecorder
      |                                |
      |                         RecordingStore
      |                         SQLite database
      |                                |
      |                         snapshots, drift, exports
      |                                |
      +----------- FastAPI API --------+
                         |
                         v
              React control panel
                         |
          Edge/Pulse/Sentinel Echo clients
```

The in-memory simulation state and the persistent recorder state are separate:

- Replay sessions, simulated positions, decisions, and account state are in memory.
- Recorder settings, Discord messages, parsed alerts, market bars, snapshots, drift events, capture sessions, and exports are persisted in SQLite.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm install
npm run build
.\.venv\Scripts\python.exe -m uvicorn sentinel_archive.main:app --host 127.0.0.1 --port 9200
```

Open:

```text
http://127.0.0.1:9200
```

## Windows Beta Installer

For non-technical beta testers, download and run `SentinelArchive-Setup-<version>.exe` from the Windows release artifact.

After installation, double-click **Sentinel Archive** from the Desktop or Start Menu. The installed launcher downloads missing runtime dependencies on first launch, including the Microsoft Visual C++ Runtime when Windows does not already have it. The installed beta build runs the packaged FastAPI app and serves the bundled control panel from the same local port.

Installed beta testers do not need to install Python, Node.js, npm, or Vite. If startup fails, send a screenshot of the launcher window and the Desktop log file named `Sentinel-Archive.log`.

Default installed URLs:

| Service | URL |
| --- | --- |
| Control panel | `http://127.0.0.1:9200` |
| Health check | `http://127.0.0.1:9200/api/health` |

## Windows Launcher

```powershell
.\Launch-Sentinel-Archive.ps1
```

Useful flags:

| Flag | Purpose |
| --- | --- |
| `-Port 9200` | Choose the FastAPI and control-panel port. |
| `-NoBrowser` | Start without opening or monitoring a browser window. |
| `-InstallDeps` | Install Python and frontend dependencies before launch. |
| `-Rebuild` | Rebuild the React control panel before launch. |
| `-SmokeTest` | Verify launcher prerequisites without starting the server. |
| `-AllowDefaultBrowserFallback` | Permit a regular browser tab if Edge or Chrome app-window mode is unavailable. |

The launcher:

1. Verifies Python and npm.
2. Creates or reuses `.venv`.
3. Installs dependencies when requested or missing.
4. Builds the control panel when requested or when `dist/index.html` is missing.
5. Replaces an existing listener on the selected port.
6. Starts `uvicorn sentinel_archive.main:app`.
7. Waits for `/api/health`.
8. Opens a dedicated Edge or Chrome app window unless `-NoBrowser` is set.
9. Stops the server when the dedicated browser closes.

By default the launcher does not silently fall back to a regular browser tab. If Edge or Chrome cannot be found, it stops with an explicit browser error. Use `-AllowDefaultBrowserFallback` only when a normal tab is acceptable.

## macOS Beta Installer

MacBook beta testers can install the local source build with the bundled macOS installer script. It creates a Python virtual environment, installs npm dependencies, builds the React control panel, and adds a double-click launcher to the Desktop.

Prerequisites:

- macOS with Python 3.11+ on `PATH`
- Node.js 20+ with `npm`

From the repository root:

```bash
chmod +x install-macos.sh
./install-macos.sh
```

After installation, double-click `Sentinel Archive.command` on the Desktop. Logs are written to `~/Desktop/Sentinel-Archive.log`.

Manual launch options:

```bash
./install-macos.sh --launch
./install-macos.sh --launch --install-deps --rebuild
./install-macos.sh --launch --port 9200 --no-browser
```

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `DISCORD_BOT_TOKEN` | Optional fallback Discord token when no saved token exists. |
| `SIMULATION_EVENT_BUS_SECRET` | Required shared secret for `/api/bus/events`; clients must send it in `X-Simulation-Event-Bus-Secret`. The configured value must be non-placeholder and at least 32 characters. |
| `BOT_EVENT_BUS_DIR` | Optional local directory for JSONL cross-bot event records. |
| `PULSE_EDGE_API_KEY` | Not required by this app; Pulse-compatible routes use the built-in local key below. |

Pulse-compatible routes require:

```text
X-API-Key: local-sim-key
```

The UI sends this key automatically for handoff composer requests.

## Control Panel Guide

The FastAPI app serves the built React UI from `dist/`. It polls `/api/simulation/state` and recorder endpoints roughly every 1.5 seconds.

### Top Metrics

| Metric | Meaning |
| --- | --- |
| Equity | Cash plus simulated market value of open positions. |
| Replay Index | Number of timestamp batches consumed from the active replay session. |
| Open Positions | Number of simulated account positions and current day P&L. |
| Current Prices | Symbols that have a current replay price. |

### Execution Model

Controls the assumptions used by simulated handoffs:

| Field | Function |
| --- | --- |
| Starting cash | Initial account cash and equity. |
| Default quantity | Quantity used when a handoff omits `metadata.quantity`. |
| Max allocation % | Maximum account allocation for one buy. |
| Fill ratio | Partial-fill multiplier for buys. |
| Slippage bps | Basis-point fill adjustment for buys and sells. |
| Commission | Flat commission per order. |
| Reject below | Minimum accepted handoff confidence. |
| Trail % | Default trailing-stop percent. |
| Stop % | Automatic regular stop loss from average entry. |
| Target % | Automatic take-profit threshold from average entry. |

### Market Day Replay

Imports stock-style OHLCV bars.

Required CSV:

```csv
timestamp,symbol,open,high,low,close,volume
2026-06-19T14:29:00Z,SPY,540,541,539,540.5,1000
```

Behavior:

- Symbols are uppercased.
- Rows are sorted by timestamp and symbol.
- Same-timestamp rows advance as one replay step.
- `close` becomes the current price.
- `high` and `low` drive stop-loss, take-profit, and trailing-stop rules.

### Paper Burn-In Automation

Run the broker-safe burn-in evidence harness:

```bash
python -m sentinel_archive.paper_burnin --output-dir outputs
```

With operator-supplied Alpaca paper credentials, run the explicit broker-paper
drill:

```bash
python -m sentinel_archive.paper_burnin --run-broker-paper --allow-paper-orders --output-dir outputs
```

With a local Alpaca MCP server already running in paper mode, run the same
order/cancel/rejection evidence through MCP:

```bash
python -m sentinel_archive.paper_burnin --run-alpaca-mcp --allow-paper-orders --alpaca-mcp-url http://127.0.0.1:8765/mcp --output-dir outputs
```

The harness runs simulator-backed checks for bot lifecycle, paper buy fills,
partial fills, duplicate idempotency, low-confidence rejection, stop-buying,
trailing-stop exit, restart/reload continuation, and live-mode rejection. Broker
paper-account drills fail closed until operator-provided paper credentials,
Pulse/Edge targets, and signoff are present. It does not place broker orders by
default; the broker-paper and MCP flags submit only a tiny non-marketable paper
limit order, cancel it, submit a controlled invalid order, and record
reconciliation evidence without storing credentials.

### Discord Recorder

Controls Discord capture and parser testing.

| Control | Function |
| --- | --- |
| Bot token | Saved Discord token. API responses mask it as `********`. |
| Channel IDs | One or more channel IDs to monitor; paste one per line, comma-separated, or space-separated. |
| Drift $ | Absolute option-price drift threshold. |
| Drift % | Percent drift threshold. |
| All channels | Record from every visible channel instead of the configured list. |
| Live quotes | Allow yfinance option quote lookup when parsed contracts are available. |
| Save | Persists recorder settings. |
| Test | Runs Discord REST diagnostics against token and channel access. |
| Start | Starts the Discord gateway listener. |
| Stop | Stops the listener. |
| Capture | Starts a recording session boundary. |
| End | Stops the active recording session. |
| Preview | Runs alert parsing without inserting a record. |

### Recorder Imports

Imports historical data without connecting to Discord:

| Import | Required fields |
| --- | --- |
| Discord alert CSV | `message_id,channel_id,channel_name,author_id,author_name,discord_timestamp,content` |
| Option price CSV | `timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume` |
| Stock price CSV | `timestamp,symbol,open,high,low,close,volume` |

Optional option columns:

```text
bid,ask,mid,last,open_interest,implied_volatility,delta,theta
```

### Exports

Exports are written under:

```text
data/recordings/
```

File paths include date, channel ID, channel name, and export timestamp.

Export types:

| Type | Contents |
| --- | --- |
| `alerts` | Discord metadata plus parser fields. |
| `joined` | Alert rows plus market snapshot and price drift columns. |

### Sentinel Echo Replay

The UI can fetch the Sentinel Echo replay feed and write JSONL test-run manifests.

| Control | Function |
| --- | --- |
| Channel | Optional channel filter. |
| Since | Optional ISO timestamp cursor. |
| Events | Fetches `/api/sentinel-echo/replay/events`. |
| JSONL | Writes a recorded replay manifest through `/api/sentinel-echo/test-runs`. |

### Playback

| Control | Function |
| --- | --- |
| Speed | Timestamp batches per second while active. |
| Loop | Restart from index `0` after the final batch. |
| Start | Starts selected replay session. |
| Step | Advances one timestamp batch. |
| Stop | Stops replay. |

### Handoff Composer

Builds `edge.pulse.handoff.v1` payloads and posts them to:

```text
POST /api/edge/handoff
X-API-Key: local-sim-key
```

Supported UI actions:

```text
buy
sell
trailing_stop
opening_trailing_stop
tighten_trailing_stop
regular_stop
stop_all
emergency_exit
dca
stop_buying
```

### Positions

Shows simulated positions:

- Symbol
- Quantity
- Average entry
- Current price
- P&L
- Trailing stop status

### Recorded Alerts

Shows recent parsed Discord alerts and drift state:

- Parse status
- Action
- Contract
- Alert price
- Market price
- Drift amount and percent

### Decision And Event Tape

Shows recent simulation decisions and event-log entries such as:

- `replay_imported`
- `replay_started`
- `replay_stopped`
- `handoff`
- `buy`
- `sell`
- `regular_stop_sell`
- `take_profit_sell`
- `trailing_stop_sell`

## Handoff Behavior

### Buy

A buy requires a price from one of:

1. Handoff metadata: `price`, `current_price`, or `market_price`.
2. Current replay price.
3. Existing position current price.

If no price is available, the buy is rejected with `price_unavailable`.

When accepted:

1. Quantity resolves from `metadata.quantity` or default quantity.
2. Fill ratio is applied.
3. Buy slippage is applied.
4. Commission is added.
5. Cash decreases.
6. Position state updates.
7. Decision and event entries are recorded.

### Sell

A sell requires an existing position.

If no position exists, it rejects with `position_not_found`.

When accepted:

1. Current price resolves.
2. Sell slippage is applied.
3. Commission is subtracted.
4. Cash increases.
5. Position is closed or removed.

### Regular Stop

Regular stop closes a matching position at the current simulated price when sent as a handoff. Automatic regular stop also runs on replay step when:

```text
bar.low <= avg_entry * (1 - stop_percent / 100)
```

### Take Profit

Automatic take profit runs on replay step when:

```text
bar.high >= avg_entry * (1 + target_percent / 100)
```

### Trailing Stop

Trailing actions enable or update trailing stop fields on ticker and position state.

On each replay bar:

```text
high_water_mark = max(previous_high_water_mark, bar.high)
trailing_floor = high_water_mark * (1 - trailing_percent / 100)
```

If:

```text
bar.low <= trailing_floor
```

the engine closes the position at the trailing floor.

### Stop All and Emergency Exit

Both close every open simulated position using current simulated prices.

### DCA

Currently routes through buy behavior.

### Stop Buying

Marks the ticker disabled in ticker state.

### Idempotency

Duplicate handoff `idempotency_key` values return the original handoff result without applying side effects twice.

## Discord Recorder Details

### Saved Settings

Stored in SQLite and returned masked:

```json
{
  "discord_token": "********",
  "discord_channel_ids": ["123456789", "987654321"],
  "drift_amount_threshold": 0.05,
  "drift_percent_threshold": 10.0,
  "yfinance_enabled": false,
  "record_all_channels": false
}
```

If the saved token field is submitted as `********`, the existing token is preserved.

`discord_channel_ids` is normalized on save. Duplicate IDs are removed, and pasted newline, comma, semicolon, or space-separated values become a stable list.

### Discord Diagnostics

`POST /api/recorder/discord/test` checks:

- token configured
- token authentication through Discord REST
- bot user identity
- configured channel accessibility
- `record_all_channels` mode
- current recorder state and last error

The response never includes the token.

### Channel And Author Filtering

A message is recorded only when:

- the source channel is enabled
- `record_all_channels` is true, the channel ID is configured, or the channel is a known source
- author is not in `ignored_author_ids`
- author is in `allowed_author_ids` when that allow-list is non-empty
- message is not from the bot itself

### Alert Parsing

The parser understands:

- buy/open words such as `BTO`, `BUY`, `ENTRY`, `ENTERING`, `LONG`
- sell/exit words such as `STC`, `SELL`, `TRIM`, `CLOSE`, `EXIT`, `OUT`
- average-down words such as `AVG DOWN`, `ADDING`, `ADD TO`
- contract forms such as `SPY 500C 6/21 @ 1.25`
- percentage exits such as `SELL 50%`
- embed title, description, field, author, and footer text

Contract keys normalize as:

```text
UNDERLYING|YYYY-MM-DD|STRIKE|CALL
UNDERLYING|YYYY-MM-DD|STRIKE|PUT
```

Unparsed messages are still stored for parser improvement.

Golden parser cases live in `tests/fixtures/alert_parser_golden.json` and are
verified by:

```bash
python -m pytest tests/test_alert_parser_golden.py
```

Add new analyst alert formats to that corpus before changing parser behavior, so
replay consumers get deterministic expected fields for buy, sell, close,
average-down, and unparsed messages.

### Market Snapshot And Drift

When a parsed alert has an option contract:

1. The recorder looks up the latest option bar at or before the alert timestamp.
2. It also looks up stock price when available.
3. It selects option market price from `mid`, `last`, `close`, bid/ask midpoint, bid, or ask.
4. It stores a snapshot.
5. It calculates price drift.

Drift formula:

```text
price_drift_amount = market_price_at_alert - alert_price
price_drift_pct = price_drift_amount / alert_price * 100
```

`price_drift_alert` is true when:

```text
abs(price_drift_amount) >= drift_amount_threshold
or
abs(price_drift_pct) >= drift_percent_threshold
```

## Sentinel Echo Test Feed

The engine publishes a stable replay contract for Sentinel Echo:

```text
GET /api/sentinel-echo/replay/events
```

Query parameters:

| Parameter | Purpose |
| --- | --- |
| `limit` | Maximum events returned. |
| `channel_id` | Optional legacy single-channel filter. |
| `channel_ids` | Optional comma, space, or newline-separated multi-channel filter. |
| `since` | Optional minimum Discord timestamp. |

Response contract:

```json
{
  "contract_version": "simulation.sentinel-echo.replay.v1",
  "mode": "simulation",
  "execution": "none",
  "event_count": 1,
  "manifest_hash_algorithm": "sha256",
  "manifest_sha256": "64-character lowercase hex digest of the JSONL replay events",
  "filters": {
    "channel_id": null,
    "channel_ids": ["123", "456"],
    "since": null,
    "limit": 1000
  },
  "next_cursor": null,
  "events": [
    {
      "event_id": "discord_alert:m1",
      "type": "discord_alert",
      "timestamp": "2026-06-19T14:30:00+00:00",
      "channel_id": "123",
      "payload": {
        "message": {},
        "alert": {},
        "market_snapshot": {},
        "price_drift": {}
      }
    }
  ]
}
```

Write a JSONL test-run manifest:

```text
POST /api/sentinel-echo/test-runs
```

Request:

```json
{
  "name": "Sentinel Echo smoke",
  "channel_ids": ["123", "456"],
  "since": null,
  "limit": 1000
}
```

`channel_id` is still accepted for older single-channel callers. New integrations should prefer `channel_ids`.

The manifest is written under:

```text
data/recordings/YYYY-MM-DD/sentinel-echo-test-runs/
```

This is still recorder-only. It does not trade.

The `manifest_sha256` returned by `/api/sentinel-echo/replay/events` is computed from the same JSONL event bytes written by `/api/sentinel-echo/test-runs`. Store that digest with any replay acceptance result so downstream bots can prove they tested the exact replay input.

## API Reference

All paths below are prefixed by `/api`.

### Health And Static UI

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Service health and replay summary. |
| GET | `/live` | Edge-style liveness. |
| GET | `/ready` | Readiness response. |
| GET | `/` | Built UI when `dist/index.html` exists. |

### Native Simulation

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/simulation/state` | Full simulation snapshot. |
| GET | `/simulation/config` | Current execution settings. |
| PUT | `/simulation/config` | Replace execution settings. |
| POST | `/simulation/reset` | Reset replay/account/decisions/logs. |
| POST | `/simulation/replay/import/csv` | Import OHLCV replay CSV. |
| GET | `/simulation/replay/sessions` | List imported replay sessions. |
| GET | `/simulation/replay/sessions/{session_id}` | Session metadata and bars. |
| POST | `/simulation/replay/sessions/{session_id}/start` | Start replay. |
| POST | `/simulation/replay/step` | Advance one timestamp batch. |
| POST | `/simulation/replay/stop` | Stop replay. |
| POST | `/simulation/handoff` | Native handoff without API key. |

### Cross-Bot Event Bus

These routes are for local replay, Discord observation, and test-run event exchange. They are not order-entry routes and fail closed unless `SIMULATION_EVENT_BUS_SECRET` is configured as a non-placeholder value of at least 32 characters.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/bus/events` | Read recent local simulation events. Requires `X-Simulation-Event-Bus-Secret`. |
| POST | `/bus/events` | Publish a local simulation event. Requires `X-Simulation-Event-Bus-Secret`. |

### Edge-Compatible Facade

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/automation` | Automation settings and last handoff. |
| GET | `/decisions` | Decision tape. |
| GET | `/pulse/handoff/schema` | `edge.pulse.handoff.v1` schema document. |
| GET | `/pulse/account` | Simulated account status. |
| GET | `/pulse/positions` | Simulated positions. |
| GET | `/price/{symbol}` | Current replay price. |
| GET | `/quote/{symbol}` | Current replay quote. |

### Pulse-Compatible Edge API

These require `X-API-Key: local-sim-key`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/edge/status` | Pulse Edge integration status. |
| GET | `/edge/account/status` | Account and positions array. |
| GET | `/edge/tickers` | Ticker states. |
| GET | `/edge/positions/{symbol}` | Position detail or no-position response. |
| POST | `/edge/handoff` | Primary structured handoff endpoint. |
| POST | `/edge/tickers/{symbol}/decision` | Legacy decision endpoint mapped into handoff actions. |
| POST | `/edge/tickers/{symbol}/trailing` | Legacy trailing endpoint mapped into `trailing_stop`. |
| POST | `/edge/signals/evaluate` | Lightweight signal scoring. |

Legacy decision mapping:

| Input | Structured action |
| --- | --- |
| `enable_trailing_stop` | `trailing_stop` |
| `trailing` | `trailing_stop` |
| `emergency_stop` | `emergency_exit` |
| `stop` | `regular_stop` |
| anything else | passed through |

### Recorder Settings And Capture

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/recorder/discord/settings` | Read masked recorder settings. |
| PUT | `/recorder/discord/settings` | Save token, channels, drift thresholds, and provider flags. |
| POST | `/recorder/discord/test` | Run Discord REST diagnostics. |
| POST | `/recorder/discord/start` | Start Discord listener. |
| POST | `/recorder/discord/stop` | Stop Discord listener. |
| GET | `/recorder/discord/status` | Recorder counts and state. |
| POST | `/recorder/discord/parse-preview` | Parse sample text without inserting. |
| POST | `/recorder/discord/ingest-message` | Ingest a synthetic Discord message. |
| POST | `/recorder/dev/ingest-message` | Development alias for synthetic ingest. |
| POST | `/recorder/discord/import-csv` | Import Discord message CSV. |
| POST | `/recorder/market/import/options-csv` | Import option bars. |
| POST | `/recorder/market/import/stocks-csv` | Import stock bars. |

### Recorder Data

Message, alert, replay, export, and Sentinel Echo endpoints accept `channel_id` for one channel or `channel_ids` for a comma-separated channel set.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/recordings/sessions/start` | Start a capture session boundary. |
| POST | `/recordings/sessions/stop` | Stop active capture session. |
| GET | `/recordings/sessions/active` | Read active session. |
| GET | `/recordings/sessions` | List capture sessions. |
| GET | `/recordings/messages` | List Discord messages. |
| GET | `/recordings/alerts` | List parsed alerts. |
| GET | `/recordings/market-bars` | List imported/observed market bars. |
| GET | `/recordings/market-snapshots` | List snapshots. |
| GET | `/recordings/drift-events` | List drift events. |
| POST | `/recordings/export` | Write alert or joined CSV export. |
| GET | `/recordings/exports` | List export records. |
| GET | `/replay/events` | Generic chronological truth stream. |

### Sentinel Echo

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/sentinel-echo/replay/events` | Joined replay event stream for Sentinel Echo. |
| POST | `/sentinel-echo/test-runs` | Write JSONL replay test-run manifest. |

## Data Files

| Path | Purpose |
| --- | --- |
| `data/sentinel_archive.sqlite3` | Recorder SQLite database. |
| `data/recordings/` | CSV and JSONL exports. |
| `dist/` | Built React control panel. |
| `.venv/` | Local Python environment. |
| `node_modules/` | Frontend dependencies. |

## Sentinel Core Integration

Point both bot URLs at this app:

```powershell
$env:EDGE_API_URL = "http://127.0.0.1:9200"
$env:PULSE_API_URL = "http://127.0.0.1:9200"
$env:PULSE_EDGE_API_KEY = "local-sim-key"
```

In this mode:

- Sentinel Core reads Edge-style status from `/api/live`, `/api/ready`, `/api/automation`, `/api/decisions`, and `/api/pulse/*`.
- Sentinel Core reads Pulse-style status from `/api/health` and `/api/edge/*`.
- Both facades use the same simulation state.

## Sentinel Echo Integration

In Sentinel Echo, set:

```powershell
$env:SENTINEL_ARCHIVE_REPLAY_URL = "http://127.0.0.1:9200/api/sentinel-echo/replay/events"
```

Then Sentinel Echo can call:

```text
POST /api/sentinel-archive/replay-preview
```

to run recorded engine events through Sentinel Echo parser/source-policy logic without inserting alerts or sending broker orders.

## Common Workflows

### Make Sentinel Core Show A Simulated Position

1. Start this engine on port `9200`.
2. Import OHLCV CSV with at least one `SPY` row.
3. Select the replay session.
4. Press `Start` or `Step` until `Current Prices` includes `SPY`.
5. Send a `buy` from Handoff Composer.
6. Sentinel Core can read the resulting position from Pulse and Edge facade endpoints.

### Test A Trailing Stop

1. Import bars where price rises, then a later low crosses the trailing floor.
2. Step to the first price.
3. Send `buy`.
4. Send `trailing_stop`.
5. Continue replay.
6. The engine closes the position when replay low crosses the trailing threshold.

### Build A Sentinel Echo Test Dataset

1. Start a capture session.
2. Import or listen to Discord alert messages.
3. Import option price CSV for matching contracts.
4. Review recorded alerts and drift.
5. Export `joined` CSV for analysis.
6. Use the Sentinel Echo Replay panel to write JSONL.
7. Point Sentinel Echo at `/api/sentinel-echo/replay/events`.

## Project Structure

```text
Sentinel-Archive/
  sentinel_archive/
    api.py                  FastAPI app and Edge/Pulse facades
    core.py                 replay and simulated account engine
    csv_import.py           OHLCV CSV parser
    models.py               simulation and handoff models
    alert_parser.py         Discord option alert parser
    discord_recorder.py     Discord listener and recorder orchestration
    discord_diagnostics.py  Discord REST diagnostics
    market_recorder.py      stock/option bar import, snapshots, drift
    recorder_api.py         recorder and Sentinel Echo replay routes
    recording_store.py      SQLite persistence and exports
  frontend/src/
    App.tsx                 control panel
    api.ts                  typed API client
    styles.css              dashboard styles
  tests/                    backend and recorder tests
  Launch-Sentinel-Archive.ps1
```

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest
npm run build
.\Launch-Sentinel-Archive.ps1 -SmokeTest
python -m unittest tests.test_launcher_lifecycle_static -v
```

Expected:

- Python tests pass.
- Vite build writes `dist/`.
- Launcher smoke test reports prerequisites without starting the app.

## Troubleshooting

### `discord_token_missing`

No saved token and no `DISCORD_BOT_TOKEN` environment variable exist. Save a token in the UI or set the environment variable.

### `invalid_token`

Discord REST rejected the bot token. Regenerate or re-copy the token from the Discord Developer Portal.

### Channel Access Fails

Check that:

- channel ID is correct
- bot was invited to the server
- bot can view the channel
- bot can read message history
- Message Content Intent is enabled if you need message body parsing

### Imported Alerts Do Not Parse

Use Parse Preview and compare the raw alert text to supported parser formats. Unparsed rows remain in SQLite so parser work can be done later.

### Drift Is Unavailable

Make sure option market CSV rows use the same normalized contract:

```text
UNDERLYING|YYYY-MM-DD|STRIKE|CALL_OR_PUT
```

and the market bar timestamp is at or before the Discord alert timestamp.

### Browser Shows Old UI

Run:

```powershell
npm run build
```

Then restart uvicorn or the launcher.

## Current Limitations

- Simulated replay state is in memory and resets on process restart.
- The Discord recorder is not a broker, paper trader, or portfolio simulator.
- yfinance quote enrichment depends on network availability and third-party data behavior.
- The parser is intentionally conservative; unsupported analyst formats are stored as unparsed.
- Sentinel Echo JSONL manifests are replay inputs, not execution records.
