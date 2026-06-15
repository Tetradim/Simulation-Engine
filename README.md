# Sentinel Simulation Engine

Standalone recorded-market replay and Edge/Pulse contract simulator for the Sentinel trading suite.

The engine can run by itself, stand in for Pulse, stand in for Edge, or serve both contracts at once. Tandem Suite can point both bot URLs at this service and show changing trade state without connecting to a broker.

## Current Feature Map

| Area | Current capability |
|------|--------------------|
| Recorded replay | Imports OHLCV CSV bars, groups rows by timestamp, and advances replay in deterministic timestamp batches. |
| Market state | Maintains current replay prices per symbol and exposes quote/price endpoints for Edge/Pulse-style clients. |
| Simulated account | Tracks cash, equity, buying power, open positions, average entry, current price, P&L dollars, P&L percent, trailing state, and day P&L. |
| Execution realism | Configurable starting cash, default quantity, max allocation percent, fill ratio, slippage basis points, commission, confidence threshold, default trailing percent, regular stop percent, and take-profit percent. |
| Handoff contract | Accepts `edge.pulse.handoff.v1` payloads at `/api/edge/handoff` and `/api/simulation/handoff`. |
| Action coverage | Handles `buy`, `sell`, `regular_stop`, `trailing_stop`, `opening_trailing_stop`, `tighten_trailing_stop`, `stop_all`, `emergency_exit`, `dca`, and `stop_buying`. |
| Risk exits | Evaluates regular stop, take profit, and trailing stop against replay bar high/low values on every replay step. |
| Idempotency | Duplicate `idempotency_key` values return the original handoff result without applying side effects twice. |
| Edge facade | Serves `/api/live`, `/api/ready`, `/api/automation`, `/api/decisions`, `/api/pulse/handoff/schema`, `/api/pulse/account`, and `/api/pulse/positions`. |
| Pulse facade | Serves `/api/health`, `/api/edge/status`, `/api/edge/account/status`, `/api/edge/tickers`, `/api/edge/positions/{symbol}`, legacy decision/trailing endpoints, and lightweight signal scoring. |
| Tandem support | Can be used as both `EDGE_API_URL` and `PULSE_API_URL` so Tandem shows a full pair dashboard without live brokers. |
| Control panel | Ships a React/Vite UI served by FastAPI after build, with execution settings, replay import/playback, handoff composer, positions, and decision/event tape. |
| Windows launcher | Starts the engine, opens a dedicated browser profile, and now supports Pulse-style "one closes the other" cleanup between browser and process. |

## Safety Boundary

The Simulation Engine is a paper/simulation-only process. It does not connect to brokers, does not place live orders, and does not persist account state across process restarts. It is meant for local replay, UI integration testing, Tandem demos, Edge/Pulse contract testing, and operator workflow validation before using real broker-connected services.

## Local Work Folder

```powershell
C:\Users\Lite OS\Documents\Codex\2026-06-12\c-users-lite-os-openclaw-workspace\work\Simulation-Engine
```

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm install
npm run build
.\.venv\Scripts\python.exe -m uvicorn simulation_engine.main:app --host 127.0.0.1 --port 9200
```

Open `http://127.0.0.1:9200`.

Windows launcher:

```powershell
.\Launch-Sentinel-Simulation-Engine.ps1
```

The Windows launcher opens the control panel in a dedicated browser window with an isolated local profile. Closing that browser window stops the Simulation Engine process started by the launcher. Closing the launcher window or pressing Ctrl+C closes the dedicated browser profile and stops the server. Use `-NoBrowser` when you intentionally want a headless run without browser-close monitoring.

Useful launcher flags:

| Flag | Purpose |
|------|---------|
| `-Port 9200` | Choose the FastAPI/control-panel port. |
| `-NoBrowser` | Start the server without opening a browser or monitoring browser close. |
| `-InstallDeps` | Install Python and frontend dependencies before launch. |
| `-Rebuild` | Rebuild the React control panel before launch. |
| `-SmokeTest` | Check launcher prerequisites without starting the engine. |

## How It Works

The engine keeps one in-memory simulation state:

1. You import recorded OHLCV bars from CSV.
2. The replay clock advances through those bars.
3. Each replay step updates `current_prices`.
4. Handoff commands buy, sell, stop, or enable trailing stops against those current prices.
5. The account model recalculates cash, equity, buying power, positions, P&L, and risk exits.
6. Edge-compatible and Pulse-compatible endpoints expose that same state to Tandem, Edge, or Pulse.

Nothing is persisted yet. Restarting the process clears imported sessions, positions, idempotency keys, decisions, and logs.

## Launcher Lifecycle

`Launch-Sentinel-Simulation-Engine.ps1` is designed for single-window local operation:

1. The launcher verifies Python and npm are available.
2. It creates/uses `.venv`.
3. It installs dependencies when requested or missing.
4. It builds the control panel when requested or when `dist/index.html` is missing.
5. It replaces an existing listener on the selected port.
6. It starts uvicorn with `simulation_engine.main:app`.
7. It waits for `/api/health` to identify the service as `sentinel-simulation-engine`.
8. Unless `-NoBrowser` is set, it opens a dedicated Edge/Chrome app window with a temporary browser profile.
9. It starts a hidden watchdog that closes the dedicated browser profile and stops the server if the launcher process disappears.
10. The foreground loop stops the server if the dedicated browser window closes.

This matches the Sentinel Pulse local-source launcher behavior and keeps stale local simulation tasks from continuing after the operator UI has been closed.

## Tandem Suite Integration

Set Tandem Suite to use the simulation engine for both bot URLs:

```powershell
EDGE_API_URL=http://127.0.0.1:9200
PULSE_API_URL=http://127.0.0.1:9200
PULSE_EDGE_API_KEY=local-sim-key
```

In this setup:

- Tandem reads Edge status from `/api/live`, `/api/ready`, `/api/automation`, `/api/decisions`, and `/api/pulse/*`.
- Tandem reads Pulse status from `/api/health` and `/api/edge/*`.
- The simulation engine answers both sides from the same replay/account state.
- Pulse-compatible routes require `X-API-Key: local-sim-key`.

## Control Panel

The web UI is served by the FastAPI app after `npm run build`. It polls `/api/simulation/state` every 1.5 seconds.

### Top Metrics

`Equity` shows simulated account equity. It is calculated as cash plus the market value of all open positions.

`Replay Index` shows how many timestamp groups have been consumed from the active replay session. It advances when replay is active or when you press `Step`.

`Open Positions` shows the count of simulated broker positions and the current day P&L.

`Current Prices` shows how many symbols have a current replay price. A symbol appears here after replay reaches at least one bar for that symbol.

## Execution Model

The Execution Model panel edits the core assumptions used by the engine. Press `Save Model` to send the values to `PUT /api/simulation/config`.

Saving the model does not wipe existing positions. If there are no positions and cash still equals equity, the cash balance updates to the new starting cash. If positions already exist, the engine preserves current cash/positions and only changes future execution assumptions.

### Starting Cash

Initial account cash and equity.

Used by:

- New account creation.
- Day P&L calculation.
- Buying power and allocation checks.

### Default Quantity

The share/contract quantity used when a buy handoff does not include `metadata.quantity`.

Actual filled quantity is:

```text
default_quantity * fill_ratio
```

### Max Allocation %

Maximum percent of current total equity that one buy can allocate.

A buy is rejected with `risk_limit` when:

```text
fill_price * quantity + commission > total_equity * (max_allocation_pct / 100)
```

The same buy is also rejected if it costs more than available cash.

### Fill Ratio

Partial-fill multiplier.

Examples:

- `1.0` fills the whole requested/default quantity.
- `0.5` fills half.
- `0` rejects the buy with `zero_fill_quantity`.

### Slippage bps

Basis-point price adjustment applied to fills.

One basis point is `0.01%`.

Buy fill:

```text
fill_price = replay_price * (1 + slippage_bps / 10000)
```

Sell fill:

```text
fill_price = replay_price * (1 - slippage_bps / 10000)
```

Example: price `100`, slippage `10` bps.

- Buy fills at `100.10`.
- Sell fills at `99.90`.

### Commission

Flat commission charged per order.

Buy:

```text
cash -= fill_price * quantity + commission
```

Sell:

```text
cash += fill_price * quantity - commission
```

### Reject Below

Minimum accepted handoff confidence.

If a handoff has:

```text
confidence < reject_below
```

the engine rejects it with `confidence_below_threshold`.

### Trail %

Default trailing-stop percent used when a trailing action does not provide its own `trailing_percent`.

The Handoff Composer has its own `Trail %` input. That value is included directly in trailing handoff payloads and overrides this default for that handoff.

### Stop %

Regular stop-loss percent from average entry.

When replay advances, each open position checks the current bar low:

```text
stop_price = avg_entry * (1 - stop_percent / 100)
```

If:

```text
bar.low <= stop_price
```

the engine closes the position at `stop_price` and records `regular_stop_sell`.

Set `0` to disable this automatic regular stop rule.

### Target %

Take-profit percent from average entry.

When replay advances, each open position checks the current bar high:

```text
target_price = avg_entry * (1 + target_percent / 100)
```

If:

```text
bar.high >= target_price
```

the engine closes the position at `target_price` and records `take_profit_sell`.

Set `0` to disable this automatic take-profit rule.

## Market Day Replay

The replay system uses recorded OHLCV bars. It does not generate price movement.

### CSV Format

Required columns:

```csv
timestamp,symbol,open,high,low,close,volume
2026-06-09T13:30:00Z,SPY,540.10,541.00,539.80,540.75,1200
```

Optional columns:

```text
vwap,trade_count,source
```

Import behavior:

- Symbols are uppercased.
- Rows are sorted by `timestamp`, then `symbol`.
- Each unique timestamp is replayed as one batch.
- `close` becomes the current price shown to Edge/Pulse.
- `high` and `low` drive stop-loss, take-profit, and trailing-stop checks.

### Session Name

Label stored with the imported bars. It is used in the session list and in the generated session fingerprint.

### Load CSV

Reads a local `.csv` file into the text area. It does not import until you press `Import Bars`.

### Import Bars

Calls:

```text
POST /api/simulation/replay/import/csv
```

The backend parses the CSV and creates a replay session:

```text
session_id = replay-{sha256(name + first timestamp + last timestamp + symbols + bar count)[0:12]}
```

The import also creates ticker records for the imported symbols.

## Playback

Playback controls operate on the selected replay session.

### Speed

Controls automatic replay speed after `Start`.

Internally the FastAPI lifespan task runs this loop:

```text
if replay.active:
    step()
    sleep(max(0.05, 1 / speed))
```

Examples:

- `1` means about one timestamp batch per second.
- `20` means about twenty batches per second.
- Very high speeds are capped by the minimum `0.05` second sleep.

### Loop

When enabled, replay wraps back to index `0` after the final bar.

When disabled, replay stops after the final bar.

### Start

Calls:

```text
POST /api/simulation/replay/sessions/{session_id}/start
```

This sets:

```json
{
  "active": true,
  "session_id": "...",
  "speed": 30,
  "loop": false,
  "index": 0
}
```

### Step

Calls:

```text
POST /api/simulation/replay/step
```

This advances one timestamp batch. If multiple symbols share the same timestamp, they advance together.

For every bar in the batch:

- `current_prices[symbol] = bar.close`
- Open position current prices are updated.
- P&L is recalculated.
- Trailing stops, regular stops, and take-profit rules are evaluated.

### Stop

Calls:

```text
POST /api/simulation/replay/stop
```

This sets `replay.active = false`. It does not clear imported bars or positions.

## Handoff Composer

The Handoff Composer builds an Edge-to-Pulse handoff payload and posts it to:

```text
POST /api/edge/handoff
X-API-Key: local-sim-key
```

The payload shape matches `edge.pulse.handoff.v1`.

Example generated payload:

```json
{
  "contract_version": "edge.pulse.handoff.v1",
  "symbol": "SPY",
  "action": "buy",
  "confidence": 0.9,
  "reason": "operator simulation control",
  "mode": "paper",
  "orb_session": "market_open",
  "idempotency_key": "edge:SPY:buy:market_open:29698555:ui",
  "source": "sentinel_edge",
  "created_at": 1781390000.0,
  "metadata": {}
}
```

### Symbol

Ticker symbol for the handoff. It is uppercased before sending.

Use `GLOBAL` for portfolio-wide actions such as `stop_all`, `emergency_exit`, or global trailing changes.

### Action

Supported actions:

| Action | What It Does |
| --- | --- |
| `buy` | Opens or adds to a position using current replay price. |
| `sell` | Closes the symbol position using current replay price. |
| `trailing_stop` | Enables trailing stop on the symbol. |
| `opening_trailing_stop` | Enables trailing stop using the same simulation behavior as `trailing_stop`. |
| `tighten_trailing_stop` | Enables or updates trailing percent on the symbol. |
| `regular_stop` | Closes the symbol position using current replay price. |
| `stop_all` | Closes all open positions. |
| `emergency_exit` | Closes all open positions. |
| `dca` | Processes like a buy in the current first release. |
| `stop_buying` | Marks the ticker disabled. |

### Confidence

Sent as `confidence` in the handoff payload.

The engine accepts it unless it is below Execution Model `Reject Below`.

### Trail %

Used only for actions containing `trailing`.

For those actions, the UI adds:

```json
{
  "stop_type": "trailing",
  "trailing_percent": 2
}
```

### Idempotency

The UI builds idempotency keys with the current minute:

```text
edge:{SYMBOL}:{ACTION}:market_open:{minute}:ui
```

If the same idempotency key is submitted twice, the second request does not apply side effects again. It returns the original handoff id with reason `duplicate`.

## Position Lifecycle

### Buy Flow

A buy handoff needs a current price. Current price comes from:

1. `handoff.metadata.price`, `current_price`, or `market_price`
2. `current_prices[symbol]` from replay
3. existing position current price

If no price is available, buy rejects with:

```text
price_unavailable
```

If accepted:

1. Quantity is calculated from `metadata.quantity` or Execution Model `Default Quantity`.
2. Fill ratio is applied.
3. Buy slippage is applied.
4. Commission is added.
5. Cash and position state update.
6. A `buy` decision is added to the decision tape.
7. A handoff event is added to the event log.

### Sell Flow

A sell handoff requires an existing position.

If no position exists, sell rejects with:

```text
position_not_found
```

If accepted:

1. Current price is resolved.
2. Sell slippage is applied.
3. Commission is subtracted.
4. Cash increases.
5. The position is removed.
6. A sell decision is added to the decision tape.

### Trailing Stop Flow

When a trailing action is accepted:

1. The ticker gets `trailing_enabled = true`.
2. The ticker gets `trailing_percent`.
3. If a position exists, that position also gets trailing fields.
4. The position high-water mark starts from the current price.

On every replay bar:

```text
high_water_mark = max(previous high_water_mark, bar.high)
trailing_floor = high_water_mark * (1 - trailing_percent / 100)
```

If:

```text
bar.low <= trailing_floor
```

the engine closes the position at `trailing_floor` and records `trailing_stop_sell`.

## Positions Panel

Positions are read from the simulated account state.

Columns:

- `Symbol`: ticker.
- `Qty`: simulated filled quantity.
- `Entry`: average entry price after slippage.
- `Price`: latest replay close for the symbol.
- `PnL`: unrealized P&L and percent.
- `Trail`: trailing percent if enabled.

## Decision And Event Tape

This panel merges recent decisions and event log entries.

Decision examples:

- `buy`
- `sell`
- `trailing_stop`
- `regular_stop_sell`
- `take_profit_sell`
- `trailing_stop_sell`

Event examples:

- `replay_imported`
- `replay_started`
- `replay_stopped`
- `handoff`

The API stores the newest entries first.

## Native Simulation API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/simulation/state` | Full engine snapshot |
| GET | `/api/simulation/config` | Current execution settings |
| PUT | `/api/simulation/config` | Replace execution model settings |
| POST | `/api/simulation/reset` | Reset replay/account/decisions/logs; optional config body |
| POST | `/api/simulation/replay/import/csv` | Import OHLCV bars |
| GET | `/api/simulation/replay/sessions` | List imported sessions |
| GET | `/api/simulation/replay/sessions/{session_id}` | Session metadata plus bars |
| POST | `/api/simulation/replay/sessions/{session_id}/start` | Start replay |
| POST | `/api/simulation/replay/step` | Advance one timestamp batch |
| POST | `/api/simulation/replay/stop` | Stop replay |
| POST | `/api/simulation/handoff` | Send a native handoff payload without API key |

## Edge-Compatible Endpoints

These let the engine stand in for Edge when Tandem reads it.

| Method | Endpoint | Behavior |
| --- | --- | --- |
| GET | `/api/live` | Returns liveness. |
| GET | `/api/ready` | Always ready in this first release. |
| GET | `/api/automation` | Returns handoff settings and last handoff. |
| GET | `/api/decisions` | Returns decision tape. |
| GET | `/api/pulse/handoff/schema` | Returns `edge.pulse.handoff.v1` schema document. |
| GET | `/api/pulse/account` | Returns simulated account status. |
| GET | `/api/pulse/positions` | Returns simulated positions. |
| GET | `/api/price/{symbol}` | Returns current replay price or 404. |
| GET | `/api/quote/{symbol}` | Returns current replay quote or 404. |

## Pulse-Compatible Edge Endpoints

These let the engine stand in for Pulse when Edge or Tandem calls Pulse's Edge integration API.

All require:

```text
X-API-Key: local-sim-key
```

| Method | Endpoint | Behavior |
| --- | --- | --- |
| GET | `/api/edge/status` | Simulated Pulse Edge API status. |
| GET | `/api/edge/account/status` | Simulated account with positions array. |
| GET | `/api/edge/tickers` | Tickers imported from replay or touched by handoffs. |
| GET | `/api/edge/positions/{symbol}` | Position detail or `has_position: false`. |
| POST | `/api/edge/handoff` | Primary structured handoff endpoint. |
| POST | `/api/edge/tickers/{symbol}/decision` | Legacy decision endpoint mapped into handoff actions. |
| POST | `/api/edge/tickers/{symbol}/trailing` | Legacy trailing endpoint mapped into `trailing_stop`. |
| POST | `/api/edge/signals/evaluate` | Lightweight signal scoring response. |

## Legacy Decision Mapping

`POST /api/edge/tickers/{symbol}/decision` maps older Pulse commands into structured handoff actions:

| Legacy Input | Structured Action |
| --- | --- |
| `enable_trailing_stop` | `trailing_stop` |
| `trailing` | `trailing_stop` |
| `emergency_stop` | `emergency_exit` |
| `stop` | `regular_stop` |
| anything else | passed through as action |

## Common Workflows

### Make Tandem Show A Live Position

1. Start Simulation Engine on port `9200`.
2. Start Tandem with both URLs pointed at `http://127.0.0.1:9200` and key `local-sim-key`.
3. Import a CSV with at least one `SPY` row.
4. Select the imported session.
5. Press `Start` or `Step` until `Current Prices` shows `SPY`.
6. In Handoff Composer, use symbol `SPY`, action `buy`, confidence `0.9`.
7. Press `Send Handoff`.
8. Tandem will read the resulting position through `/api/edge/account/status` and `/api/pulse/positions`.

### Test A Trailing Stop

1. Import bars where price rises, then the later bar low falls below the trailing floor.
2. Step to the first price.
3. Send `buy`.
4. Send `trailing_stop` with Trail `%`.
5. Continue replay.
6. When `bar.low <= high_water_mark * (1 - trail / 100)`, the engine sells and records `trailing_stop_sell`.

### Test Risk Rejection

1. Set `Reject Below` to `0.8`.
2. Save model.
3. Send a handoff with `Confidence` `0.5`.
4. The response is rejected with `confidence_below_threshold`.

### Test Allocation Rejection

1. Set low Starting Cash or low Max Allocation `%`.
2. Set high Default Quantity.
3. Replay a price.
4. Send `buy`.
5. If cost exceeds cash or allocation cap, the response is rejected with `risk_limit`.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest
npm run build
.\Launch-Sentinel-Simulation-Engine.ps1 -SmokeTest
python -m unittest tests.test_launcher_lifecycle_static -v
```

Expected current backend test result:

```text
10 passed
```
