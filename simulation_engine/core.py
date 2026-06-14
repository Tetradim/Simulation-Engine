from __future__ import annotations

import hashlib
import time
from typing import Any

from .models import (
    AccountState,
    MarketBar,
    Position,
    PulseHandoffRequest,
    ReplaySession,
    ReplayState,
    SimulationConfig,
    SimulationSnapshot,
    TickerState,
)


class SimulationEngine:
    def __init__(self, config: SimulationConfig | None = None):
        self.config = config or SimulationConfig()
        self.sessions: dict[str, ReplaySession] = {}
        self.bars: dict[str, list[MarketBar]] = {}
        self.replay = ReplayState()
        self.current_prices: dict[str, float] = {}
        self.tickers: dict[str, TickerState] = {}
        self.decisions: list[dict[str, Any]] = []
        self.event_log: list[dict[str, Any]] = []
        self.idempotency: dict[str, dict[str, Any]] = {}
        self.last_handoff: dict[str, Any] | None = None
        self.account = self._new_account()

    def _new_account(self) -> AccountState:
        cash = round(self.config.starting_cash, 6)
        return AccountState(
            starting_cash=cash,
            cash=cash,
            total_equity=cash,
            account_balance=cash,
            buying_power=cash,
            available=cash,
            day_pnl_dollar=0.0,
            day_pnl_pct=0.0,
            open_positions=0,
            positions={},
        )

    def reset(self, config: SimulationConfig | None = None) -> SimulationSnapshot:
        if config is not None:
            self.config = config
        self.replay = ReplayState()
        self.current_prices = {}
        self.tickers = {}
        self.decisions = []
        self.event_log = []
        self.idempotency = {}
        self.last_handoff = None
        self.account = self._new_account()
        return self.snapshot()

    def update_config(self, config: SimulationConfig) -> SimulationSnapshot:
        self.config = config
        self.account.starting_cash = config.starting_cash
        if not self.account.positions and self.account.cash == self.account.account_balance:
            self.account.cash = config.starting_cash
        self._mark_to_market()
        return self.snapshot()

    def import_bars(self, name: str, bars: list[MarketBar], source: str = "user_csv") -> ReplaySession:
        if not bars:
            raise ValueError("at least one market bar is required")
        ordered = sorted(bars, key=lambda item: (item.timestamp, item.symbol))
        symbols = sorted({bar.symbol for bar in ordered})
        fingerprint = hashlib.sha256(
            "|".join([name, ordered[0].timestamp, ordered[-1].timestamp, ",".join(symbols), str(len(ordered))]).encode()
        ).hexdigest()[:12]
        session_id = f"replay-{fingerprint}"
        session = ReplaySession(
            session_id=session_id,
            name=name,
            source=source,
            symbols=symbols,
            bar_count=len(ordered),
            first_timestamp=ordered[0].timestamp,
            last_timestamp=ordered[-1].timestamp,
        )
        self.sessions[session_id] = session
        self.bars[session_id] = ordered
        for symbol in symbols:
            self.tickers.setdefault(symbol, TickerState(symbol=symbol))
        self._log("replay_imported", {"session_id": session_id, "symbols": symbols, "bar_count": len(ordered)})
        return session

    def start_replay(self, session_id: str, speed: float = 1.0, loop: bool = False) -> SimulationSnapshot:
        if session_id not in self.sessions:
            raise ValueError(f"replay session '{session_id}' was not found")
        self.replay = ReplayState(active=True, session_id=session_id, speed=speed, loop=loop, index=0)
        self._log("replay_started", {"session_id": session_id, "speed": speed, "loop": loop})
        return self.snapshot()

    def stop_replay(self) -> SimulationSnapshot:
        self.replay.active = False
        self._log("replay_stopped", {"session_id": self.replay.session_id})
        return self.snapshot()

    def step(self) -> SimulationSnapshot:
        if not self.replay.active or not self.replay.session_id:
            return self.snapshot()

        rows = self.bars[self.replay.session_id]
        if self.replay.index >= len(rows):
            if self.replay.loop:
                self.replay.index = 0
            else:
                self.replay.active = False
                return self.snapshot()

        timestamp = rows[self.replay.index].timestamp
        batch: list[MarketBar] = []
        while self.replay.index < len(rows) and rows[self.replay.index].timestamp == timestamp:
            batch.append(rows[self.replay.index])
            self.replay.index += 1

        self.replay.current_timestamp = timestamp
        for market_bar in batch:
            self.current_prices[market_bar.symbol] = market_bar.close
            self.tickers.setdefault(market_bar.symbol, TickerState(symbol=market_bar.symbol))
            self._apply_bar_to_position(market_bar)
        self._mark_to_market()
        return self.snapshot()

    def process_handoff(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            handoff = PulseHandoffRequest(**payload)
        except Exception as exc:
            return {
                "accepted": False,
                "status": "failed",
                "reason": "invalid_handoff_contract",
                "message": str(exc),
            }

        if handoff.idempotency_key in self.idempotency:
            previous = dict(self.idempotency[handoff.idempotency_key])
            previous["reason"] = "duplicate"
            return previous

        if handoff.confidence < self.config.reject_below_confidence:
            return self._record_handoff(handoff, False, "rejected", "confidence_below_threshold")

        action = handoff.action
        if action in {"buy", "dca"}:
            response = self._buy(handoff)
        elif action in {"sell", "regular_stop"}:
            response = self._sell(handoff, action)
        elif action in {"trailing_stop", "opening_trailing_stop", "tighten_trailing_stop"}:
            response = self._enable_trailing(handoff)
        elif action in {"stop_all", "emergency_exit"}:
            response = self._sell_all(handoff)
        elif action == "stop_buying":
            self.tickers.setdefault(handoff.symbol, TickerState(symbol=handoff.symbol)).enabled = False
            response = self._record_handoff(handoff, True, "accepted", "pulse_accepted")
        elif action == "tighten_stop":
            response = self._record_handoff(handoff, True, "accepted", "pulse_accepted")
        else:
            response = self._record_handoff(handoff, False, "rejected", "unsupported_action")

        self.idempotency[handoff.idempotency_key] = dict(response)
        self.last_handoff = {**handoff.model_dump(mode="json"), "handoff_status": response["status"], "pulse_feedback": response}
        return response

    def _buy(self, handoff: PulseHandoffRequest) -> dict[str, Any]:
        symbol = handoff.symbol
        price = self._price_for(symbol, handoff)
        if price is None:
            return self._record_handoff(handoff, False, "rejected", "price_unavailable")

        quantity = float(handoff.metadata.get("quantity") or self.config.default_quantity) * self.config.fill_ratio
        if quantity <= 0:
            return self._record_handoff(handoff, False, "rejected", "zero_fill_quantity")

        fill_price = self._slipped_price(price, "buy")
        total_cost = round(fill_price * quantity + self.config.commission_per_order, 6)
        max_allocation = self.account.total_equity * (self.config.max_allocation_pct / 100.0)
        if total_cost > self.account.cash or total_cost > max_allocation:
            return self._record_handoff(handoff, False, "rejected", "risk_limit")

        existing = self.account.positions.get(symbol)
        if existing:
            combined_qty = existing.quantity + quantity
            avg_entry = ((existing.avg_entry * existing.quantity) + (fill_price * quantity)) / combined_qty
            existing.quantity = round(combined_qty, 6)
            existing.avg_entry = round(avg_entry, 6)
            existing.entry_price = existing.avg_entry
            existing.current_price = price
            existing.market_price = price
        else:
            self.account.positions[symbol] = Position(
                symbol=symbol,
                quantity=round(quantity, 6),
                entry_price=round(fill_price, 6),
                avg_entry=round(fill_price, 6),
                current_price=price,
                market_price=price,
                high_water_mark=price,
            )
        self.account.cash = round(self.account.cash - total_cost, 6)
        self.tickers.setdefault(symbol, TickerState(symbol=symbol))
        self._decision("buy", symbol, price, handoff, {"fill_price": fill_price, "quantity": quantity})
        self._mark_to_market()
        return self._record_handoff(handoff, True, "accepted", "pulse_accepted")

    def _sell(self, handoff: PulseHandoffRequest, action: str) -> dict[str, Any]:
        symbol = handoff.symbol
        position = self.account.positions.get(symbol)
        if not position:
            return self._record_handoff(handoff, False, "rejected", "position_not_found")
        price = self._price_for(symbol, handoff)
        if price is None:
            return self._record_handoff(handoff, False, "rejected", "price_unavailable")
        self._close_position(symbol, price, action)
        return self._record_handoff(handoff, True, "accepted", "pulse_accepted")

    def _sell_all(self, handoff: PulseHandoffRequest) -> dict[str, Any]:
        for symbol in list(self.account.positions):
            price = self.current_prices.get(symbol) or self.account.positions[symbol].current_price
            self._close_position(symbol, price, handoff.action)
        return self._record_handoff(handoff, True, "accepted", "pulse_accepted")

    def _enable_trailing(self, handoff: PulseHandoffRequest) -> dict[str, Any]:
        symbol = handoff.symbol
        percent = float(handoff.trailing_percent or self.config.default_trailing_percent)
        targets = list(self.account.positions) if symbol == "GLOBAL" else [symbol]
        for target in targets:
            ticker = self.tickers.setdefault(target, TickerState(symbol=target))
            ticker.trailing_enabled = True
            ticker.trailing_percent = percent
            position = self.account.positions.get(target)
            if position:
                position.trailing_enabled = True
                position.trailing_percent = percent
                position.high_water_mark = max(position.high_water_mark or position.current_price, position.current_price)
        self._decision(handoff.action, symbol, self.current_prices.get(symbol), handoff, {"trailing_percent": percent})
        self._mark_to_market()
        return self._record_handoff(handoff, True, "accepted", "pulse_accepted")

    def _apply_bar_to_position(self, market_bar: MarketBar) -> None:
        position = self.account.positions.get(market_bar.symbol)
        if not position:
            return
        position.current_price = market_bar.close
        position.market_price = market_bar.close
        position.high_water_mark = max(position.high_water_mark or market_bar.close, market_bar.high)
        self._update_position_pnl(position)

        if position.trailing_enabled and position.trailing_percent:
            floor = round((position.high_water_mark or market_bar.close) * (1 - position.trailing_percent / 100.0), 6)
            if market_bar.low <= floor:
                self._close_position(position.symbol, floor, "trailing_stop_sell")
                return

        if self.config.regular_stop_percent > 0:
            stop_price = position.avg_entry * (1 - self.config.regular_stop_percent / 100.0)
            if market_bar.low <= stop_price:
                self._close_position(position.symbol, round(stop_price, 6), "regular_stop_sell")
                return

        if self.config.take_profit_percent > 0:
            target_price = position.avg_entry * (1 + self.config.take_profit_percent / 100.0)
            if market_bar.high >= target_price:
                self._close_position(position.symbol, round(target_price, 6), "take_profit_sell")

    def _close_position(self, symbol: str, price: float, reason: str) -> None:
        position = self.account.positions.get(symbol)
        if not position:
            return
        fill_price = self._slipped_price(price, "sell")
        proceeds = round(position.quantity * fill_price - self.config.commission_per_order, 6)
        self.account.cash = round(self.account.cash + proceeds, 6)
        self._decision(reason, symbol, price, None, {"fill_price": fill_price, "quantity": position.quantity})
        del self.account.positions[symbol]
        self._mark_to_market()

    def _mark_to_market(self) -> None:
        total_positions = 0.0
        for symbol, position in self.account.positions.items():
            if symbol in self.current_prices:
                position.current_price = self.current_prices[symbol]
                position.market_price = self.current_prices[symbol]
            self._update_position_pnl(position)
            total_positions += position.current_price * position.quantity
        equity = round(self.account.cash + total_positions, 6)
        self.account.total_equity = equity
        self.account.account_balance = equity
        self.account.buying_power = round(self.account.cash, 6)
        self.account.available = round(self.account.cash, 6)
        self.account.day_pnl_dollar = round(equity - self.account.starting_cash, 6)
        self.account.day_pnl_pct = round((self.account.day_pnl_dollar / self.account.starting_cash) * 100, 6) if self.account.starting_cash else 0.0
        self.account.open_positions = len(self.account.positions)

    def _update_position_pnl(self, position: Position) -> None:
        pnl = round((position.current_price - position.avg_entry) * position.quantity, 6)
        pnl_pct = round(((position.current_price - position.avg_entry) / position.avg_entry) * 100, 6) if position.avg_entry else 0.0
        position.pnl = pnl
        position.pnl_pct = pnl_pct
        position.unrealized_pnl = pnl
        position.unrealized_pnl_pct = pnl_pct

    def _price_for(self, symbol: str, handoff: PulseHandoffRequest) -> float | None:
        metadata_price = handoff.metadata.get("price") or handoff.metadata.get("current_price") or handoff.metadata.get("market_price")
        if metadata_price is not None:
            return float(metadata_price)
        if symbol in self.current_prices:
            return self.current_prices[symbol]
        position = self.account.positions.get(symbol)
        if position:
            return position.current_price
        return None

    def _slipped_price(self, price: float, side: str) -> float:
        multiplier = 1 + (self.config.slippage_bps / 10000.0 if side == "buy" else -self.config.slippage_bps / 10000.0)
        return round(price * multiplier, 6)

    def _record_handoff(self, handoff: PulseHandoffRequest, accepted: bool, status: str, reason: str) -> dict[str, Any]:
        response = {
            "accepted": accepted,
            "status": status,
            "reason": reason,
            "handoff_id": handoff.idempotency_key,
            "message": f"{handoff.action} {status}: {reason}",
        }
        self._log("handoff", {"symbol": handoff.symbol, "action": handoff.action, **response})
        return response

    def _decision(self, action: str, symbol: str, price: float | None, handoff: PulseHandoffRequest | None, extra: dict[str, Any]) -> None:
        entry = {
            "timestamp": time.time(),
            "symbol": symbol,
            "action": action,
            "decision": action,
            "price": price,
            "confidence": handoff.confidence if handoff else 1.0,
            "handoff_status": "accepted" if handoff else "local",
            "handoff_reason": handoff.reason if handoff else extra.get("reason", action),
            **extra,
        }
        self.decisions.insert(0, entry)
        self.decisions = self.decisions[:250]

    def _log(self, event_type: str, payload: dict[str, Any]) -> None:
        self.event_log.insert(0, {"timestamp": time.time(), "event_type": event_type, **payload})
        self.event_log = self.event_log[:500]

    def snapshot(self) -> SimulationSnapshot:
        self._mark_to_market()
        return SimulationSnapshot(
            config=self.config,
            sessions=list(self.sessions.values()),
            replay=self.replay,
            current_prices=dict(self.current_prices),
            account=self.account,
            tickers=list(self.tickers.values()),
            decisions=list(self.decisions),
            event_log=list(self.event_log),
        )

    def account_status(self) -> dict[str, Any]:
        self._mark_to_market()
        data = self.account.model_dump(mode="json")
        data["positions"] = [position.model_dump(mode="json") for position in self.account.positions.values()]
        return data
