"""
kalshi_markets.py
=================
Kalshi weather contract fetching.

Series tickers verified from live Kalshi URLs (May 2025).
  HIGH series prefix: KXHIGH
  LOW  series prefix: KXLOWT   ← note the T, not KXLOW

Only returns contracts closing TODAY in the city's local timezone.
Tomorrow's contracts are filtered out even though they show as 'open'.
"""

import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
HEADERS = {"Accept": "application/json"}

# ── Verified HIGH series tickers ──────────────────────────────────────────────
CITY_HIGH_SERIES: dict[str, str] = {
    "New York City":  "KXHIGHNY",
    "Chicago":        "KXHIGHCHI",
    "Los Angeles":    "KXHIGHLAX",
    "Miami":          "KXHIGHMIA",
    "Seattle":        "KXHIGHTSEA",
    "Denver":         "KXHIGHDEN",
    "Philadelphia":   "KXHIGHPHIL",
    "Austin":         "KXHIGHAUS",
    "Houston":        "KXHIGHTHOU",
    "Boston":         "KXHIGHTBOS",
    "Las Vegas":      "KXHIGHTLV",
    "San Antonio":    "KXHIGHTSATX",
    "Washington DC":  "KXHIGHTDC",
    "New Orleans":    "KXHIGHTNOLA",
    "San Francisco":  "KXHIGHTSFO",
    "Oklahoma City":  "KXHIGHTOKC",
    "Phoenix":        "KXHIGHTPHX",
    "Minneapolis":    "KXHIGHTMIN",
    "Dallas":         "KXHIGHTDAL",
    "Atlanta":        "KXHIGHTATL",
}

# ── Verified LOW series tickers (prefix is KXLOWT, not KXLOW) ────────────────
CITY_LOW_SERIES: dict[str, str] = {
    "New York City":  "KXLOWTNYC",
    "Chicago":        "KXLOWTCHI",
    "Los Angeles":    "KXLOWTLAX",
    "Miami":          "KXLOWTMIA",
    "Seattle":        "KXLOWTSEA",
    "Philadelphia":   "KXLOWTPHIL",
    "Austin":         "KXLOWTAUS",
    "Boston":         "KXLOWTBOS",
    "Las Vegas":      "KXLOWTLV",
    "San Antonio":    "KXLOWTSATX",
    "Washington DC":  "KXLOWTDC",
    "New Orleans":    "KXLOWTNOLA",
    "San Francisco":  "KXLOWTSFO",
    "Oklahoma City":  "KXLOWTOKC",
    "Phoenix":        "KXLOWTPHX",
    "Minneapolis":    "KXLOWTMIN",
    "Dallas":         "KXLOWTDAL",
    "Atlanta":        "KXLOWTATL",
    "Houston":        "KXLOWTHOU",
    "Denver":         "KXLOWTDEN",
}

# ── City timezone lookup ──────────────────────────────────────────────────────
CITY_TIMEZONES: dict[str, str] = {
    "New York City":  "America/New_York",
    "Chicago":        "America/Chicago",
    "Los Angeles":    "America/Los_Angeles",
    "Miami":          "America/New_York",
    "Seattle":        "America/Los_Angeles",
    "Denver":         "America/Denver",
    "Philadelphia":   "America/New_York",
    "Austin":         "America/Chicago",
    "Boston":         "America/New_York",
    "Las Vegas":      "America/Los_Angeles",
    "San Antonio":    "America/Chicago",
    "Washington DC":  "America/New_York",
    "New Orleans":    "America/Chicago",
    "San Francisco":  "America/Los_Angeles",
    "Oklahoma City":  "America/Chicago",
    "Phoenix":        "America/Phoenix",
    "Minneapolis":    "America/Chicago",
    "Dallas":         "America/Chicago",
    "Atlanta":        "America/New_York",
    "Houston":        "America/Chicago",
}

# Cities with any Kalshi contract
KALSHI_CITIES: set[str] = set(CITY_HIGH_SERIES) | set(CITY_LOW_SERIES)


# ── Filter helper ─────────────────────────────────────────────────────────────

def _is_today_contract(close_time_str: str, local_tz: str = "America/New_York") -> bool:
    """
    Return True if this contract is for TODAY's weather report.

    Kalshi daily weather contracts close around 1 AM the *next* calendar day
    (e.g. the March 10 high/low contract closes ~March 11 1:00 AM local).
    Tomorrow's contracts (closing ~March 12 1:00 AM) must be excluded.

    Window accepted:  today 00:00:00  →  tomorrow 01:00:00  (local time)
    Anything closing after tomorrow 1 AM is next day's contract — drop it.
    """
    if not close_time_str:
        return False
    try:
        ct  = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        tz  = ZoneInfo(local_tz)
        now = datetime.now(tz)

        # Window start: midnight of today (local)
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Window end: 1:00 AM tomorrow (local) — this is when today's contract closes
        tomorrow_1am = today_midnight + timedelta(days=1, hours=1)

        ct_local = ct.astimezone(tz)
        return today_midnight <= ct_local <= tomorrow_1am
    except Exception:
        return False


# ── API helpers ───────────────────────────────────────────────────────────────

def get_open_markets(series_ticker: str, local_tz: str = "America/New_York") -> list[dict]:
    """
    Fetch all open markets for a series, filtered to today's contracts only.

    Parameters
    ----------
    series_ticker : str
        Kalshi series ticker, e.g. "KXHIGHNY" or "KXLOWTNYC".
    local_tz : str
        IANA timezone for the city, used to determine what "today" means.
        Defaults to America/New_York. Pass CITY_TIMEZONES[city] for accuracy.
    """
    url = f"{KALSHI_BASE}/markets"
    params = {"series_ticker": series_ticker, "status": "open", "limit": 100}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    markets = resp.json().get("markets", [])

    # Drop tomorrow's contracts — they show as 'open' but close_time is tomorrow
    markets = [m for m in markets if _is_today_contract(m.get("close_time", ""), local_tz)]

    return markets


def get_city_contracts(city: str) -> dict:
    """
    Return all open high + low contracts for a city, today only.

    Parameters
    ----------
    city : str
        City name matching a key in CITY_HIGH_SERIES or CITY_LOW_SERIES.

    Returns
    -------
    dict with keys:
        city            str
        high_markets    list[dict]
        low_markets     list[dict]
        error           str | None
    """
    result = {
        "city": city,
        "high_markets": [],
        "low_markets": [],
        "error": None,
    }

    high_series = CITY_HIGH_SERIES.get(city)
    low_series  = CITY_LOW_SERIES.get(city)

    if not high_series and not low_series:
        result["error"] = f"No Kalshi weather contracts available for {city}."
        return result

    local_tz = CITY_TIMEZONES.get(city, "America/New_York")

    try:
        if high_series:
            result["high_markets"] = get_open_markets(high_series, local_tz=local_tz)
        if low_series:
            result["low_markets"]  = get_open_markets(low_series,  local_tz=local_tz)
    except Exception as exc:
        result["error"] = str(exc)

    return result