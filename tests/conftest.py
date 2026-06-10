import pytest
from core.signal import SignalConfig, SignalEngine
from core.execution import ExecutionConfig, ExecutionEngine


@pytest.fixture
def signal_engine():
    cfg = SignalConfig(
        window_seconds=30.0,
        min_spot_delta=2.0,
        zscore_threshold=2.0,
        zscore_lookback_minutes=20.0,
        min_history_samples=5,   # low for fast tests
    )
    engine = SignalEngine(cfg)
    engine.set_instrument_map(
        spot_symbol="SPOT",
        atm_call_symbol="ATM-CE",
        atm_put_symbol="ATM-PE",
        otm_call_symbol="OTM-CE",
        otm_put_symbol="OTM-PE",
    )
    return engine


@pytest.fixture
def exec_engine():
    cfg = ExecutionConfig(
        virtual_capital=100_000.0,
        max_position_pct=0.20,
        stop_loss_pct=0.30,
        target_ratio=3.0,
        cooldown_minutes=0.0,    # disabled for fast tests
        morning_filter_start="00:00",
        morning_filter_end="00:00",   # no opening blackout — trading allowed all day
        last_entry_time="23:59",      # no entry cutoff in tests
        force_close_time="23:59",     # never force-close in tests
        daily_drawdown_kill_pct=0.40,
        lot_size=50,
        slippage_rupees=0.0,     # tests assume frictionless fills
        scale_by_zscore=False,   # tests assume uncapped sizing
    )
    return ExecutionEngine(cfg)
