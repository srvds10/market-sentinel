from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

PCR_STALE_SECONDS = 300.0  # must match core/engine.py

router = APIRouter(prefix="/api")


@router.get("/status")
async def get_status(request: Request) -> dict:
    state = request.app.state.engine_state
    imap = None
    if hasattr(request.app.state, "instrument_manager"):
        raw = request.app.state.instrument_manager.current_map()
        if raw:
            imap = {
                "atm_strike":        raw.atm_strike,
                "itm_call_symbol":   raw.itm_call.symbol if raw.itm_call else None,
                "itm_call_strike":   raw.itm_call.strike_price if raw.itm_call else None,
                "itm_put_symbol":    raw.itm_put.symbol if raw.itm_put else None,
                "itm_put_strike":    raw.itm_put.strike_price if raw.itm_put else None,
                "otm_call_symbol":   raw.otm_call.symbol,
                "otm_call_strike":   raw.otm_call.strike_price,
                "otm_call_delta":    raw.otm_call.delta,
                "otm_call_iv":       round(raw.otm_call.iv * 100, 2),
                "otm_put_symbol":    raw.otm_put.symbol,
                "otm_put_strike":    raw.otm_put.strike_price,
                "otm_put_delta":     raw.otm_put.delta,
                "otm_put_iv":        round(raw.otm_put.iv * 100, 2),
            }

    return {
        "engine_state":       state.engine_state.value,
        "spot_ltp":           state.spot_ltp,
        "atm_ltp":            state.atm_ltp,
        "atm_put_ltp":        state.atm_put_ltp,
        "itm_call_ltp":       state.itm_call_ltp,
        "itm_put_ltp":        state.itm_put_ltp,
        "otm_call_ltp":       state.otm_call_ltp,
        "otm_put_ltp":        state.otm_put_ltp,
        "last_z_score":       state.last_z_score,
        "last_ratio":         state.last_ratio,
        "z_sample_count":     state.z_sample_count,
        "market_bias":        state.market_bias,
        "spot_delta_5m":      state.spot_delta_5m,
        "capital":            state.capital,
        "daily_pnl":          state.daily_pnl,
        "daily_pnl_pct":      state.daily_pnl_pct,
        "open_trade":         state.open_trade,
        "last_signal":        state.last_signal,
        "warmup_remaining":   state.warmup_remaining_seconds,
        "reconnect_count":    state.reconnect_count,
        "instrument_map":     imap,
        "started_at":         state.started_at,
        "vwap":               state.vwap,
        "above_vwap":         state.above_vwap,
        "pressure_verdict":       state.pressure_verdict,
        "heavyweight_score":      state.heavyweight_score,
        "heavyweight_direction":  state.heavyweight_direction,
        "heavyweight_stocks":     list(state.heavyweight_stocks),
        "nifty_pcr":             state.nifty_pcr,
        "pcr_sentiment":         state.pcr_sentiment,
        "pcr_trend":             state.pcr_trend,
        "pcr_stale": (
            state.pcr_last_update == 0.0
            or (time.monotonic() - state.pcr_last_update) > PCR_STALE_SECONDS
        ),
        "pcr_call_oi":           state.pcr_call_oi,
        "pcr_put_oi":            state.pcr_put_oi,
        "morning_verdict":       state.morning_verdict,
        "straddle_drift_pct":    state.straddle_drift_pct,
        "option_efficiency":     state.option_efficiency,
        "morning_snap_taken":    state.morning_snap_taken,
        "morning_straddle_open": state.morning_straddle_open,
        "itm_call_mins":          list(state.itm_call_mins),
        "atm_call_mins":      list(state.atm_call_mins),
        "otm_call_mins":      list(state.otm_call_mins),
        "otm_put_mins":       list(state.otm_put_mins),
        "atm_put_mins":       list(state.atm_put_mins),
        "itm_put_mins":       list(state.itm_put_mins),
    }
