from sentinel_archive.core import SentinelArchive
from sentinel_archive.models import MarketBar, SimulationConfig


def bar(minute: int, close: float, symbol: str = "SPY") -> MarketBar:
    return MarketBar(
        timestamp=f"2026-06-09T13:{30 + minute:02d}:00Z",
        symbol=symbol,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000 + minute,
    )


def handoff(action: str, symbol: str = "SPY", **extra):
    payload = {
        "contract_version": "edge.pulse.handoff.v1",
        "symbol": symbol,
        "action": action,
        "confidence": 0.91,
        "reason": "test",
        "mode": "paper",
        "orb_session": "market_open",
        "idempotency_key": f"edge:{symbol}:{action}:market_open:123:test",
        "source": "sentinel_edge",
        "created_at": 1_782_000_000.0,
        "metadata": {},
    }
    payload.update(extra)
    return payload


def test_replay_step_updates_current_prices_and_index():
    engine = SentinelArchive()
    session = engine.import_bars("session-one", [bar(0, 540.0), bar(1, 541.25)])
    engine.start_replay(session.session_id, speed=1, loop=False)

    first = engine.step()
    second = engine.step()

    assert first.current_prices["SPY"] == 540.0
    assert second.current_prices["SPY"] == 541.25
    assert second.replay.index == 2


def test_buy_handoff_opens_position_with_slippage_and_commission():
    engine = SentinelArchive(
        SimulationConfig(starting_cash=10_000, default_quantity=10, slippage_bps=10, commission_per_order=1)
    )
    session = engine.import_bars("session-one", [bar(0, 100.0)])
    engine.start_replay(session.session_id)
    engine.step()

    response = engine.process_handoff(handoff("buy"))
    position = engine.account.positions["SPY"]

    assert response["accepted"] is True
    assert position.quantity == 10
    assert position.entry_price == 100.1
    assert engine.account.cash == 8_998.0


def test_live_mode_handoff_is_rejected_by_sentinel_archive():
    engine = SentinelArchive(SimulationConfig(starting_cash=10_000, default_quantity=10))
    session = engine.import_bars("session-one", [bar(0, 100.0)])
    engine.start_replay(session.session_id)
    engine.step()

    response = engine.process_handoff(handoff("buy", mode="live"))

    assert response["accepted"] is False
    assert response["status"] == "rejected"
    assert response["reason"] == "live_mode_not_supported"
    assert engine.account.positions == {}


def test_handoff_with_broker_metadata_is_rejected_by_sentinel_archive():
    engine = SentinelArchive(SimulationConfig(starting_cash=10_000, default_quantity=10))
    session = engine.import_bars("session-one", [bar(0, 100.0)])
    engine.start_replay(session.session_id)
    engine.step()

    response = engine.process_handoff(
        handoff(
            "buy",
            metadata={
                "price": 100.0,
                "broker_ids": ["alpaca"],
                "credentials": {"api_key": "secret"},
            },
        )
    )

    assert response["accepted"] is False
    assert response["status"] == "rejected"
    assert response["reason"] == "broker_metadata_not_supported"
    assert engine.account.positions == {}


def test_stop_buying_blocks_future_buy_handoffs_for_ticker():
    engine = SentinelArchive(SimulationConfig(starting_cash=10_000, default_quantity=10))
    session = engine.import_bars("session-one", [bar(0, 100.0)])
    engine.start_replay(session.session_id)
    engine.step()

    stop = engine.process_handoff(handoff("stop_buying"))
    buy = engine.process_handoff(
        handoff("buy", idempotency_key="edge:SPY:buy:market_open:124:test")
    )

    assert stop["accepted"] is True
    assert buy["accepted"] is False
    assert buy["reason"] == "ticker_disabled"
    assert engine.account.positions == {}


def test_duplicate_handoff_is_idempotent():
    engine = SentinelArchive(SimulationConfig(starting_cash=10_000, default_quantity=10))
    session = engine.import_bars("session-one", [bar(0, 100.0)])
    engine.start_replay(session.session_id)
    engine.step()
    payload = handoff("buy")

    first = engine.process_handoff(payload)
    second = engine.process_handoff(payload)

    assert first["handoff_id"] == second["handoff_id"]
    assert engine.account.positions["SPY"].quantity == 10
    assert second["reason"] == "duplicate"


def test_trailing_stop_sells_when_price_crosses_trailing_floor():
    engine = SentinelArchive(SimulationConfig(starting_cash=10_000, default_quantity=10))
    session = engine.import_bars(
        "session-one",
        [
            bar(0, 100.0),
            bar(1, 110.0),
            MarketBar(
                timestamp="2026-06-09T13:32:00Z",
                symbol="SPY",
                open=109.0,
                high=109.0,
                low=103.0,
                close=104.0,
                volume=1000,
            ),
        ],
    )
    engine.start_replay(session.session_id)
    engine.step()
    engine.process_handoff(handoff("buy"))
    engine.process_handoff(handoff("trailing_stop", stop_type="trailing", trailing_percent=5))

    engine.step()
    snapshot = engine.step()

    assert "SPY" not in engine.account.positions
    assert snapshot.decisions[0]["action"] == "trailing_stop_sell"
    assert snapshot.account.cash == 10_045.0
