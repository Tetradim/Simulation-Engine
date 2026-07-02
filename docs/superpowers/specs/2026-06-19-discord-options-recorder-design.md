# Discord Options Recorder Design

## Goal

Add a data-collection mode to the Sentinel Archive that records Discord option alerts, stock price action, and option price observations so functional bots such as Sentinel Echo can later be tested against a recorded truth stream.

This feature is not a trading bot and must not place, paper-fill, or simulate orders from Discord alerts. Its job is to capture what happened, when it happened, and what prices were observable at that time.

## Scope

The first release supports both historical and live data collection:

- Import exported Discord alert CSV files.
- Connect to Discord with a bot token and listen to multiple configured channels.
- Store raw Discord messages, embeds, author metadata, channel metadata, parsed alert fields, and parse failures.
- Import stock and option OHLCV CSV files.
- Record market snapshots around incoming alerts when matching stock or option prices are available.
- Flag large drift between the alert-stated option price and the observed option market price.
- Export timestamped, channel-aware datasets for later bot replay and profitability testing.

Live market-data provider integration is included as an interface and UI/config surface, but CSV-backed recording is the first required implementation path. Provider-specific live quotes can be added behind the same snapshot interface.

## Non-Goals

- No broker connections.
- No live orders.
- No simulated fills, positions, or account P/L for Discord alerts.
- No replacement for Sentinel Echo execution logic.
- No use of Discord user tokens. Only official bot tokens are supported.

## Architecture

The existing Sentinel Archive remains a FastAPI service with a React control panel. This feature adds four backend modules:

- `discord_recorder.py`: owns Discord bot lifecycle, channel subscriptions, message extraction, and safe shutdown.
- `alert_parser.py`: ports the stable Sentinel Echo parser for options alerts and exposes parse metadata.
- `market_recorder.py`: imports stock/options CSV bars, stores live or replayed market observations, and resolves the latest known price for an alert timestamp.
- `recording_store.py`: SQLite persistence layer for settings, Discord messages, parsed alerts, market snapshots, sessions, and exports.

The existing replay/execution core can remain available for Edge/Pulse contract demos, but the Discord recorder path writes recorder data only. It should not call handoff execution methods.

## Data Flow

Live Discord flow:

1. Operator saves a Discord bot token and one or more channel IDs.
2. Operator starts the Discord recorder.
3. The bot receives a message from a monitored channel.
4. The recorder stores raw message content, embeds, author/channel metadata, Discord timestamp, and engine-received timestamp.
5. The parser attempts to normalize the message into an option alert.
6. If parsed, the market recorder resolves matching stock and option prices available at receive time.
7. The engine stores a snapshot and drift calculations.
8. The alert appears in the UI inbox and becomes exportable.

CSV flow:

1. Operator imports exported Discord messages as CSV.
2. Operator imports stock/options OHLCV CSV files.
3. The engine normalizes all records into the same SQLite tables used by live recording.
4. Exports and replay APIs treat imported and live-collected alerts identically, with source metadata identifying their origin.

## SQLite Storage

SQLite is the source of truth on disk. Default path:

```text
data/sentinel_archive.sqlite3
```

Tables:

- `recorder_settings`: token reference or encrypted token value, channel configuration, drift thresholds, provider settings, and recorder flags.
- `discord_sources`: guild ID/name, channel ID/name, enabled flag, allowed author IDs/names, ignored author IDs/names, parser profile, notes.
- `recording_sessions`: session ID, started/stopped timestamps, selected channels, data sources, and operator notes.
- `discord_messages`: message ID, channel ID/name, guild ID/name, author ID/name, Discord timestamp, engine-received timestamp, content, embeds JSON, attachments JSON, and raw payload JSON.
- `parsed_alerts`: message ID, parse status, parse error, action, ticker, expiration, strike, option type, alert price, sell percentage, confidence, parser profile, and raw normalized JSON.
- `market_bars`: source, instrument type, symbol or contract key, timestamp, open, high, low, close, volume, bid, ask, mid, and provider metadata.
- `market_snapshots`: alert ID, snapshot timestamp, underlying symbol, stock price, option contract key, option bid, ask, mid, last, selected market price, price source, and lookup status.
- `price_drift_events`: alert ID, alert price, market price, drift amount, drift percent, threshold settings, drift direction, and `price_drift_alert`.
- `exports`: export ID, created timestamp, channel ID/name, format, file path, row count, and filters.

Discord tokens must be masked in API responses and excluded from exports. If practical in the first implementation, store tokens encrypted using a local app secret; otherwise allow environment-variable token loading and store only a `token_configured` flag in SQLite.

## File Exports

Exports are written under:

```text
data/recordings/
```

Export folders and filenames include date/time and channel identity:

```text
data/recordings/2026-06-19/channel-123456789-sentinel-echo-alerts/20260619-143012-alerts.csv
data/recordings/2026-06-19/channel-123456789-sentinel-echo-alerts/20260619-143012-market-snapshots.csv
data/recordings/2026-06-19/channel-123456789-sentinel-echo-alerts/20260619-143012-replay-events.jsonl
```

Every export row includes:

- `discord_timestamp`
- `engine_received_timestamp`
- `channel_id`
- `channel_name`
- `guild_id`
- `guild_name`
- `author_id`
- `author_name`
- source type: `live_discord`, `discord_csv`, `market_csv`, or future provider ID

## Discord Configuration

The control panel gets a Discord Recorder tab with:

- Bot token input with save/test/start/stop controls.
- Multi-channel editor for channel ID, friendly name, enabled flag, and notes.
- Optional allowed authors and ignored authors per channel.
- Message content intent status guidance.
- Parser preview textbox copied from Sentinel Echo behavior.
- Connection status: stopped, connecting, connected, failed.
- Last message time, messages recorded count, parsed count, unparsed count, drift-alert count.

The backend supports:

- `GET /api/recorder/discord/settings`
- `PUT /api/recorder/discord/settings`
- `POST /api/recorder/discord/test`
- `POST /api/recorder/discord/start`
- `POST /api/recorder/discord/stop`
- `POST /api/recorder/discord/parse-preview`
- `GET /api/recorder/discord/status`

The recorder must ignore its own bot messages and only process configured channels unless the operator explicitly enables all-channel mode.

## Alert Parsing

The first parser should port Sentinel Echo's proven options parser:

- Buy/open terms: BTO, buy, bought, entry, entering, long, opening.
- Sell/exit terms: STC, sell, sold, trim, close, exit, out.
- Average-down terms: average down, avg down, add to, adding.
- Contracts: ticker, strike, call/put, expiration.
- Prices: `@ 1.25`, `entry 1.25`, `fill 1.25`, `$.29`.
- Partial exits: percent, half, quarter, all.

Parser output is stored even when incomplete. Unparsed messages remain valuable because they identify formats that need new parser rules.

## Market Data Recording

The Engine records market observations from two source classes:

- CSV import: stock and option OHLCV or quote files.
- Live provider interface: future quote providers that can supply stock and option prices at alert receipt time.

The first required CSV schemas are:

Stock bars:

```csv
timestamp,symbol,open,high,low,close,volume
```

Option bars:

```csv
timestamp,underlying,expiration,strike,option_type,open,high,low,close,volume
```

Optional option quote fields:

```text
bid,ask,mid,last,open_interest,implied_volatility,delta,theta
```

Option contract keys use a normalized form:

```text
UNDERLYING|YYYY-MM-DD|STRIKE|CALL
UNDERLYING|YYYY-MM-DD|STRIKE|PUT
```

When a Discord alert is received, market lookup uses the latest known matching option observation at or before `engine_received_timestamp`. If no option price is available, the alert is stored with `market_price_unavailable`.

## Price Drift

Each parsed alert with an alert price gets drift calculations when an observed option market price is available:

```text
price_drift_amount = market_price_at_receive - alert_price
price_drift_pct = (price_drift_amount / alert_price) * 100
```

Drift alerting uses both configured thresholds:

```text
price_drift_alert = abs(price_drift_amount) >= drift_amount_threshold
                  or abs(price_drift_pct) >= drift_percent_threshold
```

Default thresholds:

- `drift_amount_threshold = 0.05`
- `drift_percent_threshold = 10.0`

Stored drift directions:

- `market_above_alert`
- `market_below_alert`
- `no_drift`
- `market_price_unavailable`

## Replay Dataset API

The Engine exposes recorded truth streams for other bots:

- `GET /api/recordings/sessions`
- `GET /api/recordings/messages`
- `GET /api/recordings/alerts`
- `GET /api/recordings/market-snapshots`
- `POST /api/recordings/export`
- `GET /api/recordings/exports`
- `GET /api/replay/events?session_id=...`

The replay event stream is chronological JSONL-compatible data:

```json
{"type":"discord_alert","timestamp":"2026-06-19T14:30:12Z","channel_id":"123","payload":{}}
{"type":"market_snapshot","timestamp":"2026-06-19T14:30:12Z","contract_key":"SPY|2026-06-19|540|CALL","payload":{}}
```

This lets Sentinel Echo or another bot replay exactly what the Engine saw without inheriting any execution behavior from the Engine.

## Error Handling

- Invalid Discord token: settings save can succeed, but test/start reports `invalid_token`.
- Missing message-content intent: status explains that the Discord Developer Portal intent must be enabled.
- Missing channel permissions: status reports channel IDs that could not be read.
- Parser failure: message stored with `parse_status = unparsed`.
- Missing market data: parsed alert stored with `lookup_status = market_price_unavailable`.
- Duplicate Discord message ID: do not insert a second raw message; update metadata only if useful.
- Unsafe regex patterns: reject nested quantifier and broad wildcard patterns, matching Sentinel Echo's validation approach.

## UI

Add a Recorder area to the existing control panel:

- Discord setup and status.
- Channel table with add/remove/test controls.
- Parser preview.
- Alert inbox with parsed fields and drift flags.
- Market data import for stock and option CSV files.
- Recording sessions and export controls.

The UI should stay utilitarian and data-dense. It should not imply that the Engine is trading. Labels should use terms like "record", "capture", "snapshot", and "export", not "execute" or "fill".

## Testing

Backend tests:

- Discord message content and embeds normalize into parseable text.
- Channel filters accept configured channels and reject unconfigured channels.
- Parser handles common BTO/STC/trim/average-down formats.
- SQLite persists messages, parsed alerts, bars, snapshots, drift events, and exports.
- Drift triggers on amount threshold, percent threshold, either threshold, and no-market-data cases.
- Export paths include timestamp and channel identity.
- Recorder APIs mask Discord tokens.
- Replay event stream emits chronological alert and market snapshot events.

Frontend tests:

- Settings payload masks token after save.
- Channel editor can add multiple channel IDs.
- Alert inbox shows parse status and drift status.
- Export controls request channel-aware filenames.

## Security And Safety

The feature is recorder-only. It never calls broker APIs and never calls the existing simulation handoff execution path from Discord messages.

Token handling rules:

- Never log token values.
- Never export token values.
- Mask token values in API responses.
- Prefer environment variable token loading for unattended launches.
- If token is stored in SQLite, use local encryption when available.

## Success Criteria

- The Engine can connect to a Discord bot account and record messages from multiple configured channels.
- The Engine can import historical Discord alert CSV and option/stock price CSV files.
- Alerts and price snapshots are stored in SQLite and survive restarts.
- Export files are timestamped and channel-aware.
- Drift flags show whether alert price differed materially from observed market price.
- Sentinel Echo or another bot can consume exported/replay data without the Engine making trade decisions.
