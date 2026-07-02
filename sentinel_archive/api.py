from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .contracts import pulse_handoff_contract_document
from .bot_event_bus_api import create_bot_event_bus_router
from .chrome_bridge_api import create_chrome_bridge_router
from .core import SentinelArchive
from .csv_import import parse_ohlcv_csv
from .discord_recorder import DiscordRecorder
from .models import SimulationConfig
from .recorder_api import create_recorder_router
from .recording_store import RecordingStore

DEFAULT_API_KEY = "local-sim-key"


class CsvImportRequest(BaseModel):
    name: str = Field(default="Recorded market day", min_length=1, max_length=120)
    csv_text: str = Field(min_length=1)


class StartReplayRequest(BaseModel):
    speed: float = Field(default=1.0, ge=0.01, le=1000.0)
    loop: bool = False


class BotControlRequest(BaseModel):
    enable_all: bool = True
    disable_all: bool = True


def require_api_key(x_api_key: str | None = Header(None), authorization: str | None = Header(None)) -> bool:
    provided = x_api_key or ""
    if not provided and authorization:
        provided = authorization.removeprefix("Bearer ").strip()
    if provided != DEFAULT_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return True


def create_app(
    engine: SentinelArchive | None = None,
    recorder_db_path: str | Path = "data/sentinel_archive.sqlite3",
    recorder_export_root: str | Path = "data/recordings",
) -> FastAPI:
    engine_instance = engine or SentinelArchive()
    recorder_store = RecordingStore(recorder_db_path)
    discord_recorder = DiscordRecorder(recorder_store)

    async def playback_loop() -> None:
        while True:
            sim = engine_instance
            if sim.replay.active:
                sim.step()
                await asyncio.sleep(max(0.05, 1.0 / max(sim.replay.speed, 0.01)))
            else:
                await asyncio.sleep(0.25)

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        await recorder_store.initialize()
        app_instance.state.playback_task = asyncio.create_task(playback_loop())
        try:
            yield
        finally:
            await discord_recorder.stop()
            task = app_instance.state.playback_task
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="Sentinel Archive", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine_instance
    app.state.recorder_store = recorder_store
    app.state.discord_recorder = discord_recorder
    app.state.playback_task = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(create_recorder_router(recorder_store, discord_recorder, export_root=recorder_export_root), prefix="/api")
    app.include_router(create_bot_event_bus_router(), prefix="/api")
    app.include_router(create_chrome_bridge_router(recorder_store, discord_recorder), prefix="/api")

    def current_engine() -> SentinelArchive:
        return app.state.engine

    @app.get("/api/health")
    async def health(sim: SentinelArchive = Depends(current_engine)):
        snapshot = sim.snapshot()
        return {
            "status": "online",
            "service": "sentinel-archive",
            "mode": "simulation",
            "execution": "none",
            "running": snapshot.replay.active,
            "bot_running": sim.bot_running,
            "bot_paused": sim.bot_paused,
            "market_open": True,
            "replay": snapshot.replay.model_dump(mode="json"),
            "symbols": sorted(snapshot.current_prices),
            "yfinance": False,
        }

    @app.get("/api/live")
    async def live():
        return {
            "status": "live",
            "service": "sentinel-archive",
            "mode": "simulation",
            "execution": "none",
            "consumer_notes": "live means the Sentinel Archive HTTP service is reachable; it never means live trading is enabled",
        }

    @app.get("/api/ready")
    async def ready():
        return {
            "ready": True,
            "status": "ready",
            "mode": "simulation",
            "execution": "none",
            "failing_checks": [],
            "failing_check_details": [],
        }

    @app.get("/api/simulation/config")
    async def get_config(sim: SentinelArchive = Depends(current_engine)):
        return sim.config

    @app.put("/api/simulation/config")
    async def put_config(config: SimulationConfig, sim: SentinelArchive = Depends(current_engine)):
        return sim.update_config(config)

    @app.post("/api/simulation/reset")
    async def reset(config: SimulationConfig | None = None, sim: SentinelArchive = Depends(current_engine)):
        return sim.reset(config)

    @app.get("/api/simulation/state")
    async def state(sim: SentinelArchive = Depends(current_engine)):
        return sim.snapshot()

    @app.post("/api/simulation/replay/import/csv")
    async def import_csv(body: CsvImportRequest, sim: SentinelArchive = Depends(current_engine)):
        try:
            bars = parse_ohlcv_csv(body.csv_text)
            session = sim.import_bars(body.name, bars)
            return {"ok": True, "session": session}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/simulation/replay/sessions")
    async def list_sessions(sim: SentinelArchive = Depends(current_engine)):
        return {"sessions": list(sim.sessions.values())}

    @app.get("/api/simulation/replay/sessions/{session_id}")
    async def get_session(session_id: str, sim: SentinelArchive = Depends(current_engine)):
        if session_id not in sim.sessions:
            raise HTTPException(404, f"Replay session '{session_id}' not found")
        return {"session": sim.sessions[session_id], "bars": sim.bars[session_id]}

    @app.post("/api/simulation/replay/sessions/{session_id}/start")
    async def start_replay(session_id: str, body: StartReplayRequest, sim: SentinelArchive = Depends(current_engine)):
        try:
            return sim.start_replay(session_id, speed=body.speed, loop=body.loop)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/simulation/replay/step")
    async def step_replay(sim: SentinelArchive = Depends(current_engine)):
        return sim.step()

    @app.post("/api/simulation/replay/stop")
    async def stop_replay(sim: SentinelArchive = Depends(current_engine)):
        return sim.stop_replay()

    @app.post("/api/simulation/handoff")
    async def native_handoff(payload: dict[str, Any], sim: SentinelArchive = Depends(current_engine)):
        return sim.process_handoff(payload)

    @app.post("/api/bot/start", dependencies=[Depends(require_api_key)])
    async def start_bot(body: BotControlRequest | None = None, sim: SentinelArchive = Depends(current_engine)):
        settings = body or BotControlRequest()
        if settings.enable_all:
            for ticker in sim.tickers.values():
                ticker.enabled = True
                ticker.auto_stop_reason = None
        sim.bot_running = True
        sim.bot_paused = False
        tickers = [ticker.model_dump(mode="json") for ticker in sim.tickers.values()]
        return {
            "running": True,
            "paused": False,
            "tickers": tickers,
            "mode": "simulation",
            "execution": "none",
        }

    @app.post("/api/bot/stop", dependencies=[Depends(require_api_key)])
    async def stop_bot(body: BotControlRequest | None = None, sim: SentinelArchive = Depends(current_engine)):
        settings = body or BotControlRequest()
        if settings.disable_all:
            for ticker in sim.tickers.values():
                ticker.enabled = False
        sim.bot_running = False
        sim.bot_paused = False
        tickers = [ticker.model_dump(mode="json") for ticker in sim.tickers.values()]
        return {
            "running": False,
            "paused": False,
            "tickers": tickers,
            "mode": "simulation",
            "execution": "none",
        }

    @app.get("/api/automation")
    async def automation(sim: SentinelArchive = Depends(current_engine)):
        return {
            "settings": {
                "enabled": True,
                "mode": "paper",
                "min_confidence": sim.config.reject_below_confidence,
                "per_ticker_enabled": {symbol: ticker.enabled for symbol, ticker in sim.tickers.items()},
            },
            "last_handoff": sim.last_handoff,
        }

    @app.get("/api/decisions")
    async def decisions(sim: SentinelArchive = Depends(current_engine)):
        return {"decisions": sim.decisions}

    @app.get("/api/pulse/handoff/schema")
    async def handoff_schema():
        return pulse_handoff_contract_document()

    @app.get("/api/pulse/account")
    async def edge_pulse_account(sim: SentinelArchive = Depends(current_engine)):
        return sim.account_status()

    @app.get("/api/pulse/positions")
    async def edge_pulse_positions(sim: SentinelArchive = Depends(current_engine)):
        return {"positions": list(sim.account_status()["positions"])}

    @app.get("/api/price/{symbol}")
    async def price(symbol: str, sim: SentinelArchive = Depends(current_engine)):
        normalized = symbol.upper()
        if normalized not in sim.current_prices:
            raise HTTPException(404, "price unavailable")
        return {"symbol": normalized, "price": sim.current_prices[normalized]}

    @app.get("/api/quote/{symbol}")
    async def quote(symbol: str, sim: SentinelArchive = Depends(current_engine)):
        normalized = symbol.upper()
        if normalized not in sim.current_prices:
            raise HTTPException(404, "quote unavailable")
        price_value = sim.current_prices[normalized]
        return {"symbol": normalized, "price": price_value, "last": price_value, "source": "simulation_replay"}

    @app.get("/api/edge/status", dependencies=[Depends(require_api_key)])
    async def pulse_edge_status(sim: SentinelArchive = Depends(current_engine)):
        return {
            "api_key_configured": True,
            "signals_cached": len(sim.decisions),
            "max_retry_attempts": 0,
            "timestamp": sim.replay.current_timestamp,
            "mongo": {"status": "simulated", "connected": True},
            "global_market_handoff": True,
        }

    @app.get("/api/edge/account/status", dependencies=[Depends(require_api_key)])
    async def pulse_account_status(sim: SentinelArchive = Depends(current_engine)):
        return sim.account_status()

    @app.get("/api/edge/tickers", dependencies=[Depends(require_api_key)])
    async def pulse_tickers(sim: SentinelArchive = Depends(current_engine)):
        return [ticker.model_dump(mode="json") for ticker in sim.tickers.values()]

    @app.get("/api/edge/positions/{symbol}", dependencies=[Depends(require_api_key)])
    async def pulse_position(symbol: str, sim: SentinelArchive = Depends(current_engine)):
        normalized = symbol.upper()
        position = sim.account.positions.get(normalized)
        if not position:
            return {"symbol": normalized, "has_position": False}
        return {"has_position": True, **position.model_dump(mode="json")}

    @app.post("/api/edge/handoff", status_code=202, dependencies=[Depends(require_api_key)])
    async def pulse_handoff(payload: dict[str, Any], response: Response, sim: SentinelArchive = Depends(current_engine)):
        result = sim.process_handoff(payload)
        if result.get("status") == "failed":
            response.status_code = 400
        elif result.get("status") == "rejected":
            response.status_code = 409
        return result

    @app.post("/api/edge/tickers/{symbol}/decision", dependencies=[Depends(require_api_key)])
    async def legacy_decision(symbol: str, payload: dict[str, Any], sim: SentinelArchive = Depends(current_engine)):
        action = str(payload.get("decision") or payload.get("action") or "hold")
        mapped = {
            "enable_trailing_stop": "trailing_stop",
            "trailing": "trailing_stop",
            "emergency_stop": "emergency_exit",
            "stop": "regular_stop",
        }.get(action, action)
        handoff = {
            "contract_version": "edge.pulse.handoff.v1",
            "symbol": symbol,
            "action": mapped,
            "confidence": float(payload.get("confidence", 1.0)),
            "reason": str(payload.get("reason", "legacy decision")),
            "mode": str(payload.get("mode", "paper")),
            "orb_session": "market_open",
            "stop_type": payload.get("stop_type") or ("trailing" if mapped == "trailing_stop" else "regular" if mapped == "regular_stop" else None),
            "trailing_percent": payload.get("trailing_percent"),
            "idempotency_key": f"edge:{symbol.upper()}:{mapped}:market_open:{int(time.time() // 60)}:legacy",
            "source": "sentinel_edge",
            "created_at": time.time(),
            "metadata": {"price": payload.get("price")},
        }
        return sim.process_handoff({key: value for key, value in handoff.items() if value is not None})

    @app.post("/api/edge/tickers/{symbol}/trailing", dependencies=[Depends(require_api_key)])
    async def enable_trailing(symbol: str, payload: dict[str, Any], sim: SentinelArchive = Depends(current_engine)):
        percent = float(payload.get("trailing_percent", sim.config.default_trailing_percent))
        handoff = {
            "contract_version": "edge.pulse.handoff.v1",
            "symbol": symbol,
            "action": "trailing_stop",
            "confidence": 1.0,
            "reason": "legacy trailing endpoint",
            "mode": "paper",
            "orb_session": "market_open",
            "stop_type": "trailing",
            "trailing_percent": percent,
            "idempotency_key": f"edge:{symbol.upper()}:trailing_stop:market_open:{int(time.time() // 60)}:legacy",
            "source": "sentinel_edge",
            "created_at": time.time(),
            "metadata": {},
        }
        return sim.process_handoff(handoff)

    @app.post("/api/edge/signals/evaluate", dependencies=[Depends(require_api_key)])
    async def evaluate_signal(payload: dict[str, Any]):
        symbol = str(payload.get("symbol", "")).upper()
        price_change = float(payload.get("price_change_pct", 0) or 0)
        volume = float(payload.get("volume", 0) or 0)
        atr = float(payload.get("atr", 0) or 0)
        strength = max(-10.0, min(10.0, price_change * 2 + (1 if volume > 0 else 0) + min(atr, 3)))
        direction = "bullish" if strength > 1 else "bearish" if strength < -1 else "neutral"
        return {
            "symbol": symbol,
            "direction": direction,
            "strength": strength,
            "volume_ratio": 1.0 if volume else 0.0,
            "volume_zscore": 0.0,
            "observation_applied": bool(payload.get("observation")),
        }

    dist_dir = Path.cwd() / "dist"
    index_file = dist_dir / "index.html"
    if index_file.exists():
        app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(index_file)

        @app.get("/{path:path}")
        async def spa_fallback(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "API route not found")
            return FileResponse(index_file)

    return app
