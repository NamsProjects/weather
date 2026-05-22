"""
server.py
=========
Flask backend for the NWS Weather Explorer web UI.
Wraps the existing data modules as REST endpoints.

Install:  pip install flask
Run with: python server.py
Open:     http://localhost:5000
"""

import json
import os
import queue
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context
from nws_weather import get_temperature_summary, get_cli_report, f_to_c
from weather_utils import CITIES, CLI_LOCATIONS, CLI_STATION_COORDS, CLI_STATION_IDS
from kalshi_markets import get_city_contracts


def _convert_browser_to_city(dt_str: str, browser_tz: str, city_tz: str) -> str:
    """Convert naive datetime (in browser's timezone) to city's timezone.

    Browser sends datetime-local strings interpreted in the browser's system timezone.
    We must convert these to the city's timezone for proper API calls.

    Example:
      dt_str = "2026-05-12T14:00" (user input, interpreted in browser's tz)
      browser_tz = "America/New_York" (where the user's browser is)
      city_tz = "America/Los_Angeles" (selected city)

      Step 1: Parse as naive → datetime(2026, 5, 12, 14, 0)
      Step 2: Attach browser tz → 2026-05-12T14:00:00-04:00 (EDT)
      Step 3: Convert to UTC → 2026-05-12T18:00:00+00:00 UTC
      Step 4: Convert to city tz → 2026-05-12T11:00:00-07:00 (PDT)
      Result: "2026-05-12T11:00:00-07:00" (correct LA local time)
    """
    try:
        # Parse as naive
        dt_naive = datetime.fromisoformat(dt_str)
        if dt_naive.tzinfo is not None:
            # Already has tz info, return as-is
            return dt_str

        # Interpret as browser's local time
        browser_tz_obj = ZoneInfo(browser_tz)
        dt_browser = dt_naive.replace(tzinfo=browser_tz_obj)

        # Convert to UTC
        dt_utc = dt_browser.astimezone(timezone.utc)

        # Convert to city's timezone
        city_tz_obj = ZoneInfo(city_tz)
        dt_city = dt_utc.astimezone(city_tz_obj)

        return dt_city.isoformat()
    except Exception as exc:
        # Fallback: just attach city tz (old behavior)
        print(f"[TZ CONVERT ERROR] {exc}  dt_str={dt_str}  browser_tz={browser_tz}  city_tz={city_tz}", file=sys.stderr, flush=True)
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(city_tz))
        return dt.isoformat()


def _localize_naive(dt_str: str, tz_name: str) -> str:
    """DEPRECATED: Use _convert_browser_to_city instead.

    This old function incorrectly attaches timezone to naive datetime strings
    without accounting for the browser's local timezone.
    """
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.isoformat()

app = Flask(__name__, static_folder="static", static_url_path="")


@app.after_request
def no_cache(response):
    if request.path.startswith("/api"):
        return response
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/app")
def app_ui():
    return send_from_directory("static", "app.html")


# ── City list ─────────────────────────────────────────────────────────────────

@app.route("/api/cities")
def api_cities():
    from weather_utils import city_observes_dst
    return jsonify([
        {"name": name, "tz": tz, "dst": city_observes_dst(tz)}
        for name, (_, _, tz) in CITIES.items()
    ])


# ── Observations ──────────────────────────────────────────────────────────────

@app.route("/api/observations", methods=["POST"])
def api_observations():
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    city     = body.get("city", "New York City")
    start    = body["start"]
    end      = body["end"]
    units    = body.get("units", "F")
    interval = body.get("interval", "hourly")
    source   = body.get("source", "nws")
    tol      = float(body.get("tol", 1.0))
    browser_tz = body.get("browser_tz", "UTC")

    if city not in CITIES:
        return jsonify({"error": f"Unknown city: {city}"}), 400

    lat, lon, tz = CITIES[city]
    if city in CLI_STATION_COORDS:
        lat, lon = CLI_STATION_COORDS[city]
    station_id = CLI_STATION_IDS.get(city)

    start = _convert_browser_to_city(start, browser_tz, tz)
    end   = _convert_browser_to_city(end, browser_tz, tz)

    try:
        result = get_temperature_summary(
            lat=lat, lon=lon,
            start=start, end=end,
            interval=interval, local_tz=tz,
            duration_tolerance=tol,
            source=source,
            station_id=station_id,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    if units == "C":
        for row in result["data"]:
            row["temp"] = f_to_c(row["temp"])
        for key in ("min_temp", "max_temp"):
            if result[key] is not None:
                result[key] = f_to_c(result[key])

    # Compute CLI window boundaries for chart shading
    try:
        from weather_utils import compute_cli_windows
        from zoneinfo import ZoneInfo
        times = [datetime.fromisoformat(r["time"].replace("Z", "+00:00")) for r in result["data"]]
        # Treat the user's start string as city-local time to suppress prior-day windows
        start_local = datetime.fromisoformat(start).astimezone(ZoneInfo(tz))
        windows = compute_cli_windows(times, tz, query_start_local=start_local)
        result["cli_windows"] = [
            {"start": ws.isoformat(), "end": we.isoformat()}
            for ws, we in windows
        ]
        print(f"[API /observations] {city}: {len(windows)} CLI windows computed", file=sys.stderr, flush=True)
        for i, (ws, we) in enumerate(windows):
            print(f"  [{i}] {ws.isoformat()} to {we.isoformat()}", file=sys.stderr, flush=True)
        result["query_start"] = start_local.isoformat()
    except Exception as exc:
        print(f"[CLI WINDOW ERROR] {exc}", file=sys.stderr, flush=True)
        result["cli_windows"] = []
        # Always set query_start even if CLI windows fail
        try:
            start_local = datetime.fromisoformat(start).astimezone(ZoneInfo(tz))
            result["query_start"] = start_local.isoformat()
        except Exception as e2:
            print(f"[QUERY START ERROR] {e2}", file=sys.stderr, flush=True)

    return jsonify(result)


# ── CLI report ────────────────────────────────────────────────────────────────

@app.route("/api/cli", methods=["POST"])
def api_cli():
    body     = request.get_json(force=True)
    city     = body.get("city", "New York City")
    cli_code = CLI_LOCATIONS.get(city)
    if not cli_code:
        return jsonify({"error": f"No CLI location code for: {city}"}), 400

    try:
        result = get_cli_report(cli_code, product_index=0)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@app.route("/api/cli/list", methods=["POST"])
def api_cli_list():
    """Return list of available CLI report issuance times for picker UI."""
    body     = request.get_json(force=True)
    city     = body.get("city", "New York City")
    cli_code = CLI_LOCATIONS.get(city)
    if not cli_code:
        return jsonify({"error": f"No CLI location code for: {city}"}), 400

    try:
        result = get_cli_report(cli_code, list_only=True)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@app.route("/api/cli/select", methods=["POST"])
def api_cli_select():
    """Fetch a specific CLI report by index from the product list."""
    body     = request.get_json(force=True)
    city     = body.get("city", "New York City")
    index    = int(body.get("index", 0))
    cli_code = CLI_LOCATIONS.get(city)
    if not cli_code:
        return jsonify({"error": f"No CLI location code for: {city}"}), 400

    try:
        result = get_cli_report(cli_code, product_index=index)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


# ── NWS gridpoint hourly forecast ────────────────────────────────────────────

@app.route("/api/forecast", methods=["POST"])
def api_forecast():
    import sys
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    city  = body.get("city", "New York City")
    start = body["start"]
    end   = body["end"]
    units = body.get("units", "F")
    browser_tz = body.get("browser_tz", "UTC")

    if city not in CITIES:
        return jsonify({"error": f"Unknown city: {city}"}), 400

    lat, lon, tz = CITIES[city]
    if city in CLI_STATION_COORDS:
        lat, lon = CLI_STATION_COORDS[city]

    try:
        from current_forecasts import ForecastManager
        from zoneinfo import ZoneInfo
        # Convert browser-local times to city timezone
        start = _convert_browser_to_city(start, browser_tz, tz)
        end = _convert_browser_to_city(end, browser_tz, tz)
        tz_obj  = ZoneInfo(tz)
        end_dt  = datetime.fromisoformat(end)
        start_dt = datetime.fromisoformat(start)

        print(
            f"[FORECAST DEBUG] city={city}  lat={lat}  lon={lon}  tz={tz}  "
            f"start={start}  end={end}  start_dt={start_dt.isoformat()}  "
            f"end_dt={end_dt.isoformat()}  units={units}",
            file=sys.stderr, flush=True,
        )

        mgr = ForecastManager()
        ok = mgr.auto_fetch(
            lat, lon,
            start_time=start_dt,
            end_time=end_dt,
            units=units,
        )
        rows = mgr.get_rows()
        print(
            f"[FORECAST DEBUG] ok={ok}  rows={len(rows)}  "
            f"first={rows[0] if rows else 'none'}  "
            f"last={rows[-1] if rows else 'none'}",
            file=sys.stderr, flush=True,
        )
        if not ok:
            return jsonify({"rows": [], "update_time": None})
        return jsonify({"rows": rows, "update_time": mgr.update_time})
    except Exception as exc:
        print(f"[FORECAST ERROR] {exc}", file=sys.stderr, flush=True)
        return jsonify({"error": str(exc)}), 500


# ── NWS forecast versions (Wethr archival API) ───────────────────────────────

WETHR_API_KEY = os.environ.get("WETHR_API_KEY", "")
WETHR_BASE = "https://wethr.net"


@app.route("/api/forecast/nws-versions", methods=["POST"])
def api_nws_versions():
    """
    Fetch all archived NWS forecast versions for a date window from the Wethr API.

    Request body:
      { "city": "New York City", "start": "2026-05-14", "end": "2026-05-14", "units": "F" }

    Response:
      {
        "versions_by_date": {
          "2026-05-14": [
            {
              "label": "v1 (05/14 06:00z)",
              "index": 0,
              "times": ["2026-05-14T06:00:00Z", ...],
              "temps": [55.0, ...]
            },
            ...
          ]
        }
      }
    """
    import requests as _req
    from datetime import date as _date, timedelta as _td2
    body  = request.get_json(force=True)
    city  = body.get("city", "New York City")
    start = body.get("start", "")[:10]
    end   = body.get("end",   "")[:10]
    units = body.get("units", "F")

    station_code = CLI_STATION_IDS.get(city)
    if not station_code:
        return jsonify({"error": f"No station code for city '{city}'"}), 400

    try:
        d_start = _date.fromisoformat(start)
        d_end   = _date.fromisoformat(end)
    except ValueError as exc:
        return jsonify({"error": f"Bad date: {exc}"}), 400

    dates = []
    d = d_start
    while d <= d_end:
        dates.append(d.isoformat())
        d += _td2(days=1)

    versions_by_date = {}

    for date_str in dates:
        try:
            resp = _req.get(
                f"{WETHR_BASE}/api/v2/nws_forecasts.php",
                params={
                    "station_code": station_code,
                    "date": date_str,
                    "mode": "history",
                },
                headers={"X-API-Key": WETHR_API_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            raw_versions = data.get("forecasts", [])

            if not raw_versions:
                continue

            city_tz_name = CITIES.get(city, (0, 0, "UTC"))[2]
            civil_tz = ZoneInfo(city_tz_name)
            y, mo, day = (int(x) for x in date_str.split("-"))

            # LST offset: Wethr uses fixed standard-time hours year-round
            tz_offset_hours = data.get("timezone_offset_hours", 0)

            parsed = []
            for version in raw_versions:
                ver_num = version.get("version", len(parsed) + 1)
                hourly = version.get("hourly_temps") or []

                times = []
                temps_f = []
                for i, tv in enumerate(hourly):
                    if tv is None:
                        continue
                    try:
                        tf = float(tv)
                    except (TypeError, ValueError):
                        continue
                    # Treat index i as civil local hour (Wethr uses civil-time indexing)
                    naive_dt = datetime(y, mo, day) + timedelta(hours=i)
                    utc_dt = naive_dt.replace(tzinfo=civil_tz).astimezone(timezone.utc)
                    times.append(utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
                    temps_f.append(tf)

                if units == "C":
                    temps = [round((t - 32) * 5 / 9, 2) for t in temps_f]
                else:
                    temps = [round(t, 2) for t in temps_f]

                hi = version.get("high")
                lo = version.get("low")
                range_str = f"  H:{hi}/L:{lo}" if hi is not None and lo is not None else ""

                # Infer approximate issuance time from first non-null hourly index.
                # Wethr sets past hours to null, so the first non-null index ≈ the LST
                # hour when the forecast was fetched. Convert to UTC for display.
                issued_at = None
                first_idx = next((i for i, v in enumerate(hourly) if v is not None), None)
                if first_idx is not None:
                    lst_hour = first_idx
                    utc_hour = (lst_hour - tz_offset_hours) % 24
                    issued_at = f"~{utc_hour:02d}z"

                parsed.append({
                    "label": f"v{ver_num}{range_str}",
                    "index": ver_num - 1,
                    "times": times,
                    "temps": temps,
                    "issued_at": issued_at,
                })

            if parsed:
                versions_by_date[date_str] = parsed

        except Exception as exc:
            print(f"[NWS-VERSIONS] Failed for {date_str}: {exc}", file=sys.stderr, flush=True)

    return jsonify({"versions_by_date": versions_by_date})


# ── Open-Meteo HRRR/GFS forecast ─────────────────────────────────────────────

@app.route("/api/forecast/openmeteo", methods=["POST"])
def api_forecast_openmeteo():
    import sys
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    city  = body.get("city", "New York City")
    end   = body["end"]
    units = body.get("units", "F")
    browser_tz = body.get("browser_tz", "UTC")

    if city not in CITIES:
        return jsonify({"error": f"Unknown city: {city}"}), 400

    lat, lon, tz = CITIES[city]
    coord_source = "city_center"
    if city in CLI_STATION_COORDS:
        lat, lon = CLI_STATION_COORDS[city]
        coord_source = "cli_station"

    try:
        from openmeteo_forecast import fetch_openmeteo_forecast
        from zoneinfo import ZoneInfo
        # Convert browser-local time to city timezone
        end = _convert_browser_to_city(end, browser_tz, tz)
        end_dt = datetime.fromisoformat(end)
        print(
            f"[OPENMETEO FLASK DEBUG] city={city}  lat={lat}  lon={lon}  tz={tz}  "
            f"browser_end_input={body['end']}  browser_tz={browser_tz}  "
            f"converted_end={end}  end_dt={end_dt.isoformat()}  units={units}  coord_source={coord_source}",
            file=sys.stderr, flush=True,
        )
        result = fetch_openmeteo_forecast(
            lat, lon,
            end_time=end_dt,
            local_tz=tz,
            units=units,
        )
        result["coord_source"] = coord_source
        rows = result.get("rows", [])
        print(
            f"[OPENMETEO FLASK DEBUG] result rows={len(rows)}  "
            f"first={rows[0] if rows else 'none'}  "
            f"last={rows[-1] if rows else 'none'}",
            file=sys.stderr, flush=True,
        )
        return jsonify(result)
    except Exception as exc:
        print(f"[OPENMETEO FLASK ERROR] {exc}", file=sys.stderr, flush=True)
        return jsonify({"error": str(exc)}), 500


# ── Open-Meteo observed (HRRR analysis for the query window) ─────────────────

@app.route("/api/observed/openmeteo", methods=["POST"])
def api_observed_openmeteo():
    body       = request.get_json(force=True)
    city       = body.get("city", "New York City")
    start      = body["start"]
    end        = body["end"]
    units      = body.get("units", "F")
    browser_tz = body.get("browser_tz", "UTC")

    if city not in CITIES:
        return jsonify({"error": f"Unknown city: {city}"}), 400

    lat, lon, tz = CITIES[city]
    coord_source = "city_center"
    if city in CLI_STATION_COORDS:
        lat, lon = CLI_STATION_COORDS[city]
        coord_source = "cli_station"

    try:
        from openmeteo_forecast import fetch_openmeteo_observed
        start = _convert_browser_to_city(start, browser_tz, tz)
        end   = _convert_browser_to_city(end,   browser_tz, tz)
        start_dt = datetime.fromisoformat(start)
        end_dt   = datetime.fromisoformat(end)
        result = fetch_openmeteo_observed(lat, lon, start_dt, end_dt, local_tz=tz, units=units)
        result["coord_source"] = coord_source
        return jsonify(result)
    except Exception as exc:
        print(f"[OM-OBS FLASK ERROR] {exc}", file=sys.stderr, flush=True)
        return jsonify({"error": str(exc)}), 500


# ── Prior-period comparison overlay ──────────────────────────────────────────

@app.route("/api/compare", methods=["POST"])
def api_compare():
    body        = request.get_json(force=True)
    city        = body.get("city", "New York City")
    start       = body["start"]
    end         = body["end"]
    offset_days = int(body.get("offset_days", 1))
    units       = body.get("units", "F")
    browser_tz  = body.get("browser_tz", "UTC")

    if city not in CITIES:
        return jsonify({"error": f"Unknown city: {city}"}), 400

    lat, lon, tz = CITIES[city]
    if city in CLI_STATION_COORDS:
        lat, lon = CLI_STATION_COORDS[city]
    station_id = CLI_STATION_IDS.get(city)

    start = _convert_browser_to_city(start, browser_tz, tz)
    end   = _convert_browser_to_city(end, browser_tz, tz)

    try:
        offset   = timedelta(days=offset_days)
        start_dt = datetime.fromisoformat(start)
        end_dt   = datetime.fromisoformat(end)
        c_start  = (start_dt - offset).isoformat()

        # Extend compare window to the DST-aware forecast cap so the prior
        # line covers the same x-range as the forecast overlay.
        try:
            from weather_utils import forecast_cap_utc
            cap_utc = forecast_cap_utc(end_dt, tz)
            c_end   = (cap_utc - offset).isoformat()
        except Exception:
            c_end = (end_dt - offset).isoformat()

        result = get_temperature_summary(
            lat=lat, lon=lon,
            start=c_start, end=c_end,
            interval="hourly", local_tz=tz,
            source="nws",
            station_id=station_id,
        )

        # Shift timestamps forward so they plot over the current window
        shifted = []
        for row in result["data"]:
            dt = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            temp = f_to_c(row["temp"]) if units == "C" else row["temp"]
            shifted.append({"time": (dt + offset).isoformat(), "temp": temp})

        return jsonify({"rows": shifted, "offset_days": offset_days})
    except Exception as exc:
        import traceback
        print(f"[COMPARE ERROR] {exc}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        return jsonify({"error": str(exc)}), 500


# ── Prior-period Open-Meteo comparison overlay ───────────────────────────────

@app.route("/api/compare/openmeteo", methods=["POST"])
def api_compare_openmeteo():
    body        = request.get_json(force=True)
    city        = body.get("city", "New York City")
    start       = body["start"]
    end         = body["end"]
    offset_days = int(body.get("offset_days", 1))
    units       = body.get("units", "F")
    browser_tz  = body.get("browser_tz", "UTC")

    if city not in CITIES:
        return jsonify({"error": f"Unknown city: {city}"}), 400

    lat, lon, tz = CITIES[city]
    if city in CLI_STATION_COORDS:
        lat, lon = CLI_STATION_COORDS[city]

    start = _convert_browser_to_city(start, browser_tz, tz)
    end   = _convert_browser_to_city(end, browser_tz, tz)

    try:
        from openmeteo_forecast import fetch_openmeteo_observed

        offset   = timedelta(days=offset_days)
        start_dt = datetime.fromisoformat(start)
        end_dt   = datetime.fromisoformat(end)
        c_start  = start_dt - offset

        # Extend to DST-aware forecast cap, same logic as NWS compare.
        try:
            from weather_utils import forecast_cap_utc
            cap_utc = forecast_cap_utc(end_dt, tz)
            c_end   = cap_utc - offset
        except Exception:
            c_end = end_dt - offset

        result = fetch_openmeteo_observed(lat, lon, c_start, c_end, tz, units)

        # Shift timestamps forward so they align with the current window on the chart
        shifted = []
        for row in result.get("rows", []):
            dt   = datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
            temp = row["temp"]
            shifted.append({"time": (dt + offset).isoformat(), "temp": temp})

        return jsonify({"rows": shifted, "offset_days": offset_days})
    except Exception as exc:
        import traceback
        print(f"[OM-CMP ERROR] {exc}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        return jsonify({"error": str(exc)}), 500


# ── High-resolution raw observations ─────────────────────────────────────────

@app.route("/api/highres", methods=["POST"])
def api_highres():
    """Return every METAR/SPECI for a station with no resampling.

    source='nws'  — NWS /stations API, raw obs, no resample
    source='iem'  — IEM ASOS archive, metar_only filter, no resample
    """
    body    = request.get_json(force=True)
    station = (body.get("station") or "").strip().upper()
    start   = body.get("start", "")
    end     = body.get("end", "")
    source  = body.get("source", "nws")
    units   = body.get("units", "F")

    if not station:
        return jsonify({"error": "station is required"}), 400
    if not start or not end:
        return jsonify({"error": "start and end are required"}), 400

    try:
        from nws_weather import fetch_iem_obs, _ensure_utc
        from weather_highres import fetch_raw_obs

        if source == "iem":
            s_dt = _ensure_utc(start)
            e_dt = _ensure_utc(end)
            rows = fetch_iem_obs(station, s_dt, e_dt, metar_only=True)
        else:
            rows = fetch_raw_obs(station, start, end)

        if units == "C":
            for row in rows:
                row["temp"] = f_to_c(row["temp"])
                if row.get("dewpoint") is not None:
                    row["dewpoint"] = f_to_c(row["dewpoint"])

        return jsonify({"rows": rows, "station": station, "source": source, "count": len(rows)})
    except Exception as exc:
        print(f"[HIGHRES ERROR] {exc}", file=sys.stderr, flush=True)
        return jsonify({"error": str(exc)}), 500


# ── MOS statistical forecast ──────────────────────────────────────────────────

@app.route("/api/mos", methods=["POST"])
def api_mos():
    """Fetch GFS-MOS + LAMP forecasts from IEM for the city's CLI station."""
    body    = request.get_json(force=True)
    city    = body.get("city", "New York City")
    units   = body.get("units", "F")
    end_str = body.get("end")
    tz_str  = body.get("browser_tz", "UTC")

    from weather_utils import CLI_STATION_IDS
    station_id = CLI_STATION_IDS.get(city)
    if not station_id:
        return jsonify({"error": f"No CLI station mapping for: {city}"}), 400

    # Compute the DST-aware forecast cap so rows don't bleed past midnight for
    # non-DST cities (e.g. Phoenix) or past 1 AM for DST cities.
    # Use the city's own timezone (from CITIES), not browser_tz — the cap is a
    # city-local concept and must not depend on where the user's browser is.
    _, _, city_tz = CITIES.get(city, (0, 0, "UTC"))
    cap_utc = None
    if end_str:
        try:
            from weather_utils import forecast_cap_utc
            end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00')).astimezone(timezone.utc)
            cap_utc = forecast_cap_utc(end_dt, city_tz)
        except Exception as exc:
            print(f"[MOS] cap calc failed: {exc}", file=sys.stderr, flush=True)

    def _cap_rows(rows: list, extra_hours: int = 0) -> list:
        if cap_utc is None:
            return rows
        cutoff = cap_utc + timedelta(hours=extra_hours)
        out = []
        for r in rows:
            try:
                t = datetime.fromisoformat(r["time"].replace("Z", "+00:00"))
                if t <= cutoff:
                    out.append(r)
            except Exception:
                out.append(r)
        return out

    try:
        from mos_forecast import fetch_mos_parallel
        results = fetch_mos_parallel(station_id, units)
        for model in ("gfs", "lav"):
            if model in results and isinstance(results[model], dict):
                if model == "lav":
                    # LAMP has only ~38 h of range — don't clip it; the chart
                    # clips at xMax naturally, and notes snapshots need the full run.
                    pass
                else:
                    # GFS-MOS has 7-day data; cap with a 3-h buffer so Chart.js
                    # can draw a segment that reaches the xMax boundary.
                    results[model]["rows"]     = _cap_rows(results[model].get("rows", []), extra_hours=3)
                    # Marker values stay at the strict cap so dots/labels don't appear past midnight.
                    results[model]["n_x_vals"] = _cap_rows(results[model].get("n_x_vals", []))
        return jsonify({"city": city, "station": station_id, **results})
    except Exception as exc:
        import traceback
        print(f"[MOS FLASK ERROR] {exc}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        return jsonify({"error": str(exc)}), 500


# ── Kalshi prediction markets ──────────────────────────────────────────────────

@app.route("/api/kalshi", methods=["POST"])
def api_kalshi():
    """Fetch Kalshi weather contracts for a city (today only)."""
    body = request.get_json(force=True)
    city = body.get("city", "New York City")
    forecast_high = body.get("forecast_high")  # optional, echoed for highlighting
    forecast_low = body.get("forecast_low")    # optional, echoed for highlighting

    if city not in CITIES:
        return jsonify({"error": f"Unknown city: {city}"}), 400

    result = get_city_contracts(city)
    if result.get("error"):
        return jsonify({"error": result["error"]}), 500

    return jsonify({
        "city": result["city"],
        "high_markets": result["high_markets"],
        "low_markets": result["low_markets"],
        "forecast_high": forecast_high,
        "forecast_low": forecast_low,
    })


# ── Kalshi live WebSocket feed ────────────────────────────────────────────────

def _init_kalshi_ws():
    key_id   = os.environ.get("KALSHI_KEY_ID", "").strip()
    key_file = os.environ.get("KALSHI_KEY_FILE", "").strip()
    key_pem  = os.environ.get("KALSHI_KEY_PEM", "").strip()

    if not key_id:
        print("[Kalshi WS] KALSHI_KEY_ID not set — live feed disabled", file=sys.stderr, flush=True)
        return

    pem = key_pem
    if not pem and key_file:
        key_path = key_file if os.path.isabs(key_file) else os.path.join(
            os.path.dirname(os.path.abspath(__file__)), key_file
        )
        try:
            with open(key_path) as f:
                pem = f.read()
        except Exception as exc:
            print(f"[Kalshi WS] could not read {key_path}: {exc}", file=sys.stderr, flush=True)
            return

    if not pem:
        print("[Kalshi WS] no PEM key found — live feed disabled", file=sys.stderr, flush=True)
        return

    try:
        from kalshi_ws import init_ws_client
        init_ws_client(key_id, pem)
        print("[Kalshi WS] client started", file=sys.stderr, flush=True)
    except Exception as exc:
        print(f"[Kalshi WS] init failed: {exc}", file=sys.stderr, flush=True)


_init_kalshi_ws()


@app.route("/api/kalshi/stream")
def api_kalshi_stream():
    """SSE stream of live Kalshi ticker updates for a city."""
    city = request.args.get("city", "").strip()

    def error_stream(msg):
        def gen():
            yield f"data: {json.dumps({'error': msg})}\n\n"
        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if not city:
        return error_stream("city parameter required")

    try:
        from kalshi_ws import get_ws_client
        from kalshi_markets import CITY_HIGH_SERIES, CITY_LOW_SERIES, CITY_TIMEZONES, get_open_markets
    except ImportError as exc:
        return error_stream(str(exc))

    ws_client = get_ws_client()
    if ws_client is None:
        return error_stream("Kalshi WS not configured — add KALSHI_KEY_ID and KALSHI_KEY_FILE to .env")

    local_tz = CITY_TIMEZONES.get(city, "America/New_York")
    tickers = []
    ticker_to_type = {}

    for kind, series_map in [("high", CITY_HIGH_SERIES), ("low", CITY_LOW_SERIES)]:
        series = series_map.get(city)
        if not series:
            continue
        try:
            markets = get_open_markets(series, local_tz)
            for m in markets:
                t = m.get("ticker", "")
                if t:
                    tickers.append(t)
                    ticker_to_type[t] = kind
        except Exception as exc:
            print(f"[SSE] get_open_markets error for {city}/{kind}: {exc}", file=sys.stderr, flush=True)

    if not tickers:
        return error_stream(f"No open contracts for {city} today")

    q = ws_client.add_subscriber()
    ws_client.subscribe_tickers(tickers)

    def generate():
        # Flush headers immediately so EventSource fires 'open' right away
        yield ": connected\n\n"
        # Send cached snapshot immediately so the client sees prices before next trade
        for item in ws_client.get_snapshot(tickers):
            t = item.get("market_ticker", "")
            payload = {"ticker": t, "type": ticker_to_type.get(t, "unknown"), "city": city, "data": item}
            yield f"data: {json.dumps(payload)}\n\n"

        try:
            while True:
                try:
                    item = q.get(timeout=25)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                t = item["ticker"]
                if t not in ticker_to_type:
                    continue
                payload = {"ticker": t, "type": ticker_to_type[t], "city": city, "data": item["data"]}
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            ws_client.remove_subscriber(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ── Web notes ─────────────────────────────────────────────────────────────────

NOTES_WEB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes_web.json")


def _load_web_notes() -> list:
    if not os.path.exists(NOTES_WEB_FILE):
        return []
    try:
        with open(NOTES_WEB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_web_notes(notes: list) -> None:
    dir_ = os.path.dirname(NOTES_WEB_FILE)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(notes, f, indent=2)
        os.replace(tmp, NOTES_WEB_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@app.route("/api/notes", methods=["GET"])
def api_notes_get():
    return jsonify({"notes": _load_web_notes()})


@app.route("/api/notes", methods=["POST"])
def api_notes_post():
    note = request.get_json(force=True, silent=True)
    if not isinstance(note, dict):
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    notes = _load_web_notes()
    idx = next((i for i, n in enumerate(notes) if n.get("id") == note.get("id")), None)
    if idx is not None:
        notes[idx] = note
    else:
        notes.insert(0, note)
    _save_web_notes(notes)
    return jsonify({"ok": True, "id": note.get("id")})


@app.route("/api/notes/<note_id>", methods=["DELETE"])
def api_notes_delete(note_id):
    notes = _load_web_notes()
    notes = [n for n in notes if n.get("id") != note_id]
    _save_web_notes(notes)
    return jsonify({"ok": True})


@app.route("/api/notes/<note_id>", methods=["PATCH"])
def api_notes_patch(note_id):
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    notes = _load_web_notes()
    for note in notes:
        if note.get("id") == note_id:
            if "note_text" in body:
                note["note_text"] = body["note_text"]
            if "append_chart_img" in body:
                imgs = note.setdefault("chart_imgs", [])
                entry = {
                    "img":      body["append_chart_img"],
                    "taken_at": body.get("taken_at", ""),
                }
                if "rows" in body and isinstance(body["rows"], dict):
                    entry["rows"]    = body["rows"]
                    entry["units"]   = body.get("units")
                    entry["city_tz"] = body.get("city_tz")
                if body.get("city"):
                    entry["city"] = body["city"]
                imgs.append(entry)
            if "remove_chart_img" in body:
                idx = body["remove_chart_img"]
                if idx == "original":
                    note["chart_img"] = None
                else:
                    imgs = note.get("chart_imgs", [])
                    i = int(idx)
                    if 0 <= i < len(imgs):
                        imgs.pop(i)
                        note["chart_imgs"] = imgs
    _save_web_notes(notes)
    return jsonify({"ok": True})


# ── Wethr.net SSE proxy (singleton upstream per station) ─────────────────────
#
# Wethr Professional allows maxConnections=1. We keep ONE upstream connection
# per station in a background thread and fan-out to all browser subscribers via
# per-subscriber queues — same pattern as the Kalshi WS client.

WETHR_API_KEY    = os.environ.get("WETHR_API_KEY")
WETHR_STREAM_URL = "https://wethr.net:3443/api/v2/stream"

import queue as _queue
import threading as _threading
import time as _time


class _WethrManager:
    """Single upstream SSE connection to Wethr for all active stations.

    maxConnections=1 is a global limit — we use one connection with a
    comma-separated stations list and route events by the station field
    in each JSON payload.
    """

    def __init__(self):
        self._lock      = _threading.Lock()
        self._subs      = {}        # station -> [Queue]
        self._snapshots = {}        # station -> last observation SSE message
        self._thread    = None
        self._reconnect = _threading.Event()   # set to signal the thread to reconnect

    # ── Public API ────────────────────────────────────────────────────────────

    def subscribe(self, station: str) -> _queue.Queue:
        q = _queue.Queue(maxsize=100)
        with self._lock:
            if station not in self._subs:
                self._subs[station] = []
            self._subs[station].append(q)
            # Replay snapshot so new subscribers see last known values immediately
            if self._snapshots.get(station):
                try:
                    q.put_nowait(self._snapshots[station])
                except _queue.Full:
                    pass
            # Start thread if needed; otherwise signal reconnect with new stations list
            if self._thread is None or not self._thread.is_alive():
                self._thread = _threading.Thread(target=self._run, daemon=True)
                self._thread.start()
            else:
                self._reconnect.set()
        return q

    def unsubscribe(self, station: str, q: _queue.Queue):
        with self._lock:
            if station in self._subs:
                try:
                    self._subs[station].remove(q)
                except ValueError:
                    pass
                if not self._subs[station]:
                    del self._subs[station]
                    self._reconnect.set()   # reconnect without this station

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _active_stations(self) -> list:
        with self._lock:
            return list(self._subs.keys())

    def _broadcast(self, station: str, msg: str, event_type: str = ''):
        with self._lock:
            if event_type == 'observation':
                self._snapshots[station] = msg
            for q in list(self._subs.get(station, [])):
                try:
                    q.put_nowait(msg)
                except _queue.Full:
                    pass

    # ── Background thread ─────────────────────────────────────────────────────

    def _run(self):
        import requests as _req
        retry_delay = 0

        while True:
            stations = self._active_stations()
            if not stations:
                break

            if retry_delay > 0:
                print(f"[WETHR] waiting {retry_delay}s before reconnect", flush=True)
                _time.sleep(retry_delay)
                retry_delay = 0

            stations = self._active_stations()
            if not stations:
                break

            self._reconnect.clear()
            stations_str = ','.join(stations)
            url = f"{WETHR_STREAM_URL}?stations={stations_str}&api_key={WETHR_API_KEY}"

            try:
                print(f"[WETHR] connecting: {stations_str}", flush=True)
                with _req.get(url, stream=True, timeout=(10, 300)) as r:
                    if r.status_code == 429:
                        try:
                            retry_delay = r.json().get('retryAfter', 10)
                        except Exception:
                            retry_delay = 10
                        print(f"[WETHR] 429 — retry in {retry_delay}s", flush=True)
                        continue

                    if r.status_code != 200:
                        print(f"[WETHR] unexpected status {r.status_code}", flush=True)
                        retry_delay = 15
                        continue

                    print(f"[WETHR] connected: {stations_str}", flush=True)
                    event_type = None

                    for raw in r.iter_lines(decode_unicode=True):
                        # Reconnect if the active-stations set changed
                        if self._reconnect.is_set():
                            print(f"[WETHR] stations changed — reconnecting", flush=True)
                            break
                        if not self._active_stations():
                            break

                        if raw.startswith('event:'):
                            event_type = raw[6:].strip()
                        elif raw.startswith('data:'):
                            data_str = raw[5:].strip()

                            if event_type == 'displaced':
                                print(f"[WETHR] displaced — reconnecting in 3s", flush=True)
                                retry_delay = 3
                                break
                            if event_type == 'connected':
                                print(f"[WETHR] handshake ok: {stations_str}", flush=True)
                                event_type = None
                                continue

                            # Parse station ID from payload to route to correct subscribers
                            station_id = ''
                            try:
                                d = json.loads(data_str)
                                station_id = (
                                    d.get('station_code') or d.get('station_id') or
                                    d.get('icao') or d.get('station') or
                                    d.get('identifier') or ''
                                ).upper()
                            except Exception:
                                pass

                            print(f"[WETHR] event={event_type!r} station={station_id!r} data={data_str[:120]}", flush=True)

                            if station_id:
                                msg = f"event: {event_type}\ndata: {data_str}\n\n" if event_type else f"data: {data_str}\n\n"
                                self._broadcast(station_id, msg, event_type or '')

                            event_type = None
                        elif raw == '':
                            event_type = None

            except Exception as exc:
                print(f"[WETHR] error: {exc}", flush=True)
                retry_delay = 5

        print(f"[WETHR] thread exiting (no subscribers)", flush=True)
        with self._lock:
            self._thread = None


_wethr_mgr = _WethrManager()


@app.route("/api/wethr/stream")
def api_wethr_stream():
    """Fan-out Wethr SSE to browser; single upstream connection per station."""
    station = request.args.get("station", "").strip().upper()
    if not station:
        def _err():
            yield 'data: {"error":"station parameter required"}\n\n'
        return Response(stream_with_context(_err()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    q = _wethr_mgr.subscribe(station)

    def generate():
        yield ": connected\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=25)
                    yield msg
                except _queue.Empty:
                    yield ": keepalive\n\n"   # keep Flask/nginx from closing idle stream
        except GeneratorExit:
            pass
        finally:
            _wethr_mgr.unsubscribe(station, q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  NWS Weather Explorer")
    print(f"  Classic UI  ->  http://localhost:{port}/")
    print(f"  Full UI     ->  http://localhost:{port}/app\n")
    app.run(debug=False, port=port, threaded=True)
