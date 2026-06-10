"""Unit tests for the execution state machine."""

import time
import pytest
from unittest.mock import patch

from core.execution import ExecutionEngine, ExecutionConfig, ExitReason
from core.signal import SignalEvent


def make_signal(z: float = 2.5, direction: str = "CALL") -> SignalEvent:
    return SignalEvent(
        timestamp=time.time(),
        z_score=z,
        ratio=1.5,
        spot_ltp=22_000.0,
        atm_symbol="ATM-CE",
        otm_call_symbol="OTM-CE",
        otm_put_symbol="OTM-PE",
        direction=direction,
        delta_spot=3.0,
        delta_otm=4.5,
    )


class TestExecutionEngine:

    def test_open_trade(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        trade = exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        assert trade is not None
        assert trade.entry_price == pytest.approx(100.0)
        assert trade.direction == "CALL"
        assert trade.stop_loss == pytest.approx(70.0)    # 100 * (1 - 0.30)
        assert exec_engine.open_trade is not None

    def test_cannot_open_second_trade(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), 100.0, "ATM-CE")
        second = exec_engine.try_open(make_signal(), 110.0, "ATM-CE")
        assert second is None

    def test_stop_loss_exit(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        closed = exec_engine.on_tick("ATM-CE", 69.0)   # below 70 stop
        assert closed is not None
        assert closed.exit_reason == ExitReason.STOP_LOSS
        assert closed.pnl < 0
        assert exec_engine.open_trade is None

    def test_take_profit_exit(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        # SL distance = 100 * 0.30 = 30; TP = 100 + 3*30 = 190
        assert exec_engine.open_trade.take_profit == pytest.approx(190.0)
        closed = exec_engine.on_tick("ATM-CE", 190.0)
        assert closed is not None
        assert closed.exit_reason == ExitReason.TAKE_PROFIT
        assert closed.pnl > 0

    def test_take_profit_not_triggered_before_target(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        # Price at 150 — below TP of 190, above SL of 70
        result = exec_engine.on_tick("ATM-CE", 150.0)
        assert result is None
        assert exec_engine.open_trade is not None

    def test_divergence_collapse_exit(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        closed = exec_engine.on_divergence_collapse(ltp=105.0)
        assert closed is not None
        assert closed.exit_reason == ExitReason.DIVERGENCE
        assert closed.pnl > 0

    def test_time_stop(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        with patch.object(exec_engine, '_past_force_close', return_value=True):
            closed = exec_engine.check_time_stop(atm_ltp=102.0)
        assert closed is not None
        assert closed.exit_reason == ExitReason.TIME_STOP

    def test_kill_switch_trigger(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        closed = exec_engine.trigger_kill_switch(atm_ltp=50.0)
        assert closed is not None
        assert closed.exit_reason == ExitReason.KILL_SWITCH
        assert exec_engine.kill_active is True

    def test_kill_switch_blocks_new_trades(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine._kill_active = True
        trade = exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        assert trade is None

    def test_capital_updated_on_close(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        start_cap = exec_engine.capital
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        exec_engine.on_tick("ATM-CE", 69.0)   # stop loss hit
        # Capital should have decreased by the loss
        assert exec_engine.capital < start_cap

    def test_daily_drawdown_kills_automatically(self, exec_engine: ExecutionEngine):
        """Four consecutive 30% losses should breach the 40% daily drawdown."""
        exec_engine.reset_day()

        for _ in range(4):
            if exec_engine.kill_active:
                break
            exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
            exec_engine.on_tick("ATM-CE", 1.0)   # catastrophic loss

        assert exec_engine.kill_active is True

    def test_pnl_calculated_correctly(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        trade = exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        assert trade is not None
        qty = trade.qty
        closed = exec_engine.on_divergence_collapse(ltp=110.0)
        assert closed is not None
        expected_pnl = (110.0 - 100.0) * qty * 50
        assert closed.pnl == pytest.approx(expected_pnl, rel=0.01)

    def test_warmup_blocks_trades(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.set_warmup(seconds=9999.0)   # very long warmup
        trade = exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        assert trade is None

    def test_tick_on_wrong_symbol_ignored(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        closed = exec_engine.on_tick("WRONG-SYMBOL", 1.0)
        assert closed is None
        assert exec_engine.open_trade is not None


def _engine(**overrides) -> ExecutionEngine:
    cfg = ExecutionConfig(
        virtual_capital=100_000.0, max_position_pct=0.20, stop_loss_pct=0.30,
        target_ratio=3.0,
        cooldown_minutes=0.0, morning_filter_start="00:00", morning_filter_end="00:00",
        last_entry_time="23:59", force_close_time="23:59", daily_drawdown_kill_pct=0.40, lot_size=50,
        slippage_rupees=0.0, scale_by_zscore=False,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return ExecutionEngine(cfg)


class TestSlippage:

    def test_entry_price_includes_slippage(self):
        eng = _engine(slippage_rupees=2.0)
        eng.reset_day()
        trade = eng.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        assert trade.entry_price == pytest.approx(102.0)
        assert trade.stop_loss == pytest.approx(102.0 * 0.7)

    def test_round_trip_pnl_subtracts_slippage_twice(self):
        eng = _engine(slippage_rupees=2.0)
        eng.reset_day()
        trade = eng.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        qty = trade.qty
        # LTP ticks up to 110; we fill at 108 (110 - 2 slippage)
        closed = eng.trigger_kill_switch(atm_ltp=110.0)
        assert closed.exit_price == pytest.approx(108.0)
        # PnL = (108 - 102) * qty * 50
        assert closed.pnl == pytest.approx(6.0 * qty * 50)


class TestZScoreSizing:

    def test_weak_signal_gets_one_lot(self):
        eng = _engine(scale_by_zscore=True, scale_zscore_per_lot=1.5)
        eng.reset_day()
        trade = eng.try_open(make_signal(z=2.0), atm_ltp=10.0, atm_symbol="ATM-CE")
        # Z=2.0, scale=1.5 → 1 lot (capped by sizing rule, not max_lots)
        assert trade.qty == 1

    def test_strong_signal_scales_up(self):
        eng = _engine(scale_by_zscore=True, scale_zscore_per_lot=1.5)
        eng.reset_day()
        trade = eng.try_open(make_signal(z=6.0), atm_ltp=10.0, atm_symbol="ATM-CE")
        # Z=6.0, scale=1.5 → 4 lots (assuming max_lots allows)
        # max_lots = 20000 / (10 * 50) = 40 lots; sizing caps at 4
        assert trade.qty == 4

    def test_qty_never_exceeds_capital_cap(self):
        eng = _engine(scale_by_zscore=True, scale_zscore_per_lot=0.5)
        eng.reset_day()
        # Expensive option: capital cap forces small qty even if Z is huge
        trade = eng.try_open(make_signal(z=10.0), atm_ltp=500.0, atm_symbol="ATM-CE")
        # max_lots = 20000 / (500 * 50) ≈ 0.8 → max(1, …) = 1
        assert trade.qty == 1


class TestTradingWindow:
    """Time guards must operate on IST regardless of the server's local timezone."""

    def test_blackout_blocks_open(self, monkeypatch):
        eng = _engine(morning_filter_end="09:30", force_close_time="15:15")
        eng.reset_day()
        # Pretend IST is 09:20 — inside the opening blackout
        monkeypatch.setattr("core.execution.ist_minutes_now", lambda: 9 * 60 + 20)
        trade = eng.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        assert trade is None

    def test_past_last_entry_blocks_open(self, monkeypatch):
        eng = _engine(morning_filter_end="09:30", last_entry_time="14:00", force_close_time="15:15")
        eng.reset_day()
        # Pretend IST is 14:05 — past last-entry cutoff (new trades blocked)
        monkeypatch.setattr("core.execution.ist_minutes_now", lambda: 14 * 60 + 5)
        trade = eng.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        assert trade is None

    def test_inside_trading_window_allows_open(self, monkeypatch):
        eng = _engine(morning_filter_end="09:30", force_close_time="15:15")
        eng.reset_day()
        # Pretend IST is 11:00 — well inside trading hours
        monkeypatch.setattr("core.execution.ist_minutes_now", lambda: 11 * 60)
        trade = eng.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        assert trade is not None

    def test_force_close_check_uses_ist(self, monkeypatch):
        eng = _engine(morning_filter_end="00:00", force_close_time="15:15")
        eng.reset_day()
        # Open while inside window
        monkeypatch.setattr("core.execution.ist_minutes_now", lambda: 11 * 60)
        eng.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")
        # Now jump IST to 15:16 — check_time_stop should close
        monkeypatch.setattr("core.execution.ist_minutes_now", lambda: 15 * 60 + 16)
        closed = eng.check_time_stop(atm_ltp=102.0)
        assert closed is not None
        assert closed.exit_reason == ExitReason.TIME_STOP
