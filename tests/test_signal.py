"""Unit tests for the signal engine: RollingWindow, ZScoreTracker, SignalEngine."""

import time
import pytest
from core.signal import RollingWindow, ZScoreTracker, SignalEngine, Tick


# ---------------------------------------------------------------------------
# RollingWindow
# ---------------------------------------------------------------------------

class TestRollingWindow:
    def test_delta_requires_two_points(self):
        w = RollingWindow(30.0)
        assert w.delta() is None
        w.push(100.0, ts=0.0)
        assert w.delta() is None

    def test_delta_simple(self):
        w = RollingWindow(30.0)
        w.push(100.0, ts=0.0)
        w.push(105.0, ts=1.0)
        assert w.delta() == pytest.approx(5.0)

    def test_negative_delta(self):
        w = RollingWindow(30.0)
        w.push(200.0, ts=0.0)
        w.push(195.0, ts=1.0)
        assert w.delta() == pytest.approx(-5.0)

    def test_old_ticks_evicted(self):
        w = RollingWindow(10.0)
        w.push(100.0, ts=0.0)
        w.push(110.0, ts=5.0)
        w.push(120.0, ts=15.0)   # ts=0 is now stale
        # oldest remaining is ts=5 (110), latest is ts=15 (120)
        assert w.delta() == pytest.approx(10.0)

    def test_all_ticks_evicted_gives_none(self):
        w = RollingWindow(5.0)
        w.push(100.0, ts=0.0)
        w.push(105.0, ts=100.0)  # ts=0 evicted; only one point left
        assert w.delta() is None

    def test_latest(self):
        w = RollingWindow(30.0)
        assert w.latest() is None
        w.push(42.0, ts=1.0)
        assert w.latest() == pytest.approx(42.0)
        w.push(43.5, ts=2.0)
        assert w.latest() == pytest.approx(43.5)

    def test_len(self):
        w = RollingWindow(30.0)
        assert len(w) == 0
        w.push(1.0, ts=0.0)
        w.push(2.0, ts=1.0)
        assert len(w) == 2


# ---------------------------------------------------------------------------
# ZScoreTracker
# ---------------------------------------------------------------------------

class TestZScoreTracker:
    def test_none_before_min_samples(self):
        z = ZScoreTracker(lookback_minutes=20.0, min_samples=10)
        for i in range(9):
            z.push(float(i), ts=float(i))
        assert z.zscore(5.0) is None

    def test_zscore_mean_zero_at_mean(self):
        z = ZScoreTracker(lookback_minutes=20.0, min_samples=5)
        for i in range(20):
            z.push(10.0, ts=float(i))   # constant series, mean=10, std=0
        # constant series → std=0 → None
        assert z.zscore(10.0) is None

    def test_zscore_correct_value(self):
        import numpy as np
        z = ZScoreTracker(lookback_minutes=20.0, min_samples=5)
        samples = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        for i, s in enumerate(samples):
            z.push(s, ts=float(i))
        arr = np.array(samples)
        expected = (12.0 - arr.mean()) / arr.std()
        assert z.zscore(12.0) == pytest.approx(expected, abs=1e-4)

    def test_old_samples_evicted(self):
        z = ZScoreTracker(lookback_minutes=1.0, min_samples=2)   # 60s window
        z.push(100.0, ts=0.0)
        z.push(100.0, ts=1.0)
        z.push(1.0, ts=200.0)   # ts=0 and ts=1 are now evicted
        assert z.sample_count() == 1
        assert z.zscore(5.0) is None


# ---------------------------------------------------------------------------
# SignalEngine
# ---------------------------------------------------------------------------

def make_tick(symbol: str, ltp: float, ts: float) -> Tick:
    return Tick(symbol=symbol, security_id="0", ltp=ltp, timestamp=ts)


class TestSignalEngine:
    def _prime_zscore(self, engine: SignalEngine, n: int = 10) -> None:
        """Push n ticks that produce a valid ratio to build up the Z-score history."""
        base_ts = 0.0
        for i in range(n):
            ts = base_ts + i * 35.0   # 35s apart so each pair forms a full 30s window
            # push old tick first
            engine._windows["SPOT"].push(22_000.0, ts=ts)
            engine._windows["OTM-CE"].push(50.0, ts=ts)
            engine._windows["OTM-PE"].push(50.0, ts=ts)
            engine._windows["ATM-CE"].push(100.0, ts=ts)
            engine._windows["ATM-PE"].push(100.0, ts=ts)
            # push new tick 30s later with a tiny ratio
            engine._windows["SPOT"].push(22_003.0, ts=ts + 30.0)
            engine._windows["OTM-CE"].push(50.5, ts=ts + 30.0)
            engine._windows["OTM-PE"].push(50.5, ts=ts + 30.0)
            # trigger evaluation via OTM-CE tick
            engine._signal_engine_eval(ts + 30.0) if hasattr(engine, '_signal_engine_eval') else None
            # use public on_tick
            engine.on_tick(make_tick("OTM-CE", 50.5, ts + 30.0))

    def test_no_signal_below_threshold(self, signal_engine: SignalEngine):
        ts = 1000.0
        # Build 30s windows with a small, normal ratio
        signal_engine._windows["SPOT"].push(22_000.0, ts=ts)
        signal_engine._windows["ATM-CE"].push(100.0,  ts=ts)
        signal_engine._windows["ATM-PE"].push(100.0,  ts=ts)
        signal_engine._windows["OTM-CE"].push(50.0,   ts=ts)
        signal_engine._windows["OTM-PE"].push(50.0,   ts=ts)

        signal_engine._windows["SPOT"].push(22_003.0, ts=ts + 30)
        signal_engine._windows["OTM-CE"].push(50.5,   ts=ts + 30)

        result = signal_engine.on_tick(make_tick("OTM-CE", 50.5, ts + 30))
        # Not enough Z-score history yet
        assert result is None

    def test_sideways_filter_suppresses_ratio(self, signal_engine: SignalEngine):
        ts = 2000.0
        # Spot barely moves (< 2 pts)
        signal_engine._windows["SPOT"].push(22_000.0,  ts=ts)
        signal_engine._windows["SPOT"].push(22_001.5,  ts=ts + 30)   # delta = 1.5 < 2.0
        signal_engine._windows["OTM-CE"].push(50.0, ts=ts)
        signal_engine._windows["OTM-CE"].push(55.0, ts=ts + 30)

        result = signal_engine.on_tick(make_tick("OTM-CE", 55.0, ts + 30))
        assert result is None

    def test_zero_division_never_occurs(self, signal_engine: SignalEngine):
        """Spot delta exactly at minimum threshold — ratio should not raise."""
        ts = 3000.0
        signal_engine._windows["SPOT"].push(22_000.0, ts=ts)
        signal_engine._windows["SPOT"].push(22_002.0, ts=ts + 30)  # exactly min_spot_delta
        signal_engine._windows["OTM-CE"].push(50.0, ts=ts)
        signal_engine._windows["OTM-CE"].push(52.0, ts=ts + 30)
        # Should not raise
        signal_engine.on_tick(make_tick("OTM-CE", 52.0, ts + 30))

    def test_signal_fires_when_zscore_high(self, signal_engine: SignalEngine):
        """Inject a spike ratio after building baseline to force a signal."""
        base_ts = 5000.0
        # Prime the Z-score baseline with normal (low) ratios
        for i in range(15):
            ts = base_ts + i * 40.0
            signal_engine._windows["SPOT"].push(22_000.0,  ts=ts)
            signal_engine._windows["SPOT"].push(22_003.0,  ts=ts + 30)
            signal_engine._windows["ATM-CE"].push(100.0,   ts=ts)
            signal_engine._windows["ATM-CE"].push(100.1,   ts=ts + 30)
            signal_engine._windows["ATM-PE"].push(100.0,   ts=ts)
            signal_engine._windows["ATM-PE"].push(100.1,   ts=ts + 30)
            signal_engine._windows["OTM-CE"].push(30.0,    ts=ts)
            signal_engine._windows["OTM-PE"].push(30.0,    ts=ts)
            # normal ratio: |0.1 / 3.0| ≈ 0.033
            signal_engine.on_tick(make_tick("OTM-CE", 30.1, ts + 30))

        # Now inject a spike: OTM moves a LOT while spot barely moves
        spike_ts = base_ts + 15 * 40.0
        signal_engine._windows["SPOT"].push(22_000.0,  ts=spike_ts)
        signal_engine._windows["SPOT"].push(22_003.0,  ts=spike_ts + 30)
        signal_engine._windows["OTM-CE"].push(30.0,    ts=spike_ts)
        signal_engine._windows["ATM-CE"].push(100.0,   ts=spike_ts)
        signal_engine._windows["ATM-PE"].push(100.0,   ts=spike_ts)
        signal_engine._windows["OTM-PE"].push(30.0,    ts=spike_ts)

        # OTM-CE jumps 6 pts while spot moved 3 → ratio = 2.0 (vs baseline ~0.033)
        result = signal_engine.on_tick(make_tick("OTM-CE", 36.0, spike_ts + 30))
        assert result is not None
        assert result.z_score > 2.0
        assert result.direction in ("CALL", "PUT")

    def test_unknown_symbol_ignored(self, signal_engine: SignalEngine):
        result = signal_engine.on_tick(make_tick("UNKNOWN-SYM", 100.0, 1.0))
        assert result is None

    def test_zscore_sample_count_increments(self, signal_engine: SignalEngine):
        ts = 9000.0
        signal_engine._windows["SPOT"].push(22_000.0, ts=ts)
        signal_engine._windows["SPOT"].push(22_003.0, ts=ts + 30)
        signal_engine._windows["OTM-CE"].push(30.0, ts=ts)
        signal_engine._windows["ATM-CE"].push(100.0, ts=ts)
        signal_engine._windows["ATM-PE"].push(100.0, ts=ts)
        signal_engine._windows["OTM-PE"].push(30.0, ts=ts)

        before = signal_engine.z_score_sample_count()
        signal_engine.on_tick(make_tick("OTM-CE", 31.0, ts + 30))
        assert signal_engine.z_score_sample_count() == before + 1
