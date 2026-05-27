"""
weather_highres.py
==================
Fetches raw NWS METAR/SPECI observations without resampling.

Useful for finding the true daily low/high when resampling artifacts cause
the main chart's sampled min to differ from the official CLI value.

IEM source mode uses report_type=[1] (METAR+SPECI only) rather than the
1-minute feed. IEM archives the full METAR/SPECI sequence more completely
than the NWS observations API, making it the most reliable way to catch
SPECIs that the NWS /stations endpoint occasionally drops.
"""

from __future__ import annotations


def fetch_raw_obs(station_id: str, start_iso: str, end_iso: str,
                  local_tz: str | None = None) -> list[dict]:
    """
    Fetch every NWS observation for the given station/window and return
    them as {"time": ISO str, "temp": float (°F), "dewpoint": float|None (°F)} dicts.
    No resampling — each dict is one actual METAR/SPECI report.
    """
    from nws_weather import fetch_observations, extract_temperature_data, _ensure_utc
    from zoneinfo import ZoneInfo

    start_dt = _ensure_utc(start_iso)
    end_dt   = _ensure_utc(end_iso)

    raw_features = fetch_observations(station_id, start_dt, end_dt)
    df = extract_temperature_data(raw_features)

    if df.empty:
        return []

    df = df.loc[(df.index >= start_dt) & (df.index <= end_dt)]
    if df.empty:
        return []

    if local_tz:
        try:
            df.index = df.index.tz_convert(ZoneInfo(local_tz))
        except Exception:
            pass

    import math
    rows = []
    for idx, row in df.iterrows():
        dew = row.get("dewpoint")
        rows.append({
            "time":     idx.isoformat(),
            "temp":     float(row["temp"]),
            "dewpoint": float(dew) if (dew is not None and not math.isnan(dew)) else None,
        })
    return rows
