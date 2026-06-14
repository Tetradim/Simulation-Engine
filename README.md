# Sentinel Simulation Engine

Standalone market replay and Edge/Pulse contract simulation for the Sentinel trading suite.

The engine can run by itself, stand in for Pulse, stand in for Edge, or serve both contracts at once. Tandem Suite can point both bot URLs at this service to show real changing state from a replayed market day.

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

## Tandem Suite Integration

Set Tandem Suite to use the simulation engine for both bot URLs:

```powershell
EDGE_API_URL=http://127.0.0.1:9200
PULSE_API_URL=http://127.0.0.1:9200
PULSE_EDGE_API_KEY=local-sim-key
```

## CSV Format

Import recorded market-day bars with these required columns:

```csv
timestamp,symbol,open,high,low,close,volume
2026-06-09T13:30:00Z,SPY,540.10,541.00,539.80,540.75,1200
```

Optional columns: `vwap`, `trade_count`, `source`.

## Main API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/simulation/state` | Full engine snapshot |
| PUT | `/api/simulation/config` | Replace execution model settings |
| POST | `/api/simulation/replay/import/csv` | Import OHLCV bars |
| GET | `/api/simulation/replay/sessions` | List imported sessions |
| POST | `/api/simulation/replay/sessions/{session_id}/start` | Start replay |
| POST | `/api/simulation/replay/step` | Advance one timestamp batch |
| POST | `/api/simulation/replay/stop` | Stop replay |
| POST | `/api/simulation/handoff` | Send a native handoff payload |

## Edge-Compatible Endpoints

- `GET /api/live`
- `GET /api/ready`
- `GET /api/automation`
- `GET /api/decisions`
- `GET /api/pulse/handoff/schema`
- `GET /api/pulse/account`
- `GET /api/pulse/positions`
- `GET /api/price/{symbol}`
- `GET /api/quote/{symbol}`

## Pulse-Compatible Edge Endpoints

These require `X-API-Key: local-sim-key`.

- `GET /api/edge/status`
- `GET /api/edge/account/status`
- `GET /api/edge/tickers`
- `GET /api/edge/positions/{symbol}`
- `POST /api/edge/handoff`
- `POST /api/edge/tickers/{symbol}/decision`
- `POST /api/edge/tickers/{symbol}/trailing`
- `POST /api/edge/signals/evaluate`

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest
npm run build
```
