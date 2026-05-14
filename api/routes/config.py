"""
Config read + live-update endpoints.

GET  /api/config        — returns current config.yaml values (no secrets)
POST /api/config        — writes a subset of safe fields back to config.yaml
                          and hot-reloads them into the running engine.
POST /api/token         — updates Dhan access token in .env and triggers
                          engine reconnect (no service restart needed).

Only the fields in MUTABLE_KEYS can be changed at runtime to prevent
accidental credential exposure via the API.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

MUTABLE_KEYS = {
    ("signal", "zscore_threshold"),
    ("signal", "min_spot_delta"),
    ("signal", "zscore_lookback_minutes"),
    ("execution", "stop_loss_pct"),
    ("execution", "trailing_stop_activation_pct"),
    ("execution", "trailing_stop_pct"),
    ("execution", "cooldown_minutes"),
    ("execution", "daily_drawdown_kill_pct"),
    ("execution", "morning_filter_start"),
    ("execution", "morning_filter_end"),
    ("execution", "virtual_capital"),
}

_SAFE_SECTIONS = ("signal", "execution", "instrument", "ws", "api")


class ConfigPatch(BaseModel):
    section: str
    key: str
    value: Any


@router.get("/config")
async def get_config(request: Request) -> dict:
    cfg = request.app.state.config
    return {s: cfg.get(s, {}) for s in _SAFE_SECTIONS}


@router.post("/config")
async def patch_config(patch: ConfigPatch, request: Request) -> dict:
    if (patch.section, patch.key) not in MUTABLE_KEYS:
        raise HTTPException(status_code=400, detail=f"Key {patch.section}.{patch.key} is not mutable")

    cfg = request.app.state.config
    if patch.section not in cfg:
        raise HTTPException(status_code=400, detail=f"Unknown section: {patch.section}")

    old_value = cfg[patch.section].get(patch.key)
    cfg[patch.section][patch.key] = patch.value

    # Hot-apply virtual_capital directly into the running execution engine
    if patch.section == "execution" and patch.key == "virtual_capital":
        exc = getattr(request.app.state, "execution_engine", None)
        if exc is not None and not exc.open_trade:
            new_cap = float(patch.value)
            exc.config.virtual_capital = new_cap
            exc._capital = new_cap
            exc._start_capital = new_cap
            exc._day_start_capital = new_cap
            request.app.state.engine_state.capital = new_cap

    # Persist to disk
    config_path = getattr(request.app.state, "config_path", "config.yaml")
    try:
        with open(config_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False)
    except OSError as exc:
        logger.warning("Could not write config: %s", exc)

    logger.info("Config updated: %s.%s  %s → %s", patch.section, patch.key, old_value, patch.value)
    return {"ok": True, "section": patch.section, "key": patch.key, "value": patch.value}


class TokenUpdate(BaseModel):
    access_token: str


@router.post("/token")
async def update_token(body: TokenUpdate, request: Request) -> dict:
    """Update Dhan access token in .env and trigger engine WS reconnect."""
    token = body.access_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token cannot be empty")

    # Update .env file
    env_path = Path("/opt/market-sentinel/.env")
    if not env_path.exists():
        env_path.write_text(f"DHAN_CLIENT_ID=1102982629\nDHAN_ACCESS_TOKEN={token}\n")
    else:
        content = env_path.read_text()
        if "DHAN_ACCESS_TOKEN=" in content:
            content = re.sub(r"^DHAN_ACCESS_TOKEN=.*$", f"DHAN_ACCESS_TOKEN={token}",
                             content, flags=re.MULTILINE)
        else:
            content += f"\nDHAN_ACCESS_TOKEN={token}\n"
        env_path.write_text(content)

    # Update running config in memory
    request.app.state.config.setdefault("dhan", {})["access_token"] = token

    # Hot-update InstrumentManager so it uses the new token immediately
    im = getattr(request.app.state, "instrument_manager", None)
    if im is not None:
        im.update_token(token)

    # Signal engine to reconnect WS with new token
    engine_state = request.app.state.engine_state
    if hasattr(engine_state, "reconnect_event"):
        engine_state.reconnect_event.set()

    logger.info("Dhan access token updated — reconnect triggered")
    return {"ok": True, "message": "Token updated. Engine reconnecting with new credentials."}
