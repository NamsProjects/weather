"""
current_forecasts.py
====================
NWS gridpoint hourly forecast for the web server.

ForecastManager.auto_fetch() is synchronous — called directly from a Flask
route handler.  It fetches the NWS /points → /forecast/hourly chain, filters
to the window after end_time, and stores rows ready for get_rows().
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from nws_weather import HEADERS, REQUEST_TIMEOUT, MAX_RETRIES, RETRY_BACKOFF, NWS_BASE


def _get_json(url: str, params=None) -> dict:
    import time
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    raise RuntimeError(f"NWS request failed after {MAX_RETRIES} attempts: {last_exc}")


class ForecastManager:
    """
    Fetches the NWS gridpoint hourly forecast for a lat/lon and query window.

    Usage (synchronous, for Flask):
        mgr = ForecastManager()
        ok  = mgr.auto_fetch(lat, lon, start_time=start_dt, end_time=end_dt, units=units)
        rows = mgr.get_rows()          # list of {"time": UTC iso Z str, "temp": float}
        ts   = mgr.update_time         # NWS forecast updateTime iso str, or None
    """

    def __init__(self):
        self._rows:       list[dict]   = []
        self.update_time: str | None   = None

    def get_rows(self) -> list[dict]:
        return list(self._rows)

    def auto_fetch(
        self,
        lat: float,
        lon: float,
        *,
        start_time: Optional[datetime] = None,
        end_time:   Optional[datetime] = None,
        units: str = "F",
    ) -> bool:
        """
        Fetch NWS gridpoint hourly forecast and store rows filtered to the
        window strictly after end_time up to the city's CLI forecast cap.

        Returns True on success, False on error.
        """
        self._rows       = []
        self.update_time = None

        try:
            # ── 1. Resolve gridpoint ──────────────────────────────────────
            points_data = _get_json(f"{NWS_BASE}/points/{lat:.4f},{lon:.4f}")
            forecast_url = points_data["properties"]["forecastHourly"]

            # ── 2. Fetch hourly forecast ──────────────────────────────────
            forecast_data = _get_json(forecast_url)
            props   = forecast_data["properties"]
            periods = props.get("periods", [])
            self.update_time = props.get("updateTime")

            print(
                f"[FORECAST DEBUG] NWS returned {len(periods)} periods  "
                f"updateTime={self.update_time}",
                file=sys.stderr, flush=True,
            )

            if not periods:
                return False

            # ── 3. Determine cap window ───────────────────────────────────
            # Use end_time (last obs) only to compute the cap, not as a lower bound.
            # All forecast periods from the API are included so the frontend can
            # show the full forecast even where it overlaps observed data.
            if end_time is not None:
                if end_time.tzinfo is None:
                    end_utc = end_time.replace(tzinfo=timezone.utc)
                else:
                    end_utc = end_time.astimezone(timezone.utc)
            else:
                end_utc = datetime.now(timezone.utc)

            # Cap: 1 AM local next day (DST-aware), matching OM forecast cap
            cap_utc: Optional[datetime] = None
            try:
                # Resolve timezone from the first period's startTime offset
                from weather_utils import forecast_cap_utc
                # Derive tz from the gridpoint response if available
                tz_name: str | None = points_data["properties"].get("timeZone")
                if tz_name:
                    cap_utc = forecast_cap_utc(end_utc, tz_name)
            except Exception as cap_exc:
                print(f"[FORECAST] cap calc failed: {cap_exc}", file=sys.stderr, flush=True)

            if cap_utc is None:
                cap_utc = (
                    end_utc.replace(hour=1, minute=0, second=0, microsecond=0)
                    + timedelta(days=1)
                )

            print(
                f"[FORECAST DEBUG] filter window: end_utc={end_utc.isoformat()}  "
                f"cap_utc={cap_utc.isoformat()}",
                file=sys.stderr, flush=True,
            )

            # ── 4. Parse and filter periods ───────────────────────────────
            rows: list[dict] = []
            for period in periods:
                try:
                    t_str = period["startTime"]
                    dt    = datetime.fromisoformat(t_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    dt_utc = dt.astimezone(timezone.utc)
                except Exception:
                    continue

                if dt_utc > cap_utc:
                    continue

                temp_val  = period.get("temperature")
                temp_unit = period.get("temperatureUnit", "F")
                if temp_val is None:
                    continue

                temp_f = float(temp_val) if temp_unit == "F" else float(temp_val) * 9 / 5 + 32
                temp   = temp_f if units == "F" else round((temp_f - 32) * 5 / 9, 2)

                utc_iso = f"{dt_utc.strftime('%Y-%m-%dT%H:%M:%S')}Z"
                rows.append({"time": utc_iso, "temp": round(temp, 2)})

            self._rows = rows
            print(
                f"[FORECAST DEBUG] kept {len(rows)} rows  "
                f"first={rows[0] if rows else 'none'}  "
                f"last={rows[-1] if rows else 'none'}",
                file=sys.stderr, flush=True,
            )
            return bool(rows)

        except Exception as exc:
            print(f"[FORECAST ERROR] auto_fetch failed: {exc}", file=sys.stderr, flush=True)
            return False
