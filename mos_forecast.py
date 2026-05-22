"""
mos_forecast.py
===============
Fetches MOS (Model Output Statistics) forecasts from the IEM (Iowa Environmental
Mesonet) JSON API — the same source the app uses for historical observations.

Products supported
------------------
  GFS   GFS-MOS (MAV equivalent)  4x/day  ~7-day range  3-hourly temps
  LAV   GFS LAMP                  hourly  ~38-hour range hourly temps

IEM endpoint
------------
  GET https://mesonet.agron.iastate.edu/api/1/mos.json?station=KNYC&model=GFS

Key response fields
-------------------
  ftime_utc  forecast valid time (UTC ISO)
  runtime_utc model run initialisation time (UTC ISO)
  tmp        point forecast temperature (°F)
  n_x        12-hour min (at 12z UTC) or max (at 00z UTC) temperature
             • 00z UTC = daytime HIGH for the period 12z-00z local
             • 12z UTC = overnight LOW for the period 00z-12z local
  dpt        dewpoint (°F)
  cld        cloud cover code  CL/FW/SC/BK/OV

n_x timing note
---------------
The NWS CLI settlement window (DST) runs 01:00 AM – 12:59 AM local.
GFS-MOS n_x HIGH (00z UTC ≈ 8 PM EDT) and LOW (12z UTC ≈ 8 AM EDT) both
fall within that window, so n_x is directly comparable to the CLI min/max
that settles Kalshi contracts.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import requests

IEM_MOS_BASE = "https://mesonet.agron.iastate.edu/api/1/mos.json"
REQUEST_TIMEOUT = 15


def fetch_mos(station_id: str, model: str = "GFS", units: str = "F") -> dict:
    """
    Fetch the latest MOS guidance for *station_id* from the IEM JSON API.

    Returns
    -------
    {
        "rows":      [{"time": utc_iso, "temp": float, "dpt": float|None, "cld": str|None}],
        "n_x_vals":  [{"time": utc_iso, "value": float, "type": "high"|"low"}],
        "runtime":   utc_iso | None,
        "model":     str,
        "station":   str,
    }
    """
    station = station_id.upper()
    model_up = model.upper()

    try:
        resp = requests.get(
            IEM_MOS_BASE,
            params={"station": station, "model": model_up},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        records = resp.json().get("data", [])
    except Exception as exc:
        raise RuntimeError(f"IEM MOS ({model_up}/{station}) fetch failed: {exc}") from exc

    if not records:
        return {
            "rows": [], "n_x_vals": [],
            "runtime": None, "model": model_up, "station": station,
        }

    # ── Runtime from first record ─────────────────────────────────────────────
    runtime: Optional[str] = None
    rt_raw = records[0].get("runtime_utc")
    if rt_raw:
        try:
            dt_rt = datetime.fromisoformat(rt_raw.replace(".000", "")).replace(tzinfo=timezone.utc)
            runtime = dt_rt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    # Use dicts keyed by ftime to deduplicate: IEM may return the same valid time
    # from multiple model runs. We keep the entry with the latest runtime_utc so
    # the chart never has two points at the same x position.
    rows_by_ftime: dict[str, dict] = {}
    nx_by_ftime: dict[str, dict] = {}

    for rec in records:
        ftime_raw = rec.get("ftime_utc")
        tmp = rec.get("tmp")
        if ftime_raw is None or tmp is None:
            continue

        try:
            dt = datetime.fromisoformat(ftime_raw.replace(".000", "")).replace(tzinfo=timezone.utc)
        except Exception:
            continue

        ftime_key = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Track the runtime so we can prefer the most recent run when deduplicating.
        rt_raw = rec.get("runtime_utc", "")
        rec_runtime = rt_raw or ""

        # Only overwrite if this record comes from a newer (or equal) model run.
        existing = rows_by_ftime.get(ftime_key)
        if existing and existing.get("_runtime", "") > rec_runtime:
            continue

        temp_f = float(tmp)
        temp_out = round((temp_f - 32) * 5 / 9, 2) if units == "C" else round(temp_f, 1)

        row: dict = {"time": ftime_key, "temp": temp_out, "_runtime": rec_runtime}

        dpt = rec.get("dpt")
        if dpt is not None:
            dpt_f = float(dpt)
            row["dpt"] = round((dpt_f - 32) * 5 / 9, 2) if units == "C" else round(dpt_f, 1)

        cld = rec.get("cld")
        if cld:
            row["cld"] = cld

        rows_by_ftime[ftime_key] = row

        # ── n_x: HIGH at 00z UTC, LOW at 12z UTC ─────────────────────────────
        n_x = rec.get("n_x")
        if n_x is not None:
            try:
                nx_f = float(n_x)
                nx_out = round((nx_f - 32) * 5 / 9, 2) if units == "C" else round(nx_f, 1)
                nx_type = "high" if dt.hour == 0 else "low"
                nx_by_ftime[ftime_key] = {
                    "time":     ftime_key,
                    "value":    nx_out,
                    "type":     nx_type,
                    "_runtime": rec_runtime,
                }
            except Exception:
                pass

    # Strip internal _runtime field and sort chronologically.
    rows = sorted(
        [{k: v for k, v in r.items() if k != "_runtime"} for r in rows_by_ftime.values()],
        key=lambda r: r["time"],
    )
    n_x_vals = sorted(
        [{k: v for k, v in r.items() if k != "_runtime"} for r in nx_by_ftime.values()],
        key=lambda r: r["time"],
    )

    return {
        "rows":     rows,
        "n_x_vals": n_x_vals,
        "runtime":  runtime,
        "model":    model_up,
        "station":  station,
    }


def fetch_mos_parallel(station_id: str, units: str = "F") -> dict:
    """
    Fetch GFS-MOS and LAMP concurrently.

    Returns
    -------
    {"gfs": {...}, "lav": {...}}
    Each value is the fetch_mos() result dict, or {"error": str, "rows": [], "n_x_vals": []}
    on failure.
    """
    results: dict = {}

    def _safe(model: str) -> dict:
        try:
            return fetch_mos(station_id, model, units)
        except Exception as exc:
            print(f"[MOS ERROR] {model}: {exc}", file=sys.stderr, flush=True)
            return {"error": str(exc), "rows": [], "n_x_vals": [],
                    "runtime": None, "model": model.upper(), "station": station_id.upper()}

    with ThreadPoolExecutor(max_workers=2) as ex:
        gfs_f = ex.submit(_safe, "GFS")
        lav_f = ex.submit(_safe, "LAV")
        results["gfs"] = gfs_f.result()
        results["lav"] = lav_f.result()

    return results
