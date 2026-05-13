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

    def test_trailing_stop(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")

        # Price runs up 25% — activates trailing (activation at +20%)
        exec_engine.on_tick("ATM-CE", 125.0)
        assert exec_engine.open_trade.trailing_active is True

        # Peak is 125; trail floor = 125 * (1 - 0.15) = 106.25
        closed = exec_engine.on_tick("ATM-CE", 106.0)
        assert closed is not None
        assert closed.exit_reason == ExitReason.TRAILING_STOP
        assert closed.pnl > 0

    def test_trailing_stop_not_triggered_before_activation(self, exec_engine: ExecutionEngine):
        exec_engine.reset_day()
        exec_engine.try_open(make_signal(), atm_ltp=100.0, atm_symbol="ATM-CE")

        # Price goes up only 10% — trailing NOT active yet
        result = exec_engine.on_tick("ATM-CE", 110.0)
        assert result is None
        assert exec_engine.open_trade.trailing_active is False

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
