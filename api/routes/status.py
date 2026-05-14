from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

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
        "server_time":        time.time(),
        "last_heartbeat":     state.last_heartbeat,
    }
