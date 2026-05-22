"""
openmeteo_forecast.py
=====================
Open-Meteo Best-Match forecast layer for the NWS Weather Explorer.

For CONUS stations (KLAX, KNYC, KORD …) Open-Meteo's best_match model
automatically uses HRRR for the first ~18 hours (updates every hour,
3 km resolution) then hands off to GFS for the remainder.  This gives
a genuinely independent, higher-resolution signal from the orange NWS
line, which is based on the same GFS run published 1-2 hours later after
NWS forecaster review.

The module also fetches the HRRR and GFS model-run timestamps from
Open-Meteo's model-status metadata API so the chart header can display:
    "Open-Meteo · HRRR 14:00 UTC  ·  GFS 12:00 UTC"

Visual constants
----------------
OM_FORECAST_COLOR     = "#ffffff"   white line
OM_FORECAST_DOT_COLOR = "#e2e8f0"   off-white markers

Public API
----------
fetch_openmeteo_forecast(lat, lon, end_time, local_tz, units) -> dict
OpenMeteoForecastManager                — attach as app._om_forecast
draw_openmeteo_overlay(ax, om_mgr, …)  — called from weather_chart.py

Fix log
-------
- FIXED: Race condition in auto_fetch() where self.clear() reset
  self._fetching = False before the background thread started, allowing
  a second concurrent fetch to begin.  Now clears rows/state directly
  without calling clear(), so _fetching stays True throughout.
- FIXED: Added explicit error logging to stderr so fetch failures are
  always visible even when the logger has no handler.
- ADDED: _fetching guard logs a warning when a duplicate fetch is
  skipped, making it easier to trace double-fetch scenarios.
"""

from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger("openmeteo_forecast")
logger.addHandler(logging.NullHandler())

# ── Visual constants (imported by weather_chart.py) ───────────────────────────
OM_FORECAST_COLOR     = "#ffffff"
OM_FORECAST_LINESTYLE = "--"
OM_FORECAST_LINEWIDTH = 1.6
OM_FORECAST_ALPHA     = 0.88
OM_FORECAST_DOT_COLOR = "#e2e8f0"

OM_OBSERVED_COLOR     = "#a78bfa"
OM_OBSERVED_LINESTYLE = "-"
OM_OBSERVED_LINEWIDTH = 1.4
OM_OBSERVED_ALPHA     = 0.80
OM_OBSERVED_DOT_COLOR = "#c4b5fd"

OM_COMPARE_COLOR     = "#2dd4bf"   # teal — prior-period OM analysis overlay
OM_COMPARE_LINESTYLE = "-."
OM_COMPARE_LINEWIDTH = 1.6
OM_COMPARE_ALPHA     = 0.85
OM_COMPARE_DOT_COLOR = "#99f6e4"

OM_BASE        = "https://api.open-meteo.com/v1/forecast"
OM_STATUS_BASE = "https://api.open-meteo.com/data/status"
REQUEST_TIMEOUT = 15

# Open-Meteo model-status endpoints for the two CONUS-relevant models.
_MODEL_STATUS_URLS = {
    "HRRR": f"{OM_STATUS_BASE}?model=gfs_hrrr",
    "GFS":  f"{OM_STATUS_BASE}?model=gfs_seamless",
}


# ── Model run metadata ────────────────────────────────────────────────────────

def fetch_model_run_times() -> dict[str, str | None]:
    """
    Fetch the last initialisation time for HRRR and GFS.
    Returns {"HRRR": iso_str | None, "GFS": iso_str | None} in UTC.
    Non-blocking errors are swallowed and return None for that model.
    """
    result: dict[str, str | None] = {"HRRR": None, "GFS": None}
    for model_name, url in _MODEL_STATUS_URLS.items():
        try:
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            data = resp.json()
            unix_ts = (
                data.get("last_run_initialisation_time")
                or data.get("lastRunInitialisationTime")
            )
            if unix_ts is not None:
                dt = datetime.fromtimestamp(int(unix_ts), tz=timezone.utc)
                result[model_name] = dt.isoformat()
        except Exception as exc:
            logger.debug("Could not fetch %s model status: %s", model_name, exc)
    return result


def _fmt_run(iso_str: str | None) -> str:
    """Format UTC ISO → 'HH:MM UTC', e.g. '14:00 UTC'. Returns '?' on failure."""
    if not iso_str:
        return "?"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%H:%M UTC")
    except Exception:
        return iso_str[:16]


# ── Core forecast fetch ───────────────────────────────────────────────────────

def fetch_openmeteo_forecast(
    lat: float,
    lon: float,
    end_time: datetime,
    local_tz: Optional[str] = None,
    units: str = "F",
) -> dict:
    """
    Fetch Open-Meteo best_match hourly forecast for the window strictly
    after *end_time* (the last observation timestamp) through the DST-aware
    CLI day cap. We intentionally drop forecast points at or before *end_time*
    because Open-Meteo's best_match returns reanalysis for past dates that
    duplicates the OM Observed line 1:1.

    Also fetches HRRR + GFS run timestamps in a parallel thread.

    Returns
    -------
    {
        "rows":              list[{"time": iso_str, "temp": float}],
        "model_runs":        {"HRRR": iso_str|None, "GFS": iso_str|None},
        "header_text":       str,    # ready for fig.text()
        "generationtime_ms": float|None,
    }
    """
    from zoneinfo import ZoneInfo

    tz_obj = ZoneInfo(local_tz) if local_tz else timezone.utc

    # ── Normalise end_time to UTC-aware ───────────────────────────────────
    if end_time.tzinfo is None:
        end_time_utc = end_time.replace(tzinfo=timezone.utc)
    else:
        end_time_utc = end_time.astimezone(timezone.utc)

    # ── DST-aware local forecast cap ──────────────────────────────────────
    try:
        from weather_utils import forecast_cap_utc
        cap_utc = forecast_cap_utc(end_time_utc, local_tz or "UTC")
        print(
            f"[OM DEBUG] TZ cap: local_tz={local_tz!r} end_time_utc={end_time_utc!r} cap_utc={cap_utc!r}",
            file=sys.stderr, flush=True,
        )
    except Exception as exc:
        logger.warning("Timezone cap failed (%s); using UTC +1 day 01:00", exc)
        print(
            f"[OM ERROR] TZ cap failed: {exc}  local_tz={local_tz!r}",
            file=sys.stderr, flush=True,
        )
        cap_utc = (
            end_time_utc.replace(hour=1, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )

    # ── Fetch model run times in background ───────────────────────────────
    _run_buf: dict = {}

    def _meta():
        _run_buf.update(fetch_model_run_times())

    meta_t = threading.Thread(target=_meta, daemon=True)
    meta_t.start()

    # ── Open-Meteo hourly forecast request ───────────────────────────────
    # Open-Meteo rejects requests that mix start_date/end_date with
    # past_days/forecast_days.  Use start_date/end_date only so we get
    # exactly the window we need and nothing else.
    # Use LOCAL date so Open-Meteo returns the right day's data.
    # UTC date can be one day ahead in the evening for western timezones
    # (e.g. 22:00 PDT = next UTC day), which would cause sparse/missing data.
    start_date = end_time_utc.astimezone(tz_obj).strftime("%Y-%m-%d")
    end_date   = cap_utc.astimezone(tz_obj).strftime("%Y-%m-%d")

    params = {
        "latitude":         lat,
        "longitude":        lon,
        "hourly":           "temperature_2m",
        "temperature_unit": "fahrenheit" if units == "F" else "celsius",
        "timezone":         local_tz or "UTC",
        "start_date":       start_date,
        "end_date":         end_date,
        "models":           "best_match",
    }

    print(
        f"[OM DEBUG] Requesting Open-Meteo: lat={lat} lon={lon} "
        f"start_date={start_date} end_date={end_date} "
        f"end_time_utc={end_time_utc.isoformat()} cap_utc={cap_utc.isoformat()}",
        file=sys.stderr, flush=True,
    )

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            resp = requests.get(OM_BASE, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            print(
                f"[OM ERROR] Open-Meteo request attempt {attempt + 1} failed: {exc}",
                file=sys.stderr, flush=True,
            )
    if last_exc is not None:
        raise RuntimeError(f"Open-Meteo request failed: {last_exc}") from last_exc

    hourly    = data.get("hourly", {})
    times_raw = hourly.get("time", [])
    temps_raw = hourly.get("temperature_2m", [])
    gen_ms    = data.get("generationtime_ms")

    print(
        f"[OM DEBUG] Response: {len(times_raw)} hourly slots  "
        f"generationtime_ms={gen_ms}",
        file=sys.stderr, flush=True,
    )

    if not times_raw or not temps_raw:
        raise RuntimeError("Open-Meteo returned no hourly data.")

    # ── Parse and filter to (end_time, cap] ──────────────────────────────
    rows: list[dict] = []
    kept_count = 0
    filtered_before = 0
    filtered_after = 0
    temp_samples = []

    for i, (t_str, temp) in enumerate(zip(times_raw, temps_raw)):
        if temp is None:
            continue

        # Collect temperature samples for debugging
        if i < 5 or i % 10 == 0:
            temp_samples.append(f"idx{i}:{temp}")

        try:
            dt_naive = datetime.fromisoformat(t_str)
            if dt_naive.tzinfo is None:
                # Open-Meteo returns naive times in the requested timezone
                dt_local = dt_naive.replace(tzinfo=tz_obj)
            else:
                # If it already has tz info, convert to local timezone
                dt_local = dt_naive.astimezone(tz_obj)
            dt_utc = dt_local.astimezone(timezone.utc)
        except Exception:
            continue

        if dt_utc <= end_time_utc:
            filtered_before += 1
            continue
        if dt_utc > cap_utc:
            filtered_after += 1
            continue

        # Return times in UTC for consistent browser-side timezone handling
        # Chart.js timezone adapter will display in city_tz automatically
        utc_iso = f"{dt_utc.strftime('%Y-%m-%dT%H:%M:%S')}Z"
        rows.append({"time": utc_iso, "temp": round(float(temp), 2)})
        kept_count += 1

    print(
        f"[OM DEBUG] Raw temps sample: {temp_samples}",
        file=sys.stderr, flush=True,
    )
    print(
        f"[OM DEBUG] Filtered to {len(rows)} rows in window "
        f"({rows[0]['time'][:16] if rows else 'none'} → "
        f"{rows[-1]['time'][:16] if rows else 'none'})  "
        f"filtered_before={filtered_before} filtered_after={filtered_after}",
        file=sys.stderr, flush=True,
    )

    # ── Wait for metadata (max 5 s) ───────────────────────────────────────
    meta_t.join(timeout=5.0)
    model_runs: dict[str, str | None] = dict(_run_buf) if _run_buf else {"HRRR": None, "GFS": None}

    header_text = (
        f"Open-Meteo · HRRR {_fmt_run(model_runs.get('HRRR'))}"
        f"  ·  GFS {_fmt_run(model_runs.get('GFS'))}"
    )

    return {
        "rows":              rows,
        "model_runs":        model_runs,
        "header_text":       header_text,
        "generationtime_ms": gen_ms,
    }


# ── Manager ───────────────────────────────────────────────────────────────────

class OpenMeteoForecastManager:
    """
    Companion to ForecastManager for the Open-Meteo white forecast line.

    FIX: auto_fetch() no longer calls self.clear() before spawning the
    background thread.  Previously clear() set self._fetching = False,
    creating a window where a second call could slip past the guard and
    start a concurrent fetch.  State is now reset field-by-field so
    _fetching stays True from guard-check through to thread completion.

    In WeatherApp.__init__:
        self._om_forecast = OpenMeteoForecastManager(self)

    After each successful main fetch (inside worker() in run_fetch):
        end_dt = datetime.fromisoformat(end)
        self._om_forecast.auto_fetch(fetch_lat, fetch_lon, end_dt, tz, units)

    Calls app.on_openmeteo_ready() on the Tk main thread when data arrives.
    """

    def __init__(self, app):
        self._app         = app
        self._rows:        list[dict]            = []
        self._model_runs:  dict[str, str | None] = {"HRRR": None, "GFS": None}
        self._header_text: str                   = ""
        self._fetching:    bool                  = False

    @property
    def has_data(self) -> bool:
        return bool(self._rows)

    @property
    def header_text(self) -> str:
        return self._header_text

    @property
    def model_runs(self) -> dict:
        return self._model_runs

    def get_rows(self) -> list[dict]:
        return list(self._rows)

    def clear(self):
        """Reset all state.  Safe to call from the main thread at any time."""
        self._rows        = []
        self._model_runs  = {"HRRR": None, "GFS": None}
        self._header_text = ""
        self._fetching    = False

    def auto_fetch(
        self,
        lat: float,
        lon: float,
        end_time: datetime,
        local_tz: Optional[str] = None,
        units: str = "F",
    ) -> None:
        """
        Start a background fetch of Open-Meteo data.

        FIX: State fields (_rows, _model_runs, _header_text) are reset
        directly here — *without* calling self.clear() — so that
        self._fetching remains True from the guard check all the way
        through to the thread's finally block.  The old code called
        self.clear() which reset _fetching to False, then immediately
        re-set it to True, but that created a one-line window where a
        re-entrant call (e.g. from a rapid double-fetch) could slip
        past the guard.
        """
        if self._fetching:
            print(
                "[OM DEBUG] auto_fetch skipped — fetch already in progress",
                file=sys.stderr, flush=True,
            )
            return

        # ── Guard is set BEFORE any state mutation ────────────────────────
        self._fetching    = True
        # Reset stale data without touching _fetching
        self._rows        = []
        self._model_runs  = {"HRRR": None, "GFS": None}
        self._header_text = ""

        print(
            f"[OM DEBUG] auto_fetch started: lat={lat} lon={lon} "
            f"end_time={end_time!r} local_tz={local_tz!r} units={units!r}",
            file=sys.stderr, flush=True,
        )

        def worker():
            try:
                result            = fetch_openmeteo_forecast(lat, lon, end_time, local_tz, units)
                self._rows        = result["rows"]
                self._model_runs  = result["model_runs"]
                self._header_text = result["header_text"]
                print(
                    f"[OM DEBUG] worker done: {len(self._rows)} rows  "
                    f"has_data={self.has_data}  header={self._header_text!r}",
                    file=sys.stderr, flush=True,
                )
                logger.info("Open-Meteo: %d rows. %s", len(self._rows), self._header_text)
                if hasattr(self._app, "on_openmeteo_ready"):
                    self._app.after(0, self._app.on_openmeteo_ready)
            except Exception as exc:
                print(f"[OM ERROR] worker exception: {exc}", file=sys.stderr, flush=True)
                logger.error("OpenMeteoForecastManager: %s", exc)
            finally:
                self._fetching = False
                print("[OM DEBUG] _fetching reset to False", file=sys.stderr, flush=True)

        threading.Thread(target=worker, daemon=True).start()


# ── Observed fetch ────────────────────────────────────────────────────────────

def fetch_openmeteo_observed(
    lat: float,
    lon: float,
    start_time: datetime,
    end_time: datetime,
    local_tz: Optional[str] = None,
    units: str = "F",
) -> dict:
    """
    Fetch Open-Meteo HRRR analysis data for a past/current date range.

    Uses the same /v1/forecast endpoint with start_date/end_date in the past;
    Open-Meteo returns HRRR analysis runs (updated hourly, 3 km) for those dates.

    Returns {"rows": list[{"time": iso_str_utc, "temp": float}]}
    """
    from zoneinfo import ZoneInfo

    if start_time.tzinfo is None:
        start_utc = start_time.replace(tzinfo=timezone.utc)
    else:
        start_utc = start_time.astimezone(timezone.utc)

    if end_time.tzinfo is None:
        end_utc = end_time.replace(tzinfo=timezone.utc)
    else:
        end_utc = end_time.astimezone(timezone.utc)

    tz_obj = ZoneInfo(local_tz) if local_tz else timezone.utc
    now_utc = datetime.now(timezone.utc)
    # Clamp end to now so future forecast hours from Open-Meteo aren't included.
    end_utc = min(end_utc, now_utc)

    # past_days must cover start_utc; forecast_days=1 is required by the API
    # even when fetching purely historical analysis data.
    days_back = max(1, (now_utc.date() - start_utc.astimezone(tz_obj).date()).days + 1)

    params = {
        "latitude":         lat,
        "longitude":        lon,
        "hourly":           "temperature_2m",
        "temperature_unit": "fahrenheit" if units == "F" else "celsius",
        "timezone":         local_tz or "UTC",
        "past_days":        days_back,
        "forecast_days":    1,
        "models":           "best_match",
    }

    print(
        f"[OM-OBS DEBUG] Requesting observed: lat={lat} lon={lon} "
        f"past_days={days_back} window=[{start_utc.isoformat()} → {end_utc.isoformat()}]",
        file=sys.stderr, flush=True,
    )

    try:
        resp = requests.get(OM_BASE, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"Open-Meteo observed request failed: {exc}") from exc

    hourly    = data.get("hourly", {})
    times_raw = hourly.get("time", [])
    temps_raw = hourly.get("temperature_2m", [])

    if not times_raw or not temps_raw:
        raise RuntimeError("Open-Meteo observed returned no hourly data.")

    rows: list[dict] = []
    for t_str, temp in zip(times_raw, temps_raw):
        if temp is None:
            continue
        try:
            dt_naive = datetime.fromisoformat(t_str)
            dt_local = dt_naive.replace(tzinfo=tz_obj) if dt_naive.tzinfo is None else dt_naive.astimezone(tz_obj)
            dt_utc   = dt_local.astimezone(timezone.utc)
        except Exception:
            continue

        if dt_utc < start_utc or dt_utc > end_utc:
            continue

        rows.append({"time": f"{dt_utc.strftime('%Y-%m-%dT%H:%M:%S')}Z", "temp": round(float(temp), 2)})

    print(
        f"[OM-OBS DEBUG] {len(rows)} rows in window "
        f"({rows[0]['time'][:16] if rows else 'none'} → {rows[-1]['time'][:16] if rows else 'none'})",
        file=sys.stderr, flush=True,
    )

    return {"rows": rows}


class OpenMeteoObservedManager:
    """
    Fetches Open-Meteo HRRR analysis data for the observed window (past/current).
    Attach as app._om_observed.  Calls app.on_om_observed_ready() on the main thread.
    """

    def __init__(self, app):
        self._app      = app
        self._rows:    list[dict] = []
        self._fetching: bool      = False

    @property
    def has_data(self) -> bool:
        return bool(self._rows)

    def get_rows(self) -> list[dict]:
        return list(self._rows)

    def clear(self):
        self._rows     = []
        self._fetching = False

    def auto_fetch(
        self,
        lat: float,
        lon: float,
        start_time: datetime,
        end_time: datetime,
        local_tz: Optional[str] = None,
        units: str = "F",
    ) -> None:
        if self._fetching:
            return
        self._fetching = True
        self._rows     = []

        def worker():
            try:
                result     = fetch_openmeteo_observed(lat, lon, start_time, end_time, local_tz, units)
                self._rows = result["rows"]
                if hasattr(self._app, "on_om_observed_ready"):
                    self._app.after(0, self._app.on_om_observed_ready)
            except Exception as exc:
                print(f"[OM-OBS ERROR] {exc}", file=sys.stderr, flush=True)
                logger.error("OpenMeteoObservedManager: %s", exc)
            finally:
                self._fetching = False

        threading.Thread(target=worker, daemon=True).start()


# ── Chart drawing helper ──────────────────────────────────────────────────────

def draw_openmeteo_overlay(
    ax,
    om_mgr,
    obs_times: list,
    obs_temps: list,
    sym: str,
    x_left=None,
    x_right_effective=None,
    placed_anchors: list | None = None,
    query_tz: Optional[str] = None,
) -> tuple | None:
    """
    Draw the white Open-Meteo forecast line on *ax*.
    Bridges from the last observed point, same as the orange NWS line.

    Returns ("Open-Meteo Forecast", artists) for legend toggle, or None.
    """
    if om_mgr is None or not om_mgr.has_data:
        print(
            f"[OM DEBUG] draw_openmeteo_overlay: skipped "
            f"(om_mgr={om_mgr!r} has_data={getattr(om_mgr, 'has_data', 'N/A')})",
            file=sys.stderr, flush=True,
        )
        return None

    rows = om_mgr.get_rows()
    if not rows:
        return None

    try:
        f_times = [datetime.fromisoformat(r["time"]) for r in rows]
        f_temps = [r["temp"] for r in rows]

        # Normalize all times to query timezone if available
        if query_tz and f_times and f_times[0].tzinfo is not None:
            try:
                from zoneinfo import ZoneInfo
                target_tz = ZoneInfo(query_tz)
                f_times = [
                    dt.astimezone(target_tz) if dt.tzinfo is not None else dt
                    for dt in f_times
                ]
                print(
                    f"[OM DEBUG] Normalized {len(f_times)} times to {query_tz}",
                    file=sys.stderr, flush=True,
                )
            except Exception as tz_exc:
                print(
                    f"[OM DEBUG] Could not normalize times to {query_tz}: {tz_exc}",
                    file=sys.stderr, flush=True,
                )
    except Exception as exc:
        logger.warning("Could not parse Open-Meteo rows: %s", exc)
        return None

    if not f_times:
        return None

    print(
        f"[OM DEBUG] draw_openmeteo_overlay: drawing {len(f_times)} points "
        f"({f_times[0]} → {f_times[-1]})",
        file=sys.stderr, flush=True,
    )

    bridge_times = ([obs_times[-1]] + f_times) if obs_times else f_times
    bridge_temps = ([obs_temps[-1]] + f_temps) if obs_temps else f_temps

    _fill = ax.fill_between(
        bridge_times, bridge_temps,
        alpha=0.07, color=OM_FORECAST_COLOR, zorder=2,
    )
    _line, = ax.plot(
        bridge_times, bridge_temps,
        color=OM_FORECAST_COLOR,
        linewidth=OM_FORECAST_LINEWIDTH,
        linestyle=OM_FORECAST_LINESTYLE,
        alpha=OM_FORECAST_ALPHA,
        zorder=3,
        label="Open-Meteo Forecast",
    )

    # ── Min / max markers ─────────────────────────────────────────────────
    f_min_val  = min(f_temps)
    f_max_val  = max(f_temps)
    f_min_time = f_times[f_temps.index(f_min_val)]
    f_max_time = f_times[f_temps.index(f_max_val)]

    def _xo(t):
        try:
            if x_left is None or x_right_effective is None:
                return 6
            span = (x_right_effective - x_left).total_seconds()
            pos  = (t - x_left).total_seconds()
            return -6 if span > 0 and pos / span > 0.70 else 6
        except Exception:
            return 6

    def _py(time, temp, default_y, flip_y):
        for pt, ptemp in (placed_anchors or []):
            if abs((time - pt).total_seconds()) / 3600 < 2.5 and abs(temp - ptemp) < 4.0:
                return flip_y
        return default_y

    _xo_min = _xo(f_min_time)
    _xo_max = _xo(f_max_time)
    _py_min = _py(f_min_time, f_min_val, -18, 18)
    _py_max = _py(f_max_time, f_max_val,   8, -18)

    _sc_min = ax.scatter(
        [f_min_time], [f_min_val],
        color=OM_FORECAST_DOT_COLOR, s=55, zorder=6,
        marker="v", edgecolors="#0f1117", linewidths=0.8, alpha=OM_FORECAST_ALPHA,
    )
    _sc_max = ax.scatter(
        [f_max_time], [f_max_val],
        color=OM_FORECAST_DOT_COLOR, s=55, zorder=6,
        marker="^", edgecolors="#0f1117", linewidths=0.8, alpha=OM_FORECAST_ALPHA,
    )
    _an_min = ax.annotate(
        f"{f_min_val}{sym}",
        (f_min_time, f_min_val),
        textcoords="offset points", xytext=(_xo_min, _py_min),
        color=OM_FORECAST_COLOR, fontsize=7, fontfamily="Consolas",
        ha="right" if _xo_min < 0 else "left",
    )
    _an_max = ax.annotate(
        f"{f_max_val}{sym}",
        (f_max_time, f_max_val),
        textcoords="offset points", xytext=(_xo_max, _py_max),
        color=OM_FORECAST_COLOR, fontsize=7, fontfamily="Consolas",
        ha="right" if _xo_max < 0 else "left",
    )

    if placed_anchors is not None:
        placed_anchors.extend([(f_min_time, f_min_val), (f_max_time, f_max_val)])

    return "Open-Meteo Forecast", [_fill, _line, _sc_min, _sc_max, _an_min, _an_max]


def draw_openmeteo_observed_overlay(
    ax,
    om_obs_mgr,
    sym: str,
    x_left=None,
    x_right_effective=None,
    placed_anchors: list | None = None,
    query_tz: Optional[str] = None,
) -> tuple | None:
    """
    Draw the violet Open-Meteo observed (HRRR analysis) line on *ax*.
    Covers the same time window as the NWS observed line for comparison.
    Returns ("OM Observed", artists) for legend toggle, or None.
    """
    if om_obs_mgr is None or not om_obs_mgr.has_data:
        return None

    rows = om_obs_mgr.get_rows()
    if not rows:
        return None

    try:
        from zoneinfo import ZoneInfo
        tz_obj = ZoneInfo(query_tz) if query_tz else None

        o_times = []
        o_temps = []
        for r in rows:
            dt = datetime.fromisoformat(r["time"])
            if tz_obj and dt.tzinfo is not None:
                dt = dt.astimezone(tz_obj)
            o_times.append(dt)
            o_temps.append(r["temp"])
    except Exception as exc:
        logger.warning("Could not parse OM observed rows: %s", exc)
        return None

    if not o_times:
        return None

    _fill = ax.fill_between(
        o_times, o_temps,
        alpha=0.07, color=OM_OBSERVED_COLOR, zorder=2,
    )
    _line, = ax.plot(
        o_times, o_temps,
        color=OM_OBSERVED_COLOR,
        linewidth=OM_OBSERVED_LINEWIDTH,
        linestyle=OM_OBSERVED_LINESTYLE,
        alpha=OM_OBSERVED_ALPHA,
        zorder=3,
        label="OM Observed",
    )

    o_min_val  = min(o_temps)
    o_max_val  = max(o_temps)
    o_min_time = o_times[o_temps.index(o_min_val)]
    o_max_time = o_times[o_temps.index(o_max_val)]

    def _xo(t):
        try:
            if x_left is None or x_right_effective is None:
                return 6
            span = (x_right_effective - x_left).total_seconds()
            pos  = (t - x_left).total_seconds()
            return -6 if span > 0 and pos / span > 0.70 else 6
        except Exception:
            return 6

    def _py(time, temp, default_y, flip_y):
        for pt, ptemp in (placed_anchors or []):
            if abs((time - pt).total_seconds()) / 3600 < 2.5 and abs(temp - ptemp) < 4.0:
                return flip_y
        return default_y

    _sc_min = ax.scatter(
        [o_min_time], [o_min_val],
        color=OM_OBSERVED_DOT_COLOR, s=45, zorder=6,
        marker="v", edgecolors="#0f1117", linewidths=0.8, alpha=OM_OBSERVED_ALPHA,
    )
    _sc_max = ax.scatter(
        [o_max_time], [o_max_val],
        color=OM_OBSERVED_DOT_COLOR, s=45, zorder=6,
        marker="^", edgecolors="#0f1117", linewidths=0.8, alpha=OM_OBSERVED_ALPHA,
    )
    _an_min = ax.annotate(
        f"{o_min_val}{sym}",
        (o_min_time, o_min_val),
        textcoords="offset points", xytext=(_xo(o_min_time), _py(o_min_time, o_min_val, -18, 18)),
        color=OM_OBSERVED_COLOR, fontsize=7, fontfamily="Consolas",
        ha="right" if _xo(o_min_time) < 0 else "left",
    )
    _an_max = ax.annotate(
        f"{o_max_val}{sym}",
        (o_max_time, o_max_val),
        textcoords="offset points", xytext=(_xo(o_max_time), _py(o_max_time, o_max_val, 8, -18)),
        color=OM_OBSERVED_COLOR, fontsize=7, fontfamily="Consolas",
        ha="right" if _xo(o_max_time) < 0 else "left",
    )

    if placed_anchors is not None:
        placed_anchors.extend([(o_min_time, o_min_val), (o_max_time, o_max_val)])

    return "OM Observed", [_fill, _line, _sc_min, _sc_max, _an_min, _an_max]


# ── OM Compare (prior-period HRRR analysis) ───────────────────────────────────

class OpenMeteoCompareManager:
    """
    Fetches Open-Meteo HRRR analysis for the same prior period as CompareManager,
    then time-shifts rows forward by the offset so they overlay on the current
    query window.  Mirrors CompareManager's interface so the chart treats them
    identically except for colour.

    Attach as app._om_compare.  Calls app.on_om_compare_ready() on main thread.
    """

    def __init__(self, app):
        self._app      = app
        self._rows:    list[dict]    = []
        self._offset:  timedelta     = timedelta(days=1)
        self._fetching: bool         = False

    @property
    def has_data(self) -> bool:
        return bool(self._rows)

    def get_rows(self) -> list[dict]:
        """Rows time-shifted forward by offset, for chart alignment."""
        if not self._rows:
            return []
        return [
            {
                "time": (datetime.fromisoformat(r["time"]) + self._offset).isoformat(),
                "temp": r["temp"],
            }
            for r in self._rows
        ]

    def get_raw_rows(self) -> list[dict]:
        return list(self._rows)

    def clear(self):
        self._rows     = []
        self._fetching = False

    def fetch(
        self,
        result:      dict,
        lat:         float,
        lon:         float,
        tz:          Optional[str],
        units:       str = "F",
        offset:      Optional[timedelta] = None,
        query_start: Optional[str] = None,
    ) -> None:
        """
        Kick off a background fetch for the prior period using Open-Meteo HRRR
        analysis.  Window computation mirrors CompareManager.fetch() exactly.
        """
        if offset is not None:
            self._offset = offset

        data = result.get("data", [])
        if not data:
            return

        try:
            t_start = datetime.fromisoformat(data[0]["time"])
            t_end   = datetime.fromisoformat(data[-1]["time"])
        except Exception:
            return

        if t_start.tzinfo is None:
            t_start = t_start.replace(tzinfo=timezone.utc)
        if t_end.tzinfo is None:
            t_end = t_end.replace(tzinfo=timezone.utc)

        if query_start is not None:
            try:
                qs_dt = datetime.fromisoformat(query_start)
                if qs_dt.tzinfo is None:
                    qs_dt = qs_dt.replace(tzinfo=timezone.utc)
                compare_start = qs_dt - self._offset
            except Exception:
                compare_start = t_start - self._offset
        else:
            compare_start = t_start - self._offset

        # Cover the same observation window, plus a small buffer for resampling.
        compare_end = t_end - self._offset + timedelta(hours=2)

        self._fetching = True
        self._rows     = []

        _offset_snap = self._offset  # capture for worker closure

        def worker():
            try:
                r = fetch_openmeteo_observed(lat, lon, compare_start, compare_end, tz, units)
                self._rows = r.get("rows", [])
                print(
                    f"[OM-CMP] done: {len(self._rows)} rows  "
                    f"({self._rows[0]['time'][:16] if self._rows else 'none'} → "
                    f"{self._rows[-1]['time'][:16] if self._rows else 'none'})",
                    file=sys.stderr, flush=True,
                )
                if self._rows and hasattr(self._app, "on_om_compare_ready"):
                    self._app.after(0, self._app.on_om_compare_ready)
            except Exception as exc:
                print(f"[OM-CMP ERROR] {exc}", file=sys.stderr, flush=True)
                logger.error("OpenMeteoCompareManager: %s", exc)
            finally:
                self._fetching = False

        threading.Thread(target=worker, daemon=True).start()


def draw_om_compare_overlay(
    ax,
    om_cmp_mgr,
    sym:              str,
    offset:           Optional[timedelta] = None,
    x_left=None,
    x_right=None,
    x_right_effective=None,
    placed_anchors:   list | None = None,
    query_tz:         Optional[str] = None,
) -> tuple | None:
    """
    Draw the teal Open-Meteo prior-period line on *ax*.

    Parameters mirror draw_compare_overlay() from weather_compare.py.
    Returns ("OM Prior (Xd prior)", artists) for legend toggle, or None.
    """
    if om_cmp_mgr is None or not om_cmp_mgr.has_data:
        return None

    rows = om_cmp_mgr.get_rows()
    if not rows:
        return None

    try:
        from zoneinfo import ZoneInfo
        tz_obj = ZoneInfo(query_tz) if query_tz else None

        c_times = []
        c_temps = []
        for r in rows:
            dt = datetime.fromisoformat(r["time"])
            if tz_obj and dt.tzinfo is not None:
                dt = dt.astimezone(tz_obj)
            c_times.append(dt)
            c_temps.append(r["temp"])
    except Exception as exc:
        logger.warning("Could not parse OM compare rows: %s", exc)
        return None

    if not c_times:
        return None

    # Right-clip to forecast boundary
    if x_right is not None:
        try:
            x_right_aware = x_right if x_right.tzinfo is not None else x_right.replace(tzinfo=c_times[0].tzinfo)
            paired = [(t, v) for t, v in zip(c_times, c_temps) if t <= x_right_aware]
            if paired:
                c_times, c_temps = map(list, zip(*paired))
        except Exception:
            pass

    # Left-clip to chart left boundary
    if x_left is not None:
        try:
            paired = [(t, v) for t, v in zip(c_times, c_temps) if t >= x_left]
            if paired:
                c_times, c_temps = map(list, zip(*paired))
        except Exception:
            pass

    if not c_times:
        return None

    _off = offset if offset is not None else om_cmp_mgr._offset
    days  = int(_off.total_seconds() / 86400)
    hours = int((_off.total_seconds() % 86400) / 3600)
    if days and hours:
        off_label = f"{days}d {hours}h prior"
    elif days:
        off_label = f"{days}d prior"
    else:
        off_label = f"{hours}h prior"

    legend_label = f"OM Prior ({off_label})"

    _fill = ax.fill_between(c_times, c_temps, alpha=0.08, color=OM_COMPARE_COLOR, zorder=2)
    _line, = ax.plot(
        c_times, c_temps,
        color=OM_COMPARE_COLOR,
        linewidth=OM_COMPARE_LINEWIDTH,
        linestyle=OM_COMPARE_LINESTYLE,
        alpha=OM_COMPARE_ALPHA,
        zorder=3,
        label=legend_label,
    )

    c_min_val  = min(c_temps)
    c_max_val  = max(c_temps)
    c_min_time = c_times[c_temps.index(c_min_val)]
    c_max_time = c_times[c_temps.index(c_max_val)]

    def _xo(t):
        try:
            if x_left is None or x_right_effective is None:
                return 6
            span = (x_right_effective - x_left).total_seconds()
            pos  = (t - x_left).total_seconds()
            return -6 if span > 0 and pos / span > 0.70 else 6
        except Exception:
            return 6

    def _py(time, temp, default_y, flip_y):
        for pt, ptemp in (placed_anchors or []):
            if abs((time - pt).total_seconds()) / 3600 < 2.5 and abs(temp - ptemp) < 4.0:
                return flip_y
        return default_y

    _sc_min = ax.scatter(
        [c_min_time], [c_min_val],
        color=OM_COMPARE_COLOR, s=48, zorder=5,
        marker="v", edgecolors="#1a1d27", linewidths=0.8, alpha=OM_COMPARE_ALPHA,
    )
    _sc_max = ax.scatter(
        [c_max_time], [c_max_val],
        color=OM_COMPARE_COLOR, s=48, zorder=5,
        marker="^", edgecolors="#1a1d27", linewidths=0.8, alpha=OM_COMPARE_ALPHA,
    )
    _an_min = ax.annotate(
        f"{c_min_val}{sym}",
        (c_min_time, c_min_val),
        textcoords="offset points", xytext=(_xo(c_min_time), _py(c_min_time, c_min_val, -18, 18)),
        color=OM_COMPARE_COLOR, fontsize=7, fontfamily="Consolas",
        ha="right" if _xo(c_min_time) < 0 else "left",
    )
    _an_max = ax.annotate(
        f"{c_max_val}{sym}",
        (c_max_time, c_max_val),
        textcoords="offset points", xytext=(_xo(c_max_time), _py(c_max_time, c_max_val, 8, -18)),
        color=OM_COMPARE_COLOR, fontsize=7, fontfamily="Consolas",
        ha="right" if _xo(c_max_time) < 0 else "left",
    )

    if placed_anchors is not None:
        placed_anchors.extend([(c_min_time, c_min_val), (c_max_time, c_max_val)])

    return legend_label, [_fill, _line, _sc_min, _sc_max, _an_min, _an_max]