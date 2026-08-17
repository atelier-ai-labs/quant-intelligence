from datetime import datetime, timedelta, timezone

from quant_intelligence.trading import AutonomousTrader, FakeClock, IntervalScheduler, PaperBroker, TradingCycleService
from quant_intelligence.trading.audit import TradingAuditStore
from quant_intelligence.trading.market import SequencedFixtureMarketDataProvider
from quant_intelligence.trading.risk import RiskGate
from quant_intelligence.trading.status import StatusStore
from quant_intelligence.strategies import SmaTrendStrategy
from tests.integration.test_trading import bars

START = datetime(2020, 1, 3, 12, tzinfo=timezone.utc)

def make_trader(tmp_path, provider=None, broker=None, scheduler=None, clock=None):
    provider = provider or SequencedFixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 10, 9]))
    broker = broker or PaperBroker(1000, transaction_cost_bps=0, state_path=tmp_path / "broker.json")
    cycle = TradingCycleService(strategy=SmaTrendStrategy(3), broker=broker, market_data=provider, risk_gate=RiskGate(transaction_cost_bps=0), audit_store=TradingAuditStore(tmp_path / "audit"))
    return AutonomousTrader(symbol="SYNTH", cycle_service=cycle, scheduler=scheduler or IntervalScheduler(timedelta(days=1), START), clock=clock or FakeClock(START), status_store=StatusStore(tmp_path / "status.json")), broker, provider

def test_scheduled_cycle_fires_and_updates_status(tmp_path):
    trader, broker, provider = make_trader(tmp_path)
    provider.advance(3); trader.start()
    decisions = trader.run_due_cycles()
    assert len(decisions) == 1; assert decisions[0].outcome == "EXECUTED"; assert len(broker.fills) == 1
    assert trader.status.state == "running"; assert trader.status.last_cycle_id == decisions[0].cycle_id; assert trader.status.current_cash == 4; assert trader.status.current_equity == 1000

def test_cycle_does_not_fire_before_due_time(tmp_path):
    clock = FakeClock(START - timedelta(hours=1)); trader, broker, provider = make_trader(tmp_path, clock=clock)
    provider.advance(3); trader.start()
    assert trader.run_due_cycles() == []; assert len(broker.orders) == 0

def test_multiple_scheduled_periods_progress_through_simulation(tmp_path):
    clock = FakeClock(START); provider = SequencedFixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 10, 9]))
    trader, broker, provider = make_trader(tmp_path, provider=provider, clock=clock); trader.start()
    provider.advance(3); first = trader.run_due_cycles()
    clock.advance(timedelta(days=1)); provider.advance(4); second = trader.run_due_cycles()
    clock.advance(timedelta(days=1)); provider.advance(5); third = trader.run_due_cycles()
    assert [decision.signal.value for decision in first + second + third] == ["BUY", "SELL", "HOLD"]
    assert len(broker.orders) == 2; assert len(broker.fills) == 2; assert trader.status.last_cycle_outcome == "HOLD"
    assert len(list((tmp_path / "audit").glob("*.json"))) == 3

def test_restart_does_not_duplicate_order_or_fill(tmp_path):
    provider = SequencedFixtureMarketDataProvider("SYNTH", bars([10, 11, 12, 13])); provider.advance(3)
    first, broker, _ = make_trader(tmp_path, provider=provider); first.start(); first_decision = first.run_due_cycles()[0]
    restored_broker = PaperBroker(0, transaction_cost_bps=0, state_path=tmp_path / "broker.json")
    restarted, restored_broker, _ = make_trader(tmp_path, provider=provider, broker=restored_broker); restarted.start(); second_decision = restarted.run_due_cycles()[0]
    assert second_decision == first_decision; assert len(restored_broker.orders) == 1; assert len(restored_broker.fills) == 1

def test_stop_is_graceful_and_prevents_future_triggers(tmp_path):
    trader, broker, provider = make_trader(tmp_path); provider.advance(3); trader.start(); trader.stop()
    assert trader.run_due_cycles() == []; assert trader.status.state == "stopped"; assert not broker.orders

def test_failed_cycle_stops_without_retry_loop(tmp_path):
    class FailingCycle:
        calls = 0
        def run(self, symbol, now):
            self.calls += 1; raise RuntimeError("unexpected cycle failure")
    clock = FakeClock(START); failing = FailingCycle()
    trader = AutonomousTrader(symbol="SYNTH", cycle_service=failing, scheduler=IntervalScheduler(timedelta(days=1), START), clock=clock, status_store=StatusStore(tmp_path / "status.json"))
    trader.start(); clock.advance(timedelta(days=3)); assert trader.run_due_cycles() == []
    assert failing.calls == 1; assert trader.status.state == "stopped"; assert trader.status.last_cycle_outcome == "FAILED"; assert "unexpected" in trader.status.last_error
    assert trader.run_due_cycles() == []

def test_status_persists_across_restart(tmp_path):
    provider = SequencedFixtureMarketDataProvider("SYNTH", bars([10, 11, 12])); provider.advance(3)
    trader, _, _ = make_trader(tmp_path, provider=provider); trader.start(); decision = trader.run_due_cycles()[0]
    restored = StatusStore(tmp_path / "status.json").load()
    assert restored.last_cycle_id == decision.cycle_id; assert restored.current_positions[0].shares == 83; assert restored.current_equity == 1000
