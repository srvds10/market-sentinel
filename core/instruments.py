"""
Instrument map: queries the Dhan option chain API to find the strikes
whose delta falls in the target range (0.10–0.15).

InstrumentManager runs a recalibration loop every 30 minutes and
notifies the SignalEngine of new symbol assignments.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

import httpx

from core.signal import SignalEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Black-Scholes constants
# ---------------------------------------------------------------------------

RISK_FREE_RATE     = 0.065     # annualised; used in BS pricing & IV inversion
FALLBACK_SIGMA     = 0.14      # used until live LTP yields a converged IV
TRADING_DAYS_YEAR  = 252       # day-count convention for T = days / N
LTP_STALENESS_SECS = 30.0      # ignore cached LTPs older than this for IV


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class OptionStrike:
    symbol: str
    security_id: str
    strike_price: float
    is_call: bool
    delta: float
    ltp: float
    expiry_days: int = 5  # days to expiry, used for BS delta calc
    iv: float = FALLBACK_SIGMA  # implied volatility (annualised)


@dataclass
class InstrumentMap:
    """Wide strike grid + currently-active ATM/OTM selection.

    The grid (all_calls, all_puts) is built at calibration time and stays
    stable while spot stays inside the grid range.  The active fields
    (atm_call, atm_put, otm_call, otm_put) are re-picked from the grid
    on every spot tick by `select_active_for_spot` — no WS reconnect
    needed when ATM shifts within the grid.
    """
    timestamp: float
    spot_symbol: str
    spot_security_id: str   # "13" for Nifty 50 on NSE_IDX
    spot_ltp: float
    expiry_days: int

    # Wide strike grid (subscribed to once at WS connect time)
    all_calls: list[OptionStrike]   # sorted ascending by strike
    all_puts:  list[OptionStrike]   # sorted ascending by strike

    # Currently-selected leg (mutated as spot drifts)
    atm_strike: float
    atm_call: OptionStrike
    atm_put: OptionStrike
    otm_call: OptionStrike  # delta closest to 0.10–0.15
    otm_put: OptionStrike


# ---------------------------------------------------------------------------
# Black-Scholes delta (for option chain delta filtering)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    t = 1.0 / (1.0 + 0.2316419 * abs(x))
    poly = t * (0.319381530
                + t * (-0.356563782
                       + t * (1.781477937
                              + t * (-1.821255978
                                     + t * 1.330274429))))
    p = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-0.5 * x * x) * poly
    return p if x >= 0 else 1.0 - p


def bs_delta(S: float, K: float, T: float, sigma: float, is_call: bool) -> float:
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return (1.0 if S > K else 0.0) if is_call else (-1.0 if S < K else 0.0)
    r  = 0.065
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Black-Scholes option price."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K, 0.0) if is_call else max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Vega = dPrice/dSigma (identical for calls and puts)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return S * math.sqrt(T) * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)


def compute_iv(
    market_price: float,
    S: float,
    K: float,
    T: float,
    is_call: bool,
    r: float = 0.065,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> float | None:
    """Newton–Raphson implied volatility.

    Returns annualised sigma, or None if the price is below intrinsic,
    vega collapses, or the iteration doesn't converge to within 50 paise.
    """
    if T <= 0 or S <= 0 or K <= 0 or market_price <= 0:
        return None
    intrinsic = max(S - K, 0.0) if is_call else max(K - S, 0.0)
    if market_price < intrinsic - 0.01:
        return None

    sigma = 0.20
    for _ in range(max_iter):
        vega = bs_vega(S, K, T, r, sigma)
        if abs(vega) < 1e-10:
            break
        diff = bs_price(S, K, T, r, sigma, is_call) - market_price
        if abs(diff) < tol:
            return sigma
        sigma -= diff / vega
        sigma = max(0.01, min(sigma, 5.0))

    # Accept if within 50 paise of the target price
    if abs(bs_price(S, K, T, r, sigma, is_call) - market_price) < 0.50:
        return sigma
    return None


def find_closest_delta_strike(strikes: list[OptionStrike], target: float) -> OptionStrike:
    return min(strikes, key=lambda s: abs(abs(s.delta) - target))


# ---------------------------------------------------------------------------
# InstrumentManager
# ---------------------------------------------------------------------------

class InstrumentManager:
    """Fetches the Dhan option chain and resolves the 5-leg instrument map.

    Recalibrates on the configured interval so ATM/OTM strikes track a trending
    market.  The scrip master CSV is cached for the trading day (it is a static
    daily file) so only the first calibration of the day downloads it.
    A WS reconnect is triggered only when the subscribed instruments actually
    change (i.e. ATM/OTM strike shifted), not on every recalibration tick.
    """

    def __init__(
        self,
        signal_engine: SignalEngine,
        recalibration_interval_minutes: float = 30.0,
        target_delta_min: float = 0.10,
        target_delta_max: float = 0.15,
        dhan_client_id: str = "",
        dhan_access_token: str = "",
        instrument_name: str = "NIFTY",
        on_instruments_changed: Optional[Callable[[], None]] = None,
    ) -> None:
        self._signal_engine   = signal_engine
        self._interval        = recalibration_interval_minutes * 60.0
        self._target_delta    = (target_delta_min + target_delta_max) / 2.0
        self._dhan_client_id  = dhan_client_id
        self._dhan_access_token = dhan_access_token
        self._instrument_name = instrument_name
        self._on_instruments_changed = on_instruments_changed
        self._current_map: Optional[InstrumentMap] = None
        self._recalibrate_now = asyncio.Event()
        # Scrip master CSV cache — valid for one trading day
        self._csv_cache: Optional[str] = None
        self._csv_cache_date: Optional[date] = None
        # Live option LTP cache for IV computation: symbol -> (ltp, monotonic_ts)
        self._ltp_cache: dict[str, tuple[float, float]] = {}
        # Symbol-to-OptionStrike index, rebuilt on each calibration
        self._strike_index: dict[str, OptionStrike] = {}
        # Most recent spot price, updated by the engine on every spot tick
        self._current_spot: float = 0.0

    def update_token(self, token: str) -> None:
        """Hot-update the access token and trigger immediate recalibration."""
        self._dhan_access_token = token
        self._recalibrate_now.set()

    async def run(self) -> None:
        while True:
            try:
                await self._calibrate()
                # Wait for the interval or an immediate recalibrate request
                try:
                    await asyncio.wait_for(self._recalibrate_now.wait(), timeout=self._interval)
                    self._recalibrate_now.clear()
                    logger.info("Token updated — recalibrating instruments immediately")
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Recalibration failed: %s", exc, exc_info=True)
                # On failure, wait up to 60s or until a new token arrives
                try:
                    await asyncio.wait_for(self._recalibrate_now.wait(), timeout=60)
                    self._recalibrate_now.clear()
                    logger.info("Token updated — retrying recalibration")
                except asyncio.TimeoutError:
                    pass

    async def _calibrate(self) -> None:
        imap = await self._fetch_map()
        changed = self._instruments_changed(imap)
        self._current_map = imap
        self._signal_engine.set_instrument_map(
            spot_symbol     = imap.spot_symbol,
            atm_call_symbol = imap.atm_call.symbol,
            atm_put_symbol  = imap.atm_put.symbol,
            otm_call_symbol = imap.otm_call.symbol,
            otm_put_symbol  = imap.otm_put.symbol,
        )
        # Pre-register every grid symbol so windows accumulate history
        # before the active leg ever rolls onto them.
        self._signal_engine.register_symbols(self.all_grid_symbols())
        # Rebuild symbol → OptionStrike index for O(1) per-tick IV updates
        self._strike_index = {
            s.symbol: s
            for s in imap.all_calls + imap.all_puts
        }
        logger.info(
            "Calibrated — grid=%d strikes  ATM=%.0f  "
            "OTM call %.0f (Δ=%.3f  IV=%.1f%%)  "
            "OTM put %.0f (Δ=%.3f  IV=%.1f%%)%s",
            len(imap.all_calls) + len(imap.all_puts),
            imap.atm_strike,
            imap.otm_call.strike_price, imap.otm_call.delta, imap.otm_call.iv * 100,
            imap.otm_put.strike_price,  imap.otm_put.delta,  imap.otm_put.iv * 100,
            "  [grid unchanged]" if not changed else "  [GRID SHIFTED — reconnecting WS]",
        )
        if changed and self._on_instruments_changed:
            self._on_instruments_changed()

    def _instruments_changed(self, new_map: InstrumentMap) -> bool:
        """True when the *grid* (set of subscribed strikes) changed — i.e. the
        WS needs to resubscribe.  Active ATM/OTM shifts inside the same grid
        do NOT trigger a reconnect; they are handled by update_active_for_spot.
        """
        old = self._current_map
        if old is None:
            return True
        old_call_ids = {c.security_id for c in old.all_calls}
        new_call_ids = {c.security_id for c in new_map.all_calls}
        old_put_ids  = {p.security_id for p in old.all_puts}
        new_put_ids  = {p.security_id for p in new_map.all_puts}
        return (
            old.spot_security_id != new_map.spot_security_id
            or old_call_ids != new_call_ids
            or old_put_ids  != new_put_ids
        )

    def current_map(self) -> Optional[InstrumentMap]:
        return self._current_map

    def update_active_for_spot(self, spot: float) -> bool:
        """Re-pick ATM/OTM legs from the grid based on the latest spot tick.
        Returns True if the active selection changed (so the SignalEngine
        instrument map should be refreshed)."""
        if self._current_map is None or spot <= 0:
            return False
        self._current_spot = spot
        return self._select_active_for_spot(self._current_map, spot)

    def update_option_ltp(self, symbol: str, ltp: float) -> None:
        """Cache LTP and immediately recompute IV/delta for that specific strike.

        Called on every option tick so IV is always as fresh as the last print,
        not delayed until the next spot tick.  O(1) via the strike index.
        """
        if ltp <= 0:
            return
        ts = time.monotonic()
        self._ltp_cache[symbol] = (ltp, ts)

        strike = self._strike_index.get(symbol)
        spot   = self._current_spot
        imap   = self._current_map
        if strike is None or spot <= 0 or imap is None:
            return

        T  = max(imap.expiry_days, 1) / TRADING_DAYS_YEAR
        iv = compute_iv(ltp, spot, strike.strike_price, T, strike.is_call,
                        r=RISK_FREE_RATE)
        if iv is not None:
            strike.iv    = round(iv, 4)
            strike.delta = round(bs_delta(spot, strike.strike_price, T, iv,
                                          strike.is_call), 4)

    def all_grid_symbols(self) -> set[str]:
        """All strike symbols currently subscribed (for window pre-registration)."""
        if self._current_map is None:
            return set()
        return ({self._current_map.spot_symbol}
                | {s.symbol for s in self._current_map.all_calls}
                | {s.symbol for s in self._current_map.all_puts})

    def get_dhan_instruments(self) -> list:
        """Return DhanInstrument list for the WS client subscription.

        Returns just Nifty spot until the first calibration completes;
        afterwards returns spot + every strike in the grid (~27 instruments).
        """
        from core.ws_client import DhanInstrument

        imap = self._current_map
        if imap is None:
            return [DhanInstrument("13", "NSE_IDX", "NIFTY-SPOT")]

        instruments: list = [
            DhanInstrument(imap.spot_security_id, "NSE_IDX", imap.spot_symbol),
        ]
        for s in imap.all_calls + imap.all_puts:
            instruments.append(DhanInstrument(s.security_id, "NSE_FNO", s.symbol))
        return [i for i in instruments if i.security_id]

    # ------------------------------------------------------------------
    # Instrument lookup via Dhan public scrip master (no auth required)
    # ------------------------------------------------------------------

    # Dhan publishes a daily instrument master CSV — no API key needed.
    _SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

    # Hardcoded spot security IDs for major indices on NSE_IDX
    _SPOT_SECURITY_IDS = {
        "NIFTY":       "13",
        "BANKNIFTY":   "25",
        "FINNIFTY":    "27",
        "MIDCPNIFTY":  "442",
    }

    async def _fetch_map(self) -> InstrumentMap:
        """Build instrument map from the public Dhan scrip master CSV.

        No authentication needed — the CSV is a public daily download.
        We need the spot LTP to calculate ATM strike, so we use the
        Dhan intraday chart API (which is in the official spec) as a
        fallback spot source when the WebSocket hasn't connected yet.
        If we have a current_map already, we reuse its spot_ltp.
        """
        today = date.today()
        if self._csv_cache and self._csv_cache_date == today:
            csv_text = self._csv_cache
            logger.debug("Scrip master: using cached CSV for %s", today)
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self._SCRIP_MASTER_URL)
                if not resp.is_success:
                    raise ValueError(
                        f"Dhan scrip master download failed ({resp.status_code}). "
                        f"Check internet connectivity."
                    )
                csv_text = resp.text
            self._csv_cache = csv_text
            self._csv_cache_date = today
            logger.info("Scrip master: downloaded and cached for %s", today)

        # Parse: find nearest weekly/monthly expiry for this instrument
        calls, puts = self._parse_scrip_master(csv_text)

        if not calls or not puts:
            raise ValueError(
                f"No options found for {self._instrument_name} in scrip master"
            )

        # Use existing spot LTP if available, otherwise fall back to a
        # rough ATM calculation (will be corrected once WS ticks arrive)
        spot = self._current_map.spot_ltp if self._current_map else 0.0
        if spot <= 0:
            # Try to get spot from intraday chart (authenticated)
            spot = await self._fetch_spot_ltp()

        is_bank = "BANK" in self._instrument_name
        step    = 100 if is_bank else 50
        atm_strike = self._round_to_100(spot) if is_bank else self._round_to_50(spot)

        # Calculate BS delta for each strike (re-computed in select_active_for_spot
        # as spot moves, but we initialise here so the grid has reasonable values)
        T     = max(calls[0].expiry_days, 1) / TRADING_DAYS_YEAR
        for s in calls + puts:
            s.delta = round(bs_delta(spot, s.strike_price, T, FALLBACK_SIGMA, s.is_call), 4)

        # Build the grid: 13 calls (2 ITM + ATM + 10 OTM) and 13 puts.
        #   call ITM  → strike < spot
        #   call OTM  → strike > spot
        #   put  ITM  → strike > spot
        #   put  OTM  → strike < spot
        call_strikes_wanted = {atm_strike + step * i for i in range(-2, 11)}
        put_strikes_wanted  = {atm_strike + step * i for i in range(-10, 3)}

        all_calls = sorted(
            [c for c in calls if c.strike_price in call_strikes_wanted],
            key=lambda s: s.strike_price,
        )
        all_puts = sorted(
            [p for p in puts if p.strike_price in put_strikes_wanted],
            key=lambda s: s.strike_price,
        )

        if not all_calls or not all_puts:
            raise ValueError(
                f"Strike grid is empty for {self._instrument_name} around ATM={atm_strike}. "
                f"Available call strikes: {sorted({c.strike_price for c in calls})[:10]}…"
            )

        spot_security_id = self._SPOT_SECURITY_IDS.get(self._instrument_name, "13")

        # Build the map with placeholder active legs; select_active_for_spot
        # below picks the real ones based on live spot.
        imap = InstrumentMap(
            timestamp        = time.time(),
            spot_symbol      = f"{self._instrument_name}-SPOT",
            spot_security_id = spot_security_id,
            spot_ltp         = spot,
            expiry_days      = max(calls[0].expiry_days, 1),
            all_calls        = all_calls,
            all_puts         = all_puts,
            atm_strike       = atm_strike,
            atm_call         = all_calls[0],
            atm_put          = all_puts[0],
            otm_call         = all_calls[-1],
            otm_put          = all_puts[0],
        )
        self._select_active_for_spot(imap, spot)

        logger.info(
            "Scrip master grid built — spot=%.0f ATM=%.0f T=%dd | "
            "calls %s..%s (%d) | puts %s..%s (%d) | "
            "active OTM call %s (Δ=%.3f  IV=%.1f%%) | active OTM put %s (Δ=%.3f  IV=%.1f%%)",
            spot, atm_strike, imap.expiry_days,
            int(all_calls[0].strike_price), int(all_calls[-1].strike_price), len(all_calls),
            int(all_puts[0].strike_price),  int(all_puts[-1].strike_price),  len(all_puts),
            imap.otm_call.symbol, imap.otm_call.delta, imap.otm_call.iv * 100,
            imap.otm_put.symbol,  imap.otm_put.delta,  imap.otm_put.iv * 100,
        )
        return imap

    def _select_active_for_spot(self, imap: InstrumentMap, spot: float) -> bool:
        """Re-pick active ATM/OTM strikes from the grid based on live spot.

        Recomputes BS delta using live implied vol (Newton–Raphson from market
        LTP) when available; falls back to sigma=0.14 if no LTP is cached yet.
        Returns True if any active leg changed.
        """
        is_bank = "BANK" in self._instrument_name
        atm_strike = self._round_to_100(spot) if is_bank else self._round_to_50(spot)

        T = max(imap.expiry_days, 1) / TRADING_DAYS_YEAR
        now = time.monotonic()

        iv_computed = 0
        for s in imap.all_calls + imap.all_puts:
            cached = self._ltp_cache.get(s.symbol)
            sigma = FALLBACK_SIGMA
            if cached is not None:
                ltp, ts = cached
                if ltp > 0 and (now - ts) <= LTP_STALENESS_SECS:
                    iv = compute_iv(ltp, spot, s.strike_price, T, s.is_call,
                                    r=RISK_FREE_RATE)
                    if iv is not None:
                        sigma = iv
                        iv_computed += 1
            s.iv = round(sigma, 4)
            s.delta = round(bs_delta(spot, s.strike_price, T, sigma, s.is_call), 4)

        if iv_computed:
            logger.debug("IV computed for %d/%d grid strikes", iv_computed,
                         len(imap.all_calls) + len(imap.all_puts))

        new_atm_call = min(imap.all_calls, key=lambda s: abs(s.strike_price - atm_strike))
        new_atm_put  = min(imap.all_puts,  key=lambda s: abs(s.strike_price - atm_strike))
        otm_calls = [c for c in imap.all_calls if c.strike_price > atm_strike]
        otm_puts  = [p for p in imap.all_puts  if p.strike_price < atm_strike]
        new_otm_call = find_closest_delta_strike(otm_calls or imap.all_calls, self._target_delta)
        new_otm_put  = find_closest_delta_strike(otm_puts  or imap.all_puts,  self._target_delta)

        changed = (
            new_atm_call.security_id != imap.atm_call.security_id
            or new_atm_put.security_id  != imap.atm_put.security_id
            or new_otm_call.security_id != imap.otm_call.security_id
            or new_otm_put.security_id  != imap.otm_put.security_id
        )

        imap.atm_strike = atm_strike
        imap.spot_ltp   = spot
        imap.atm_call   = new_atm_call
        imap.atm_put    = new_atm_put
        imap.otm_call   = new_otm_call
        imap.otm_put    = new_otm_put
        return changed

    def _parse_scrip_master(self, csv_text: str) -> tuple[list[OptionStrike], list[OptionStrike]]:
        """Parse Dhan scrip master CSV and return call/put OptionStrike lists
        for the nearest expiry of self._instrument_name.

        Primary filter: SEM_INSTRUMENT_NAME == "OPTIDX" (index options)
        Underlying:     SM_SYMBOL_NAME == instrument_name  OR
                        SEM_TRADING_SYMBOL starts with instrument_name
        CE/PE:          SEM_OPTION_TYPE if present; otherwise SEM_TRADING_SYMBOL suffix

        Dhan's scrip master uses single-char SEM_SEGMENT codes (E/M/C/…) and
        does not reliably carry "CE"/"PE" in SEM_OPTION_TYPE across all CSV
        versions, so we derive option type from the trading symbol suffix.
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        today  = date.today()

        rows_by_expiry: dict[date, list[dict]] = {}
        total_rows = 0
        name_upper = self._instrument_name.upper()
        sample_optidx: list[dict] = []   # for diagnostics on failure

        for row in reader:
            total_rows += 1
            inst     = row.get("SEM_INSTRUMENT_NAME", "").strip()
            sym      = (row.get("SEM_TRADING_SYMBOL", "") or row.get("SEM_CUSTOM_SYMBOL", "")).strip()
            sm_sym   = row.get("SM_SYMBOL_NAME", "").strip().upper()
            opt_raw  = row.get("SEM_OPTION_TYPE", "").strip().upper()

            # Must be an index option contract
            if inst not in ("OPTIDX", "OPTSTK"):
                continue

            # Capture a few OPTIDX rows for diagnostics in case we find nothing
            if len(sample_optidx) < 3:
                sample_optidx.append({
                    "inst": inst, "sym": sym, "sm_sym": sm_sym,
                    "opt": opt_raw, "exp": row.get("SEM_EXPIRY_DATE", ""),
                    "seg": row.get("SEM_SEGMENT", ""),
                })

            # Underlying check via trading symbol prefix (SM_SYMBOL_NAME is an
            # internal Dhan code like 'SX50OPT', not the human-readable name)
            if not sym.upper().startswith(name_upper):
                continue

            # Determine CE/PE from SEM_OPTION_TYPE; fall back to symbol suffix
            sym_upper = sym.upper()
            if opt_raw in ("CE", "C") or sym_upper.endswith("CE"):
                is_call: bool | None = True
            elif opt_raw in ("PE", "P") or sym_upper.endswith("PE"):
                is_call = False
            else:
                continue

            # Expiry may include time ('2026-05-27 15:30:00') — slice to date part
            exp_str = row.get("SEM_EXPIRY_DATE", "").strip()[:10]
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            except ValueError:
                try:
                    exp_date = datetime.strptime(exp_str, "%d-%b-%Y").date()
                except ValueError:
                    continue

            if exp_date < today:
                continue
            row["_is_call"] = is_call   # stash so second pass doesn't re-derive
            rows_by_expiry.setdefault(exp_date, []).append(row)

        if not rows_by_expiry:
            logger.error(
                "Scrip master: no %s options found. rows=%d  "
                "sample_OPTIDX_rows=%s",
                self._instrument_name, total_rows, sample_optidx,
            )
            raise ValueError(
                f"No upcoming {self._instrument_name} options found in scrip master "
                f"(total rows={total_rows})"
            )

        nearest_expiry = min(rows_by_expiry)
        days_to_expiry = (nearest_expiry - today).days
        logger.info("Scrip master: using expiry %s (%d days away)", nearest_expiry, days_to_expiry)

        calls: list[OptionStrike] = []
        puts:  list[OptionStrike] = []

        for row in rows_by_expiry[nearest_expiry]:
            try:
                strike      = float(row.get("SEM_STRIKE_PRICE", 0))
                security_id = str(row.get("SEM_SMST_SECURITY_ID", "")).strip()
                is_call     = row["_is_call"]
                opt_label   = "CE" if is_call else "PE"
                symbol      = f"{self._instrument_name}-{int(strike)}-{opt_label}"
                obj = OptionStrike(
                    symbol=symbol, security_id=security_id,
                    strike_price=strike, is_call=is_call,
                    delta=0.0, ltp=0.0,
                    expiry_days=max(days_to_expiry, 1),
                )
                (calls if is_call else puts).append(obj)
            except (ValueError, KeyError):
                continue

        return sorted(calls, key=lambda s: s.strike_price), \
               sorted(puts,  key=lambda s: s.strike_price)

    async def _fetch_spot_ltp(self) -> float:
        """Get NIFTY spot via Dhan intraday chart (requires auth).
        Falls back to a hardcoded recent-ish value if auth fails.
        """
        if not self._dhan_access_token:
            logger.warning("No token — using fallback spot for initial calibration")
            return 24500.0  # rough NIFTY level; corrected once WS ticks arrive

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.dhan.co/v2/charts/intraday",
                    headers={
                        "access-token": self._dhan_access_token,
                        "client-id":    self._dhan_client_id,
                        "Content-Type": "application/json",
                    },
                    json={
                        "securityId":      "13",
                        "exchangeSegment": "IDX_I",
                        "instrument":      "INDEX",
                        "interval":        "1",
                        "fromDate":        date.today().strftime("%Y-%m-%d"),
                        "toDate":          date.today().strftime("%Y-%m-%d"),
                    },
                )
                if resp.is_success:
                    data = resp.json()
                    closes = data.get("close", [])
                    if closes:
                        return float(closes[-1])
        except Exception as e:
            logger.warning("Spot fetch via chart API failed: %s", e)

        logger.warning("Could not get live spot — using fallback; will recalibrate after first tick")
        return 24500.0

    @staticmethod
    def _round_to_50(price: float) -> float:
        return round(price / 50) * 50

    @staticmethod
    def _round_to_100(price: float) -> float:
        return round(price / 100) * 100
