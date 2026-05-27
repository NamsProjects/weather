"""
nws_weather.py
==============
Production-quality module for pulling historical weather observations
and official CLI climatological reports from the National Weather Service (NWS) API.
"""

import re
import time
import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import pandas as pd

logger = logging.getLogger("nws_weather")
logger.addHandler(logging.NullHandler())

NWS_BASE = "https://api.weather.gov"
USER_AGENT = (
    "NWSWeatherExplorer/1.0 "
    "(personal weather tool; github.com/namki)"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/geo+json",
}
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0
REQUEST_TIMEOUT = 30


def c_to_f(c: float) -> float:
    return round(c * 9 / 5 + 32, 2)


def f_to_c(f: float) -> float:
    return round((f - 32) * 5 / 9, 2)


def _get(url: str, params: Optional[dict] = None) -> dict:
    last_status: int | str = "?"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            last_status = exc.response.status_code if exc.response is not None else "?"
            if last_status in (400, 404):
                raise RuntimeError(f"NWS API returned {last_status} for {url}.") from exc
            logger.warning("HTTP %s on attempt %d/%d for %s", last_status, attempt, MAX_RETRIES, url)
        except requests.exceptions.RequestException as exc:
            last_status = type(exc).__name__
            logger.warning("Request error on attempt %d/%d for %s: %s", attempt, MAX_RETRIES, url, exc)
        if attempt < MAX_RETRIES:
            sleep = RETRY_BACKOFF * (2 ** (attempt - 1))
            time.sleep(sleep)
    raise RuntimeError(
        f"NWS API returned HTTP {last_status} after {MAX_RETRIES} attempts — "
        f"endpoint: {url.split('api.weather.gov')[-1]}\n"
        f"If status is 403: update USER_AGENT in nws_weather.py with a real contact email.\n"
        f"If status is 500/503: the NWS observations API is temporarily down; try IEM source."
    )


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def get_station(lat: float, lon: float, max_distance_miles: float = 75.0) -> str:
    if not (-90 <= lat <= 90):
        raise ValueError(f"Latitude {lat} is out of range.")
    if not (-180 <= lon <= 180):
        raise ValueError(f"Longitude {lon} is out of range.")
    coord = f"{lat},{lon}"
    data = _get(f"{NWS_BASE}/points/{coord}/stations")
    features = data.get("features", [])
    if not features:
        raise RuntimeError(f"No observation stations found near ({lat}, {lon}).")

    # The NWS list is approximately sorted by distance. Accept the first
    # station that falls within the threshold (fast path for normal cases).
    # best_id/best_dist track the overall closest as a fallback for when
    # no station is within threshold (mis-sorted or remote queries).
    best_id = None
    best_dist = float("inf")
    no_coords_fallback = None  # first station without geometry, last resort

    for feature in features:
        props = feature.get("properties", {})
        station_id = props.get("stationIdentifier") or ""
        if not station_id:
            continue
        geo = feature.get("geometry") or {}
        coords = geo.get("coordinates")
        if coords and len(coords) >= 2:
            s_lon, s_lat = coords[0], coords[1]
            dist = _haversine_miles(lat, lon, s_lat, s_lon)
            if dist < best_dist:
                best_dist = dist
                best_id = station_id
            if dist <= max_distance_miles:
                logger.debug("Station %s is %.1f mi away — accepted", station_id, dist)
                return station_id
            logger.warning(
                "Station %s is %.1f mi away — skipping (threshold %.0f mi)",
                station_id, dist, max_distance_miles,
            )
        else:
            if no_coords_fallback is None:
                no_coords_fallback = station_id

    if best_id is None:
        if no_coords_fallback:
            logger.warning("No station with coordinates found; falling back to %s", no_coords_fallback)
            return no_coords_fallback
        raise RuntimeError("All candidate stations are missing a stationIdentifier.")

    logger.warning(
        "No station within %.0f mi of (%.4f, %.4f); using closest: %s (%.1f mi)",
        max_distance_miles, lat, lon, best_id, best_dist,
    )
    return best_id


def fetch_observations(station_id: str, start_time: datetime, end_time: datetime) -> list[dict]:
    if start_time.tzinfo is None or end_time.tzinfo is None:
        raise ValueError("start_time and end_time must be timezone-aware.")
    if start_time >= end_time:
        raise ValueError("start_time must be strictly before end_time.")
    url = f"{NWS_BASE}/stations/{station_id}/observations"
    params = {"start": start_time.isoformat(), "end": end_time.isoformat(), "limit": 500}
    all_features = []
    page = 1
    # On cursor pages, re-send `start` so NWS stops at the right time boundary.
    # Without it, NWS ignores start on paginated requests and returns data all
    # the way back to its retention limit (weeks of extra pages).
    cursor_params = {"start": start_time.isoformat()}
    while url:
        try:
            data = _get(url, params if page == 1 else cursor_params)
        except RuntimeError:
            if page > 1 and all_features:
                logger.warning(
                    "Pagination page %d timed out for %s; returning %d observations from prior pages.",
                    page, station_id, len(all_features),
                )
                break
            raise
        features = data.get("features", [])
        # NWS paginates newest→oldest. If the oldest obs on this page is already
        # before start_time, trim and stop — no earlier page can have in-range data.
        if features:
            oldest_ts = features[-1].get("properties", {}).get("timestamp", "")
            if oldest_ts and oldest_ts < start_time.isoformat():
                features = [
                    f for f in features
                    if (f.get("properties") or {}).get("timestamp", "") >= start_time.isoformat()
                ]
                all_features.extend(features)
                break
        all_features.extend(features)
        next_url = data.get("pagination", {}).get("next") or data.get("links", {}).get("next")
        url = next_url if next_url else None
        page += 1
    return all_features


def extract_temperature_data(observations: list[dict]) -> pd.DataFrame:
    records = []
    for feature in observations:
        props = feature.get("properties", {})
        ts_str = props.get("timestamp")
        if not ts_str:
            continue
        try:
            ts = pd.to_datetime(ts_str, utc=True)
        except Exception:
            continue
        temp_block = props.get("temperature", {}) or {}
        temp_c = temp_block.get("value")
        if temp_c is None:
            continue
        unit_code = temp_block.get("unitCode", "")
        temp_f = round(float(temp_c), 2) if "degF" in unit_code else c_to_f(float(temp_c))

        dew_block = props.get("dewpoint", {}) or {}
        dew_c = dew_block.get("value")
        dew_unit = dew_block.get("unitCode", "")
        if dew_c is not None:
            dew_f = round(float(dew_c), 2) if "degF" in dew_unit else c_to_f(float(dew_c))
        else:
            dew_f = None

        records.append({"time": ts, "temp": temp_f, "dewpoint": dew_f})
    if not records:
        return pd.DataFrame(columns=["temp", "dewpoint"])
    df = (
        pd.DataFrame(records)
        .drop_duplicates(subset="time")
        .set_index("time")
        .sort_index()
    )
    return df


def resample_data(df: pd.DataFrame, interval: str = "hourly") -> pd.DataFrame:
    if df.empty:
        return df
    interval_map = {"hourly": "1h", "30min": "30min", "1h": "1h", "60min": "1h"}
    freq = interval_map.get(interval.lower())
    if freq is None:
        raise ValueError(f"Unsupported interval '{interval}'.")
    resampled = (
        df["temp"].resample(freq).mean().ffill(limit=2).dropna().rename("temp").to_frame()
    )
    return resampled


def _compute_duration(df: pd.DataFrame, target_temp: float, tolerance: float = 1.0) -> dict:
    """
    Find how long the temperature stayed within `tolerance` degrees of
    `target_temp` in the longest unbroken run of readings.
    """
    if df.empty or "temp" not in df.columns:
        return {"duration_minutes": 0, "duration_str": "—", "run_start": None, "run_end": None}

    near = (df["temp"] - target_temp).abs() <= tolerance

    best_start_idx = None
    best_end_idx   = None
    best_minutes   = 0
    run_start_idx  = None

    idx_list = df.index.tolist()
    near_list = near.tolist()

    for i, (ts, is_near) in enumerate(zip(idx_list, near_list)):
        if is_near:
            if run_start_idx is None:
                run_start_idx = i
        else:
            if run_start_idx is not None:
                run_end_idx = i - 1
                run_minutes = int(
                    (idx_list[run_end_idx] - idx_list[run_start_idx]).total_seconds() / 60
                )
                if run_minutes > best_minutes:
                    best_minutes   = run_minutes
                    best_start_idx = run_start_idx
                    best_end_idx   = run_end_idx
                run_start_idx = None

    if run_start_idx is not None:
        run_end_idx = len(idx_list) - 1
        run_minutes = int(
            (idx_list[run_end_idx] - idx_list[run_start_idx]).total_seconds() / 60
        )
        if run_minutes > best_minutes:
            best_minutes   = run_minutes
            best_start_idx = run_start_idx
            best_end_idx   = run_end_idx

    if best_start_idx is None:
        closest_idx = (df["temp"] - target_temp).abs().idxmin()
        return {
            "duration_minutes": 0,
            "duration_str":     "< 1 reading",
            "run_start":        closest_idx.isoformat(),
            "run_end":          closest_idx.isoformat(),
        }

    def _fmt(minutes: int) -> str:
        if minutes == 0:
            return "< 1 min"
        h, m = divmod(minutes, 60)
        if h == 0:
            return f"{m} min"
        if m == 0:
            return f"{h} h"
        return f"{h} h {m} min"

    return {
        "duration_minutes": best_minutes,
        "duration_str":     _fmt(best_minutes),
        "run_start":        idx_list[best_start_idx].isoformat(),
        "run_end":          idx_list[best_end_idx].isoformat(),
    }


def get_summary_stats(df: pd.DataFrame, tolerance: float = 1.0) -> dict:
    if df.empty or "temp" not in df.columns:
        raise ValueError("DataFrame is empty or missing 'temp' column.")

    min_idx = df["temp"].idxmin()
    max_idx = df["temp"].idxmax()
    min_val = round(df["temp"].min(), 2)
    max_val = round(df["temp"].max(), 2)

    min_dur = _compute_duration(df, min_val, tolerance)
    max_dur = _compute_duration(df, max_val, tolerance)

    return {
        "min_temp":             min_val,
        "min_time":             min_idx.isoformat(),
        "min_duration_minutes": min_dur["duration_minutes"],
        "min_duration_str":     min_dur["duration_str"],
        "min_run_start":        min_dur["run_start"],
        "min_run_end":          min_dur["run_end"],

        "max_temp":             max_val,
        "max_time":             max_idx.isoformat(),
        "max_duration_minutes": max_dur["duration_minutes"],
        "max_duration_str":     max_dur["duration_str"],
        "max_run_start":        max_dur["run_start"],
        "max_run_end":          max_dur["run_end"],
    }


def _filter_metar_speci(rows: list[dict]) -> list[dict]:
    """
    Given a list of rows fetched with report_type=[1,2], filter down to only
    discrete METAR/SPECI observations — dropping the 1-minute feed echoes.

    Strategy: the 1-min ASOS feed repeats the last known value every minute,
    so consecutive rows with identical temperatures at 1-min intervals are
    echoes. A real METAR or SPECI is a discrete event where either:
      (a) the temperature changed from the previous row, OR
      (b) the minute is on a standard METAR schedule (xx:00, xx:20, xx:50/51-56)

    We keep a row if EITHER condition is true. This reliably catches:
      - Routine METARs (every 20-60 min depending on station)
      - SPECIs (irregular minute, temp change triggered them)
      - The first row in any run (anchor point)

    Rows are assumed to be sorted by time ascending.
    """
    if not rows:
        return rows

    # Standard METAR minute marks used by FAA ASOS
    METAR_MINUTES = {0, 20, 35, 50, 51, 52, 53, 54, 55, 56}

    kept = []
    prev_temp = None

    for i, row in enumerate(rows):
        try:
            ts   = pd.to_datetime(row["time"])
            mins = ts.minute
            temp = float(row["temp"])
        except Exception:
            continue

        on_schedule  = mins in METAR_MINUTES
        temp_changed = (prev_temp is None) or (abs(temp - prev_temp) >= 0.5)

        if on_schedule or temp_changed:
            kept.append(row)

        prev_temp = temp

    import sys as _sys
    print(f"[IEM DEBUG] _filter_metar_speci: {len(rows)} → {len(kept)} rows "
          f"after METAR/SPECI filter", file=_sys.stderr, flush=True)
    return kept


def fetch_iem_obs(
    station_id: str,
    start_time: datetime,
    end_time: datetime,
    report_type: Optional[list[int]] = None,
    metar_only: bool = False,
) -> list[dict]:
    """
    Fetch ASOS data from Iowa Environmental Mesonet for a station/window.
    Returns list of {"time": ISO str (UTC), "temp": float (°F)} dicts.
    station_id should be the 4-letter ICAO code (e.g. "KORD").

    Parameters
    ----------
    report_type : list[int], optional
        IEM report type filter.
        [1]    — METAR + SPECI only. In practice IEM returns M (missing) temps
                 for many stations on this feed, so use metar_only=True instead.
        [2]    — 1-minute ASOS feed only.
        [1, 2] — Both (default). Most complete dataset.

    metar_only : bool, optional
        When True, fetches with report_type=[1,2] to get actual temperature
        values, then filters client-side to keep only rows that look like
        discrete METAR/SPECI observations (on-schedule minutes or temp changes).
        This is more reliable than report_type=[1] alone because IEM's type-1
        feed often has missing (M) temperatures even for real METAR obs.
        Use this in High-Res tab when verifying against CLI official high/low.
    """
    import csv as _csv
    import sys as _sys

    # metar_only mode: fetch everything, filter after
    if metar_only:
        report_type = [1, 2]

    # Default to both report types for backwards compatibility
    if report_type is None:
        report_type = [1, 2]

    s = start_time.astimezone(timezone.utc)
    e = end_time.astimezone(timezone.utc)

    url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
    params = {
        "station":   station_id,
        "data":      ["tmpf", "dwpf"],
        "tz":        "UTC",
        "year1":     s.year,  "month1": s.month,  "day1": s.day,
        "hour1":     s.hour,  "minute1": s.minute,
        "year2":     e.year,  "month2": e.month,  "day2": e.day,
        "hour2":     e.hour,  "minute2": e.minute,
        "minutes":   1,
        "direct":    "yes",
        "report_type": report_type,
    }

    # IEM's CGI endpoint expects repeated keys for list params, e.g.:
    #   ?data=tmpf&data=dwpf&report_type=1&report_type=2
    # requests serializes lists as ?data=%5B%27tmpf%27...%5D which IEM rejects.
    # Build the query string manually so every value gets its own key.
    from urllib.parse import urlencode
    _scalar = {k: v for k, v in params.items() if k not in ("data", "report_type")}
    _qs = urlencode(_scalar)
    _qs += "".join(f"&data={d}" for d in params["data"])
    _qs += "".join(f"&report_type={r}" for r in params["report_type"])
    _full_url = f"{url}?{_qs}"

    print(f"[IEM DEBUG] report_type={report_type} metar_only={metar_only}",
          file=_sys.stderr, flush=True)
    print(f"[IEM DEBUG] full url: {_full_url}", file=_sys.stderr, flush=True)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(_full_url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.exceptions.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"IEM fetch failed after {MAX_RETRIES} attempts.") from exc
            logger.warning("IEM request error on attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
            time.sleep(RETRY_BACKOFF * (2 ** (attempt - 1)))

    print(f"[IEM DEBUG] HTTP {resp.status_code}  response length: {len(resp.text)} chars",
          file=_sys.stderr, flush=True)
    print(f"[IEM DEBUG] first 500 chars of response:\n{resp.text[:500]}",
          file=_sys.stderr, flush=True)

    lines = [ln for ln in resp.text.splitlines() if not ln.startswith("#")]
    reader = _csv.DictReader(lines)
    rows = []
    for row in reader:
        valid = (row.get("valid") or "").strip()
        tmpf  = (row.get("tmpf")  or "").strip()
        if not valid or tmpf in ("", "M", "None", "null"):
            continue
        dwpf = (row.get("dwpf") or "").strip()
        dew  = None if dwpf in ("", "M", "None", "null") else float(dwpf)
        try:
            ts = pd.to_datetime(valid).tz_localize("UTC")
            rows.append({"time": ts.isoformat(), "temp": float(tmpf), "dewpoint": dew})
        except Exception:
            continue

    print(f"[IEM DEBUG] parsed {len(rows)} raw rows before METAR filter",
          file=_sys.stderr, flush=True)

    if metar_only and rows:
        rows = _filter_metar_speci(rows)

    return rows


def iem_obs_to_dataframe(rows: list[dict]) -> pd.DataFrame:
    """Convert fetch_iem_obs output to a DataFrame indexed by UTC timestamp."""
    records = []
    for row in rows:
        try:
            ts = pd.to_datetime(row["time"], utc=True)
            records.append({"time": ts, "temp": float(row["temp"]), "dewpoint": row.get("dewpoint")})
        except Exception:
            continue
    if not records:
        return pd.DataFrame(columns=["temp", "dewpoint"])
    return (
        pd.DataFrame(records)
        .drop_duplicates(subset="time")
        .set_index("time")
        .sort_index()
    )


def _ensure_utc(dt_str: str) -> datetime:
    dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _convert_to_local(df: pd.DataFrame, tz_name: str) -> pd.DataFrame:
    try:
        local_tz = ZoneInfo(tz_name)
        df = df.copy()
        df.index = df.index.tz_convert(local_tz)
    except Exception as exc:
        logger.warning("Could not convert to timezone '%s' (%s). Keeping UTC.", tz_name, exc)
    return df


def get_cli_report(
    location_code: str,
    product_index: int = 0,
    list_only: bool = False,
) -> dict:
    """Fetch and parse a NWS Daily Climatological Report (CLI).

    Parameters
    ----------
    location_code : str
        Three-letter location code, e.g. "NYC".
    product_index : int
        0 = most recent (default), 1 = second-most-recent, etc.
    list_only : bool
        If True, return just the list of available products without fetching
        the full text. Useful for populating a picker UI.
    """
    code = location_code.upper().strip()
    if not code:
        raise ValueError("location_code cannot be empty.")

    list_url = f"{NWS_BASE}/products/types/CLI/locations/{code}"
    data = _get(list_url)
    products = data.get("@graph", [])
    if not products:
        raise RuntimeError(f"No CLI products found for location code '{code}'.")

    # Build a lightweight summary list for the picker
    product_list = []
    for p in products:
        pid = p.get("id") or p.get("productId", "")
        issued_str = p.get("issuanceTime", "")
        label = issued_str  # fallback
        try:
            dt = datetime.fromisoformat(issued_str).astimezone(timezone.utc)
            label = dt.strftime("%b %d, %Y  %H:%M UTC")
        except Exception:
            pass
        product_list.append({"id": pid, "issuanceTime": issued_str, "label": label})

    if list_only:
        return {"location_code": code, "products": product_list}

    if product_index >= len(products):
        raise ValueError(
            f"product_index {product_index} out of range — "
            f"only {len(products)} products available."
        )

    selected = products[product_index]
    prod_id = selected.get("id") or selected.get("productId", "")
    issued_str = selected.get("issuanceTime", "")
    if not prod_id:
        raise RuntimeError(f"CLI product listing for '{code}' returned no product ID.")

    product_data = _get(f"{NWS_BASE}/products/{prod_id}")
    raw_text = product_data.get("productText", "")

    issued_at = None
    if issued_str:
        try:
            issued_dt = datetime.fromisoformat(issued_str).astimezone(timezone.utc)
            issued_at = issued_dt.isoformat()
        except Exception:
            pass

    def _find(pattern, text, group=1, default=None):
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(group).strip() if m else default

    # NWS CLI time formats vary by office:
    #   NYC style (no colon):  "MAXIMUM    80   1220 PM"  or  "MINIMUM    59   510 AM"
    #   Other offices (colon): "MAXIMUM    61   12:10 AM" or  "MINIMUM    47   7:01 AM"
    # Pattern: 1-2 digit hour, optional colon, exactly 2 digit minutes, AM/PM.
    _time_pat = r"\d{1,2}:?\d{2}\s*[AP]M"

    valid_date = _find(r"CLIMATE\s+SUMMARY\s+FOR\s+([A-Z]+ \d{1,2} \d{4})", raw_text)

    high_str  = _find(rf"MAXIMUM\s+(\d+)\s+{_time_pat}", raw_text)
    high_time = _find(rf"MAXIMUM\s+\d+\s+({_time_pat})", raw_text)
    high_temp = int(high_str) if high_str else None

    low_str   = _find(rf"MINIMUM\s+(\d+)\s+{_time_pat}", raw_text)
    low_time  = _find(rf"MINIMUM\s+\d+\s+({_time_pat})", raw_text)
    low_temp  = int(low_str) if low_str else None

    precip    = _find(r"(?:YESTERDAY|TODAY)\s+([\d.]+|T)\b", raw_text)

    return {
        "location_code": code,
        "raw_text":      raw_text,
        "issued_at":     issued_at,
        "valid_date":    valid_date,
        "high_temp":     high_temp,
        "high_time":     high_time,
        "low_temp":      low_temp,
        "low_time":      low_time,
        "precip":        precip,
        "product_list":  product_list,
    }

def _empty_result(station_id: str, interval: str, tz: Optional[str]) -> dict:
    return {
        "station": station_id, "interval": interval,
        "timezone": tz or "UTC", "data": [],
        "min_temp": None, "min_time": None,
        "min_duration_minutes": None, "min_duration_str": None,
        "min_run_start": None, "min_run_end": None,
        "max_temp": None, "max_time": None,
        "max_duration_minutes": None, "max_duration_str": None,
        "max_run_start": None, "max_run_end": None,
    }


def get_temperature_summary(
    lat: float,
    lon: float,
    start: str,
    end: str,
    interval: str = "hourly",
    local_tz: Optional[str] = None,
    duration_tolerance: float = 1.0,
    source: str = "nws",
    station_id: Optional[str] = None,
) -> dict:
    """High-level convenience function. source: 'nws' or 'iem'.

    station_id : str, optional
        When provided the /points lat-lon lookup is skipped entirely and
        observations are fetched directly from this station.  Pass the known
        CLI station (e.g. 'KNYC') to guarantee the result matches the CLI
        report rather than whatever station the NWS API happens to return for
        those coordinates.
    """
    start_dt = _ensure_utc(start)
    end_dt   = _ensure_utc(end)

    if station_id is None:
        station_id = get_station(lat, lon)
    if source == "iem":
        # Main chart tab uses both report types for maximum coverage
        raw_rows = fetch_iem_obs(station_id, start_dt, end_dt, report_type=[1, 2])
        df = iem_obs_to_dataframe(raw_rows)
    else:
        raw = fetch_observations(station_id, start_dt, end_dt)
        df  = extract_temperature_data(raw)

    if df.empty:
        return _empty_result(station_id, interval, local_tz)

    # ── PRE-RESAMPLE TRIM ─────────────────────────────────────────────────
    # Must happen BEFORE resample() so that resampled bins can never be
    # anchored at a timestamp earlier than start_dt.  Without this, a bin
    # whose label falls before start (e.g. "May 04 21:00") is created from
    # observations that straddle the query boundary and survives the
    # post-resample trim, causing the chart to show data from the prior day.
    try:
        tz_obj_pre   = ZoneInfo(local_tz) if local_tz else timezone.utc
        start_pre    = start_dt.astimezone(tz_obj_pre)
        end_pre      = end_dt.astimezone(tz_obj_pre)
        df = df.loc[(df.index >= start_pre) & (df.index <= end_pre)]
    except Exception as exc:
        logger.warning("Pre-resample trim failed: %s", exc)

    if df.empty:
        return _empty_result(station_id, interval, local_tz)

    df = resample_data(df, interval)

    tz_label = "UTC"
    if local_tz:
        df = _convert_to_local(df, local_tz)
        tz_label = local_tz

    # ── POST-RESAMPLE TRIM (belt-and-suspenders) ──────────────────────────
    try:
        tz_obj      = ZoneInfo(local_tz) if local_tz else timezone.utc
        start_local = _ensure_utc(start).astimezone(tz_obj)
        end_local   = _ensure_utc(end).astimezone(tz_obj)
        df = df.loc[(df.index >= start_local) & (df.index <= end_local)]
    except Exception as exc:
        logger.warning("Could not trim to local window: %s", exc)

    if df.empty:
        return _empty_result(station_id, interval, tz_label)

    stats = get_summary_stats(df, tolerance=duration_tolerance)

    # Convert all times back to UTC for consistent browser-side timezone handling
    # (Open-Meteo forecast also returns UTC times)
    data_rows = [
        {"time": ts.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), "temp": round(row["temp"], 2)}
        for ts, row in df.iterrows()
    ]

    return {
        "station":  station_id,
        "interval": interval,
        "timezone": tz_label,
        "data":     data_rows,
        **stats,
    }