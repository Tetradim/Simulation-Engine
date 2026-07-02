# Replay Workbench UI Replacement Design

## Goal

Replace the existing Sentinel Archive control panel with a chart-first Replay Workbench for testing recorded market-day price action against the Sentinel bot suite.

The UI should make recorded replay feel like an operator cockpit: select a market session, inspect price action, replay forward with media-style controls, watch simulated handoffs and bot outputs, and review orders, fills, risk events, drawdown, and exports from one workspace.

The first implementation is an in-place frontend replacement. The current FastAPI API, local launcher behavior, and safety boundary remain intact.

## Approved Direction

The approved layout is Option A, "Replay Workbench":

- Left rail: replay sessions, scenario library, imports, exports, and replay setup.
- Center: primary price-action chart with candle/line modes, indicators, volume, heatmap layer, buy/sell markers, and synchronized replay controls.
- Right rail: bot contract lanes for Sentinel Pulse, Sentinel Edge, Sentinel Echo, Sentinel-Chain, and Sentinel-Flare.
- Bottom band: results snapshot, fill-model confidence, synchronized event tape, and drill-in report surfaces.

The visual style starts with dark metallic red/gold/silver framing from the provided `Desktop/Pics` references and dense trading-dashboard structure from `Desktop/Dark`. Color, background, opacity, and glass-card treatments are intentionally deferred to a visual-polish pass after the workflow is implemented and verified.

## Sources And Research Constraints

The design follows established replay/backtest patterns:

- TradingView Bar Replay and NinjaTrader Playback both support a media-control mental model: choose historical data, set a start point, play/pause, step, and control speed.
- Backtrader's replay model reinforces that replayed bars have a data-resolution contract. The UI should label whether a session uses OHLCV bars, ticks, L1 quotes, or L2/depth data.
- QuantConnect's slippage, fee, and backtest report documentation reinforces making execution realism explicit: slippage, fees, fills, drawdown, orders, trades, and reports must not be hidden behind a single profit number.

Research-derived UI requirements:

- Replay controls must be prominent and always visible in the Replay tab.
- Fill assumptions must be visible whenever reviewing trades or bot outcomes.
- Report surfaces must separate equity, drawdown, orders, fills, trade list, logs, and exports.
- The UI must not imply tick-accurate fills when the data source is only OHLCV bars.

## Scope

The first replacement UI includes:

- Replay session selection from current `/api/simulation/state` sessions.
- CSV market-day import using the existing import endpoint.
- Replay start, stop, step, loop, and speed controls using existing endpoints.
- Current account metrics: equity, cash, buying power, open positions, day P/L, and current prices.
- Execution model controls for starting cash, default quantity, allocation cap, fill ratio, slippage, commission, confidence threshold, stop, trailing stop, and take-profit assumptions.
- Handoff composer for manual bot-contract tests.
- Positions table with current price, average entry, quantity, P/L, trailing state, and stop context.
- Event tape combining decisions, simulated fills, replay state, recorder alerts, and drift events where available.
- Discord recorder controls, parser preview, imports, exports, and Sentinel Echo replay/test-run generation.
- Bot lanes and drawers for the target bot workflows.
- Empty, loading, degraded, and API-error states for every lane.

## Non-Goals

- No broker connections.
- No live orders.
- No live trading enablement.
- No replacement of bot-specific execution logic inside Pulse, Edge, Sentinel Echo, Sentinel-Chain, or Sentinel-Flare.
- No backend rewrite unless a frontend requirement exposes missing data that cannot be derived safely from existing endpoints.
- No pixel-perfect visual-theme finalization in the first implementation pass.

## Primary Tabs

### Replay

The default first screen. It contains:

- Session rail with imported sessions and scenario labels.
- Market chart with price, volume, heatmap, trade markers, replay cursor, and selected symbol.
- Playback controls: jump to start, step, play/pause, stop, speed, and loop.
- Top metrics: equity, replay progress, open risk, fill confidence, and bot readiness.
- Right-side bot lanes showing the latest status and outcome per target bot.
- Bottom event tape synchronized to replay time.

### Scenarios

Scenario library for naming and reusing recorded-day test cases:

- Opening bell reversal.
- Trailing-stop chop.
- Never re-enters range.
- Discord alert drift.
- Crypto stop/take-profit path.
- Darkpool confluence/manual-review case.

The first version can store these labels in UI state or derive them from session metadata if available. Persistence can follow once the backend has a stable scenario model.

### Results

Results drill-in area:

- Equity curve.
- Drawdown curve.
- Orders and fills.
- Trade list.
- Position timeline.
- Bot outcome summary.
- Replay event log.
- Exported report metadata.

The Results tab should be report-first and not compete with the Replay tab for operational controls.

### Recorder

Recorder operations:

- Discord settings and connection diagnostics.
- Recording session start/stop.
- Parser preview.
- Discord alert CSV import.
- Stock and option price CSV imports.
- Drift event review.
- Export alerts or joined datasets.
- Sentinel Echo replay URL/test-run creation.

### Bot Contracts

Bot-specific contract review:

- Sentinel Pulse: bracket order assumptions, simulated accepted/rejected handoffs, positions, stop/trailing/take-profit behavior, and partial fill assumptions.
- Sentinel Edge: readiness, handoff gating, confidence, cooldown, suppression, and decision feed feedback.
- Sentinel Echo: Discord alert parser result, source-policy preview, replay event compatibility, and no-mutation preview status.
- Sentinel-Chain: alert normalization, risk checks, paper order state, bracket lot stops/take-profits, and symbol/exchange context.
- Sentinel-Flare: confluence/intent packet preview, manual-review status, risk envelope, and reasons a Pulse packet is withheld.

### Settings

Execution and interface settings:

- Execution assumptions.
- Fill model.
- Recorder thresholds.
- API status diagnostics.
- Theme controls later: background set, glass opacity, card transparency, metallic accent strength, and chart contrast.

## Component Architecture

The existing `frontend/src/App.tsx` should be split into focused components during replacement:

- `App`: owns polling, error boundary state, top-level tab selection, and cross-tab data.
- `ReplayWorkbench`: shell for the Replay tab.
- `SessionRail`: replay session selection, imports, scenario labels, and export actions.
- `ReplayChart`: chart composition, overlays, trade markers, volume, heatmap, and replay cursor.
- `PlaybackControls`: start, stop, step, speed, loop, and progress.
- `MetricStrip`: account, replay, risk, and bot-readiness metrics.
- `BotLanes`: compact bot status lanes and drawer triggers.
- `BotLaneDrawer`: expanded per-bot contract details.
- `EventTape`: synchronized decisions, fills, recorder alerts, drift, and errors.
- `ResultsPanel`: equity, drawdown, orders, fills, trades, and report summaries.
- `RecorderWorkspace`: Discord recorder and import/export workflows.
- `ExecutionSettings`: execution and fill-model assumptions.
- `StatusBadge`, `Panel`, `Field`, `NumberField`, and table primitives shared across tabs.

The first implementation can keep components in `frontend/src/App.tsx` if needed for speed, but the target design should move them into separate files once the UI stabilizes.

## Data Flow

The UI continues to poll the existing API:

1. `api.state()` provides replay sessions, active replay state, current prices, config, account, tickers, decisions, and event log.
2. Recorder endpoints provide settings, status, parsed alerts, drift events, and exports.
3. User imports CSV market data through the existing replay import endpoint.
4. User starts or steps replay through existing replay endpoints.
5. The backend advances replay state and returns updated account, prices, decisions, positions, and events.
6. The UI derives bot-lane statuses from snapshot fields and recorder data.
7. Sentinel Echo replay/test-run actions continue to call existing recorder API endpoints.

No browser route should call broker-capable endpoints. The Sentinel Archive remains a local paper/simulation surface.

## Fill Confidence Model

Every run should display a fill-confidence label:

- High: tick or order-book replay with explicit fill model.
- Medium: OHLCV bars with slippage, commission, high/low stop checks, and documented assumptions.
- Low: sparse bars, missing volume, missing timestamps, or symbols falling through to a non-replay price source.

The current Sentinel Archive primarily imports OHLCV CSV rows, so the default label should be Medium when rows include valid open, high, low, close, volume, and timestamps. If volume or high/low is missing, downgrade confidence and show the reason.

## Visual Direction

Initial visual direction:

- Dark operator cockpit.
- Dense but legible chart-first layout.
- Metallic red/gold/silver accents from the supplied `Desktop/Pics` reference folder.
- Chart/data panels should use calm dark backgrounds with enough contrast for gridlines, candles, markers, heatmaps, and tables.
- Decorative references should be used as top-bar, rail, drawer, or tab framing, not as busy backgrounds behind chart data.

Deferred visual-polish controls:

- Glass panel opacity.
- Background image selection.
- Metallic border intensity.
- Red/gold/silver accent variants.
- Chart contrast mode.
- Reduced-effects mode.

## Accessibility And Usability

- Keep controls keyboard reachable.
- Do not rely on color alone for buy/sell/risk status.
- Use text labels on critical replay and safety states.
- Ensure buttons and inputs have stable dimensions so layout does not shift during polling.
- Keep chart overlays togglable to avoid visual overload.
- Keep all financial actions labeled as simulated/paper.
- Show per-panel errors instead of replacing the entire dashboard when one endpoint fails.

## Error States

Required states:

- Backend unavailable: top-level API error with retry.
- No replay sessions: empty state with import action.
- Replay active but no current price for selected symbol: chart warning and symbol fallback.
- Recorder not configured: settings prompt, not an error.
- Discord token/channel failure: diagnostic result with masked token behavior.
- Sentinel Echo replay empty: event-count zero state with filter guidance.
- Bot lane unavailable: per-lane degraded badge and last known detail.

## Testing Plan

Automated:

- `npm run build` for the React/Vite frontend.
- Existing Python test suite subset for API contracts touched by any frontend-required backend changes.
- TypeScript checks through the Vite build.

Browser verification:

- Load the app through the existing launcher or dev server.
- Verify Replay tab renders with empty state before import.
- Import a small OHLCV CSV and verify session appears.
- Start, step, stop, speed, and loop controls call expected endpoints.
- Verify positions, event tape, recorder alerts, and drift sections render available data.
- Verify no text overlaps at desktop and narrower viewport widths.
- Verify chart panel is nonblank and remains readable with heatmap/marker overlays.

Manual review:

- Confirm paper/simulation labels are visible.
- Confirm fill-confidence label matches session data quality.
- Confirm bot lanes communicate withheld/rejected/manual-review states without implying live orders.

## Open Decisions

- Which chart implementation to use in the first build: SVG/custom canvas, Recharts, Plotly, lightweight-charts, or a local custom renderer.
- Whether scenario labels are frontend-only in the first pass or require a backend persistence model.
- Whether theme controls ship in the first replacement or after core replay workflows pass verification.

Current recommendation:

- Use a pragmatic chart renderer that can be implemented and verified quickly in the current React/Vite stack.
- Keep scenario labeling lightweight in the first pass.
- Ship core replay workflow first, then tune glass panels, opacity, colors, and backgrounds as a second visual iteration.

## Transition Plan

1. Preserve the current FastAPI API and launcher.
2. Replace the current React control panel in place.
3. Keep API functions in `frontend/src/api.ts`, extending only if the UI needs already-supported backend data.
4. Remove the old basic dashboard from the production route.
5. Build and browser-test the replacement.
6. Tune visual theme after workflow validation.

## Spec Review Notes

No requirements in this spec authorize live trading, broker connections, or order placement. The design is scoped to local replay, paper simulation, parser/contract previews, and report generation.
