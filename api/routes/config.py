"""
Config read + live-update endpoints.

GET  /api/config        — returns current config.yaml values (no secrets)
POST /api/config        — writes a subset of safe fields back to config.yaml
                          and hot-reloads them into the running engine.

Only the fields in MUTABLE_KEYS can be changed at runtime to prevent
accidental credential exposure via the API.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

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

    # Persist to disk
    config_path = getattr(request.app.state, "config_path", "config.yaml")
    try:
        with open(config_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False)
    except OSError as exc:
        logger.warning("Could not write config: %s", exc)

    logger.info("Config updated: %s.%s  %s → %s", patch.section, patch.key, old_value, patch.value)
    return {"ok": True, "section": patch.section, "key": patch.key, "value": patch.value}
