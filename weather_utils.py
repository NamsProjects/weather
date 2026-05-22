"""
weather_utils.py
================
Pure helper functions for the NWS Weather web server.

Covers:
  - CLI reporting-window calculation
  - Filtering observation rows to CLI windows
  - Computing min/max stats from raw rows
  - Time-string formatting
  - City / timezone / CLI-location lookup tables
  - CLI_STATION_COORDS — official ASOS station lat/lon used for CLI reporting
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# ── Lookup tables ─────────────────────────────────────────────────────────────
CITIES: dict[str, tuple[float, float, str]] = {
    "New York City":  (40.743, -74.032,   "America/New_York"),
    "Chicago":        (41.7868, -87.7522,  "America/Chicago"),
    "Los Angeles":    (34.052, -118.244,  "America/Los_Angeles"),
    "Houston":        (29.760, -95.370,   "America/Chicago"),
    "Miami":          (25.774, -80.195,   "America/New_York"),
    "Seattle":        (47.608, -122.335,  "America/Los_Angeles"),
    "Denver":         (39.739, -104.984,  "America/Denver"),
    "Boston":         (42.360, -71.059,   "America/New_York"),
    "Atlanta":        (33.749, -84.388,   "America/New_York"),
    "Phoenix":        (33.448, -112.074,  "America/Phoenix"),
    "Dallas":         (32.779, -96.809,   "America/Chicago"),
    "Philadelphia":   (39.952, -75.165,   "America/New_York"),
    "San Francisco":  (37.773, -122.432,  "America/Los_Angeles"),
    "Minneapolis":    (44.979, -93.265,   "America/Chicago"),
    "Detroit":        (42.332, -83.046,   "America/Detroit"),
    "Las Vegas":      (36.0840, -115.1537, "America/Los_Angeles"),
    "Oklahoma City":  (35.3931,  -97.6011, "America/Chicago"),
    "Austin":         (30.1975,  -97.6664, "America/Chicago"),
    "Washington DC":  (38.8521,  -77.0377, "America/New_York"),
    "San Antonio":    (29.5337,  -98.4698, "America/Chicago"),
    "New Orleans":    (29.9934,  -90.2580, "America/Chicago"),
}

CLI_LOCATIONS: dict[str, str] = {
    "New York City":  "NYC",
    "Chicago":        "MDW",
    "Los Angeles":    "LAX",
    "Houston":        "HOU",
    "Miami":          "MIA",
    "Seattle":        "SEA",
    "Denver":         "DEN",
    "Boston":         "BOS",
    "Atlanta":        "ATL",
    "Phoenix":        "PHX",
    "Dallas":         "DFW",
    "Philadelphia":   "PHL",
    "San Francisco":  "SFO",
    "Minneapolis":    "MSP",
    "Detroit":        "DTW",
    "Las Vegas":      "LAS",
    "Oklahoma City":  "OKC",
    "Austin":         "AUS",
    "Washington DC":  "DCA",
    "San Antonio":    "SAT",
    "New Orleans":    "MSY",
}

# ── Official CLI reporting station coordinates ────────────────────────────────
# Each entry is (lat, lon) for the primary ASOS station that the NWS uses
# to generate the city's CLI product.  These are fed to get_temperature_summary()
# and ForecastManager.auto_fetch() so the /points lookup naturally resolves to
# the correct observation station instead of whatever happens to be nearest to
# the city-centre coordinate in CITIES above.
#
# Sources: NWS CLI product headers + FAA airport coordinates.
CLI_STATION_COORDS: dict[str, tuple[float, float]] = {
    # City             lat        lon
    "New York City":  (40.7789,  -73.9692),   # KNYC  Central Park
    "Chicago":        (41.7868,  -87.7522),   # KMDW  Midway Intl
    "Los Angeles":    (33.9425, -118.4081),   # KLAX  Los Angeles Intl
    "Houston":        (29.6454,  -95.2789),   # KHOU  William P. Hobby
    "Miami":          (25.7959,  -80.2870),   # KMIA  Miami Intl
    "Seattle":        (47.4445, -122.3139),   # KSEA  Seattle-Tacoma Intl
    "Denver":         (39.8561, -104.6737),   # KDEN  Denver Intl
    "Boston":         (42.3606,  -71.0097),   # KBOS  Logan Intl
    "Atlanta":        (33.6367,  -84.4281),   # KATL  Hartsfield-Jackson
    "Phoenix":        (33.4373, -112.0078),   # KPHX  Phoenix Sky Harbor
    "Dallas":         (32.8998,  -97.0403),   # KDFW  Dallas/Fort Worth Intl
    "Philadelphia":   (39.8721,  -75.2411),   # KPHL  Philadelphia Intl
    "San Francisco":  (37.6213, -122.3790),   # KSFO  San Francisco Intl
    "Minneapolis":    (44.8848,  -93.2223),   # KMSP  Minneapolis-St Paul Intl
    "Detroit":        (42.2124,  -83.3534),   # KDTW  Detroit Metropolitan Wayne County
    "Las Vegas":      (36.0840, -115.1537),   # KLAS  Harry Reid Intl
    "Oklahoma City":  (35.3931,  -97.6011),   # KOKC  Will Rogers World
    "Austin":         (30.1975,  -97.6664),   # KAUS  Austin-Bergstrom Intl
    "Washington DC":  (38.8521,  -77.0377),   # KDCA  Reagan National
    "San Antonio":    (29.5337,  -98.4698),   # KSAT  San Antonio Intl
    "New Orleans":    (29.9934,  -90.2580),   # KMSY  Louis Armstrong Intl
}

# ── Official CLI reporting station ICAO IDs ──────────────────────────────────
# These are the exact stations whose observations the NWS uses to produce each
# city's CLI product.  Passed directly to fetch_observations() so we never
# rely on the /points lat-lon lookup accidentally picking a different station.
CLI_STATION_IDS: dict[str, str] = {
    "New York City":  "KNYC",   # Central Park
    "Chicago":        "KMDW",   # Midway Intl
    "Los Angeles":    "KLAX",   # Los Angeles Intl
    "Houston":        "KHOU",   # William P. Hobby
    "Miami":          "KMIA",   # Miami Intl
    "Seattle":        "KSEA",   # Seattle-Tacoma Intl
    "Denver":         "KDEN",   # Denver Intl
    "Boston":         "KBOS",   # Logan Intl
    "Atlanta":        "KATL",   # Hartsfield-Jackson
    "Phoenix":        "KPHX",   # Phoenix Sky Harbor
    "Dallas":         "KDFW",   # Dallas/Fort Worth Intl
    "Philadelphia":   "KPHL",   # Philadelphia Intl
    "San Francisco":  "KSFO",   # San Francisco Intl
    "Minneapolis":    "KMSP",   # Minneapolis-St Paul Intl
    "Detroit":        "KDTW",   # Detroit Metropolitan Wayne County
    "Las Vegas":      "KLAS",   # Harry Reid Intl
    "Oklahoma City":  "KOKC",   # Will Rogers World
    "Austin":         "KAUS",   # Austin-Bergstrom Intl
    "Washington DC":  "KDCA",   # Reagan National
    "San Antonio":    "KSAT",   # San Antonio Intl
    "New Orleans":    "KMSY",   # Louis Armstrong Intl
}

TIMEZONES: list[str] = [
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Phoenix", "America/Anchorage",
    "Pacific/Honolulu", "UTC",
]


# ── CLI window helpers ────────────────────────────────────────────────────────

def compute_cli_windows(
    times: list[datetime],
    tz_name: str,
    query_start_local: Optional[datetime] = None,
) -> list[tuple[datetime, datetime]]:
    """
    Return a list of (window_start, window_end) datetime pairs representing
    the CLI reporting windows that cover the span of *times*.

    During DST the CLI window is 01:00 LST -> 00:59 LST the next day
    (i.e. exactly 24 hours, anchored to 1 AM local standard time).
    During standard time it is 00:00 -> 23:59 (calendar day).

    Windows are non-overlapping.  Each window is identified by the calendar
    date on which it *opens* (the "anchor date").  A window for anchor date D
    runs from D 01:00 (DST) or D 00:00 (STD) to the moment before the next
    window opens.  We enumerate only as many anchor dates as are needed to
    cover every timestamp in *times*.

    Parameters
    ----------
    times : list[datetime]
        Observation timestamps (any tz).
    tz_name : str
        IANA timezone name, e.g. "America/Chicago".
    query_start_local : datetime, optional
        The user's intended query start expressed in local time.
        Any CLI window whose anchor date falls strictly before
        ``query_start_local.date()`` is suppressed.  Pass this whenever
        the raw query start is a UTC string that converts to a prior local
        calendar day (e.g. UTC midnight = 7 PM CDT the day before).
    """
    if not times or not tz_name:
        return []
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)

        # ── Determine the earliest local date we allow ────────────────────
        min_anchor_date = None
        if query_start_local is not None:
            try:
                qsl = query_start_local
                if qsl.tzinfo is None:
                    qsl = qsl.replace(tzinfo=tz)
                else:
                    qsl = qsl.astimezone(tz)
                # The minimum anchor date is simply the local calendar date
                # of the query start.  Any window anchored to an earlier
                # date is suppressed, even if the data contains readings
                # from that prior evening (due to UTC-vs-local offset).
                min_anchor_date = qsl.date()
            except Exception:
                pass

        # Convert all times to local tz
        local_times = []
        for t in times:
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            local_times.append(t.astimezone(tz))

        # Determine the anchor date for each timestamp.
        # During DST, a timestamp between 00:00 and 00:59 belongs to the
        # *previous* calendar day's window (which opened at 01:00 yesterday).
        def anchor_date(lt: datetime):
            is_dst = lt.dst() != timedelta(0)
            if is_dst and lt.hour < 1:
                return (lt - timedelta(days=1)).date()
            return lt.date()

        anchor_dates = sorted(set(anchor_date(lt) for lt in local_times))

        # ── Suppress anchor dates before the query's local start date ─────
        if min_anchor_date is not None:
            anchor_dates = [d for d in anchor_dates if d >= min_anchor_date]

        windows: list[tuple[datetime, datetime]] = []
        for date in anchor_dates:
            # Check DST at noon on the anchor date (stable proxy)
            noon = datetime(date.year, date.month, date.day, 12, 0, 0, tzinfo=tz)
            is_dst = noon.dst() != timedelta(0)

            if is_dst:
                ws = datetime(date.year, date.month, date.day, 1, 0, 0, tzinfo=tz)
                nd = date + timedelta(days=1)
                # Window ends at 00:59:59 of the next calendar day
                we = datetime(nd.year, nd.month, nd.day, 0, 59, 59, tzinfo=tz)
            else:
                ws = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=tz)
                we = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=tz)

            windows.append((ws, we))
        return windows
    except Exception as exc:
        print(f"Error calculating CLI windows: {exc}")
        return []


def forecast_cap_utc(end_dt: datetime, tz_name: str) -> datetime:
    """
    Return the UTC-aware forecast cap for the CLI window that *end_dt* belongs to.

    DST cities  → 1 AM local (CLI window opens at 1 AM, cap matches)
    Non-DST cities → midnight local (window is 00:00-23:59, cap at next-day 00:00)

    Boundary semantics: when *end_dt* lands exactly on the cap hour
    (e.g. 01:00 local during DST), it is treated as the cap of the
    *previous* CLI day, not the start of the next — so this function
    is idempotent on values that are already a cap.

    DST status is evaluated at noon on end_dt's local date (stable — no
    ambiguous-hour risk).
    """
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(tz_name)
        if end_dt.tzinfo is None:
            end_local = end_dt.replace(tzinfo=timezone.utc).astimezone(tz)
        else:
            end_local = end_dt.astimezone(tz)
        noon = end_local.replace(hour=12, minute=0, second=0, microsecond=0)
        is_dst = noon.dst() != timedelta(0)
        cap_hour = 1 if is_dst else 0
        # If end_local is strictly past the cap_hour on its date, the cap is
        # the *next* calendar day's cap_hour. If it's at or before cap_hour
        # (including exactly cap_hour:00:00.000000), the cap is the same date.
        if (end_local.hour, end_local.minute, end_local.second, end_local.microsecond) > (cap_hour, 0, 0, 0):
            cap_date = end_local.date() + timedelta(days=1)
        else:
            cap_date = end_local.date()
        cap_local = datetime(cap_date.year, cap_date.month, cap_date.day, cap_hour, 0, 0, tzinfo=tz)
        return cap_local.astimezone(timezone.utc)
    except Exception:
        # Safe fallback: 1 AM UTC next day
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        return (
            end_dt.replace(hour=1, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        ).astimezone(timezone.utc)


def city_observes_dst(tz_name: str) -> bool:
    """Return True if the timezone observes DST (checked on a summer date)."""
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(tz_name)
        summer = datetime(2026, 7, 1, 12, 0, 0, tzinfo=tz)
        return summer.dst() != timedelta(0)
    except Exception:
        return True


def filter_to_cli_windows(
    data_rows: list[dict],
    tz_name: str,
    query_start_local: Optional[datetime] = None,
) -> tuple[list[dict], list[tuple[datetime, datetime]]]:
    """
    Given a list of ``{"time": iso_str, "temp": float}`` rows, return only the
    rows that fall inside a CLI reporting window.  Also returns the windows list.
    """
    if not data_rows:
        return data_rows, []

    times = [datetime.fromisoformat(r["time"]) for r in data_rows]
    windows = compute_cli_windows(times, tz_name, query_start_local=query_start_local)
    if not windows:
        return data_rows, windows

    def in_window(t: datetime) -> bool:
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return any(ws <= t <= we for ws, we in windows)

    filtered = [r for r, t in zip(data_rows, times) if in_window(t)]
    return filtered, windows


# ── Stats computation ─────────────────────────────────────────────────────────

def _fmt_duration(minutes: int) -> str:
    """Convert an integer number of minutes to a human-readable string."""
    if minutes == 0:
        return "< 1 min"
    h, m = divmod(minutes, 60)
    if h == 0:
        return f"{m} min"
    if m == 0:
        return f"{h} h"
    return f"{h} h {m} min"


def _compute_held_duration(
    rows: list[dict],
    target: float,
    tolerance: float,
    fallback_row: dict,
) -> dict:
    """
    Find the longest continuous run of readings within *tolerance* of *target*.
    Returns a dict with duration_minutes, duration_str, run_start, run_end.
    """
    near = [abs(r["temp"] - target) <= tolerance for r in rows]
    best_start = best_end = None
    best_min = 0
    run_start = None

    for i, is_near in enumerate(near):
        if is_near:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                t0 = datetime.fromisoformat(rows[run_start]["time"])
                t1 = datetime.fromisoformat(rows[i - 1]["time"])
                mins = int((t1 - t0).total_seconds() / 60)
                if mins > best_min:
                    best_min, best_start, best_end = mins, run_start, i - 1
                run_start = None

    if run_start is not None:
        t0 = datetime.fromisoformat(rows[run_start]["time"])
        t1 = datetime.fromisoformat(rows[-1]["time"])
        mins = int((t1 - t0).total_seconds() / 60)
        if mins > best_min:
            best_min, best_start, best_end = mins, run_start, len(rows) - 1

    if best_start is None:
        return {
            "duration_minutes": 0,
            "duration_str": "< 1 reading",
            "run_start": fallback_row["time"],
            "run_end": fallback_row["time"],
        }
    return {
        "duration_minutes": best_min,
        "duration_str": _fmt_duration(best_min),
        "run_start": rows[best_start]["time"],
        "run_end": rows[best_end]["time"],
    }


def compute_stats_from_rows(
    data_rows: list[dict],
    tolerance: float = 1.0,
) -> dict | None:
    """
    Compute min/max stats from a list of ``{"time": iso_str, "temp": float}`` rows.

    Returns a stats dict whose shape matches the one produced by
    ``nws_weather.get_temperature_summary``, or *None* if *data_rows* is empty.
    """
    if not data_rows:
        return None

    min_row = min(data_rows, key=lambda r: r["temp"])
    max_row = max(data_rows, key=lambda r: r["temp"])
    min_val = min_row["temp"]
    max_val = max_row["temp"]

    min_dur = _compute_held_duration(data_rows, min_val, tolerance, min_row)
    max_dur = _compute_held_duration(data_rows, max_val, tolerance, max_row)

    return {
        "min_temp": min_val,
        "min_time": min_row["time"],
        "min_duration_minutes": min_dur["duration_minutes"],
        "min_duration_str": min_dur["duration_str"],
        "min_run_start": min_dur["run_start"],
        "min_run_end": min_dur["run_end"],
        "max_temp": max_val,
        "max_time": max_row["time"],
        "max_duration_minutes": max_dur["duration_minutes"],
        "max_duration_str": max_dur["duration_str"],
        "max_run_start": max_dur["run_start"],
        "max_run_end": max_dur["run_end"],
    }


def compute_stats_per_cli_window(
    data_rows: list[dict],
    tz_name: str,
    tolerance: float = 1.0,
    query_start_local: Optional[datetime] = None,
) -> list[dict]:
    """
    Compute min/max stats separately for each CLI reporting window
    that overlaps with data_rows.

    Returns a list of dicts, one per CLI window, each shaped like:
    {
        "label":     "May 04 CLI",
        "win_start": iso_str,
        "win_end":   iso_str,
        "min_temp":  float,
        "min_time":  iso_str,
        "min_duration_str": str,
        "min_run_start": iso_str,
        "min_run_end":   iso_str,
        "max_temp":  float,
        "max_time":  iso_str,
        "max_duration_str": str,
        "max_run_start": iso_str,
        "max_run_end":   iso_str,
    }
    Only windows that contain at least one data row are included.

    Parameters
    ----------
    query_start_local : datetime, optional
        Passed through to compute_cli_windows() to suppress windows whose
        anchor date precedes the user's intended local start date.
    """
    if not data_rows or not tz_name:
        return []

    times = [datetime.fromisoformat(r["time"]) for r in data_rows]
    windows = compute_cli_windows(
        times, tz_name, query_start_local=query_start_local
    )
    if not windows:
        return []

    results = []
    for ws, we in windows:
        window_rows = [
            r for r, t in zip(data_rows, times)
            if ws <= t <= we
        ]
        if not window_rows:
            continue
        stats = compute_stats_from_rows(window_rows, tolerance)
        if stats is None:
            continue
        # Human-readable label using the window start date
        try:
            label = ws.strftime("CLI  %b %d")
        except Exception:
            label = "CLI window"
        stats["label"]     = label
        stats["win_start"] = ws.isoformat()
        stats["win_end"]   = we.isoformat()
        results.append(stats)

    return results


# ── Formatting ────────────────────────────────────────────────────────────────

def fmt_time(iso_str: str) -> str:
    """Format an ISO datetime string as ``'Mon DD  HH:MM'``."""
    if not iso_str:
        return ""
    try:
        return datetime.fromisoformat(iso_str).strftime("%b %d  %H:%M")
    except Exception:
        return iso_str