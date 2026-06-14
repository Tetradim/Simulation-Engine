# Simulation Engine Design

## Goal

Build a standalone Sentinel Simulation Engine that can replay recorded market-day price action, emulate the Edge/Pulse HTTP contracts, and let users tune execution, replay, account, risk, and signal settings from a web control panel.

## Architecture

The first release is a single FastAPI service with an embedded deterministic simulation core and a React/Vite control panel. The same service can be used as an Edge-compatible API, a Pulse-compatible API, or both at once. Tandem Suite can point `EDGE_API_URL` and `PULSE_API_URL` to this engine to show a live tandem dashboard without changing Tandem code.

## Core Capabilities

- Import user-provided OHLCV bars from CSV text or file upload.
- Replay bars manually or with a configurable playback speed and loop setting.
- Maintain simulated account cash, equity, buying power, positions, orders, decisions, event log, and tickers.
- Apply configurable execution assumptions: starting cash, default quantity, max allocation, slippage, commission, fill ratio, latency, regular stop, trailing stop, take profit, and reject-below-confidence gate.
- Accept Edge structured handoffs at `/api/edge/handoff` and mutate simulated Pulse broker state.
- Expose Tandem-facing Edge endpoints: `/api/live`, `/api/ready`, `/api/automation`, `/api/decisions`, `/api/pulse/handoff/schema`, `/api/pulse/account`, `/api/pulse/positions`.
- Expose Pulse-facing Edge integration endpoints: `/api/edge/status`, `/api/edge/account/status`, `/api/edge/tickers`, `/api/edge/positions/{symbol}`, `/api/edge/tickers/{symbol}/decision`, `/api/edge/tickers/{symbol}/trailing`, `/api/edge/signals/evaluate`.
- Work without live brokers and without seeded market data. Empty states should be explicit until users import bars or send handoffs.

## User Customization

Users can configure account assumptions, execution realism, replay speed, replay loop, symbols, Edge signal behavior, Pulse fill behavior, and risk exits. The web panel exposes these as form inputs and sends them to `/api/simulation/config`.

## Error Handling

Invalid CSV imports return a 400 with a row-specific message. Handoffs with invalid contract fields return a rejected/failed response. Duplicate handoffs are idempotent. Missing prices reject buy orders with `price_unavailable`.

## Testing

Backend unit tests cover replay stepping, execution math, handoff mutations, idempotency, trailing stop behavior, and contract API responses. Frontend verification is build-based in this first release.
