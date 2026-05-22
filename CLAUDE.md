# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

**Web server (Flask):**
```powershell
python server.py
# Then open http://localhost:5000
```

## Architecture

This is a **Flask web app** — there is no desktop GUI. The browser fetches data from a Python/Flask backend that wraps NWS, Open-Meteo, MOS, and Kalshi APIs.

- **Backend** (`server.py`): REST API + SSE streams; served at `:5000`
- **Frontend** (`static/`): HTML + vanilla JS (Chart.js + Luxon); split into feature modules

### Module responsibilities

| File | Role |
|------|------|
| `server.py` | Flask REST API — all endpoints, SSE streams, notes persistence |
| `nws_weather.py` | NWS API — `get_temperature_summary()`, `get_cli_report()`, `fetch_iem_obs()`, retry/backoff, multi-source observations |
| `openmeteo_forecast.py` | `OpenMeteoForecastManager` — fetches Open-Meteo HRRR/GFS best-match forecast |
| `mos_forecast.py` | MOS forecast fetching — `fetch_mos_parallel()` |
| `kalshi_markets.py` | Kalshi REST API helpers |
| `kalshi_ws.py` | Kalshi WebSocket client (used for SSE stream) |
| `weather_highres.py` | `fetch_raw_obs()` — raw NWS METAR/SPECI without resampling |
| `weather_utils.py` | Lookup tables (`CITIES`, `CLI_LOCATIONS`, `CLI_STATION_COORDS`, `CLI_STATION_IDS`), CLI window logic, stats computation |

### Frontend modules (`static/`)

| File | Role |
|------|------|
| `app.html` | Main app shell |
| `index.html` | Landing page |
| `styles.css` | Global styles |
| `app-state.js` | Shared app state |
| `app-controls.js` | City/date/unit controls |
| `app-fetch.js` | API fetch helpers |
| `app-chart.js` | Chart.js rendering and overlays |
| `app-cards.js` | Stat cards (min/max/duration) |
| `app-panels.js` | Panel layout and tab management |
| `app-cli.js` | CLI report panel |
| `app-notes.js` | Notes panel |
| `app-wethr.js` | Wethr SSE stream integration |

### Data flow

1. Browser sends HTTP POST to a Flask endpoint with city, start/end dates, options
2. Flask calls the relevant data module(s)
3. JSON response returned; browser renders with Chart.js
4. Streaming data (Kalshi, wethr) delivered via SSE (`/api/kalshi/stream`, `/api/wethr/stream`)

### CLI window logic

NWS CLI reports use a non-standard reporting window: during DST it is 01:00–00:59 local (next day); during standard time it is 00:00–23:59. This logic lives in `weather_utils.compute_cli_windows()` and is critical to correct min/max computation. The `query_start_local` parameter suppresses windows anchored to a prior local calendar date when the UTC query start crosses a midnight boundary.

### Flask REST API endpoints

All endpoints require Flask (`pip install flask`). Served at `http://localhost:5000`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Serve `static/index.html` (landing page) |
| `/app` | GET | Serve `static/app.html` (main web UI) |
| `/api/cities` | GET | List available cities with timezones |
| `/api/observations` | POST | Fetch temperature observations (NWS or IEM) |
| `/api/cli` | POST | Fetch latest CLI report for a city |
| `/api/cli/list` | POST | List available CLI reports for a city |
| `/api/cli/select` | POST | Fetch a specific CLI report by index |
| `/api/forecast` | POST | Fetch NWS gridpoint hourly forecast |
| `/api/forecast/nws-versions` | POST | Fetch multiple NWS forecast versions for comparison |
| `/api/forecast/openmeteo` | POST | Fetch Open-Meteo HRRR/GFS forecast |
| `/api/observed/openmeteo` | POST | Fetch Open-Meteo historical observed data |
| `/api/compare` | POST | Fetch prior-period NWS observations (time-shifted for overlay) |
| `/api/compare/openmeteo` | POST | Fetch prior-period Open-Meteo data (time-shifted for overlay) |
| `/api/highres` | POST | Fetch raw NWS METAR/SPECI observations (no resampling) |
| `/api/mos` | POST | Fetch MOS forecast |
| `/api/kalshi` | POST | Fetch Kalshi prediction-market contracts |
| `/api/kalshi/stream` | GET (SSE) | Stream live Kalshi price updates |
| `/api/notes` | GET / POST | List or create notes |
| `/api/notes/<note_id>` | DELETE / PATCH | Delete or update a note |
| `/api/wethr/stream` | GET (SSE) | Stream wethr data |

**Example request (observations):**
```json
{
  "city": "New York City",
  "start": "2026-05-10",
  "end": "2026-05-11",
  "units": "F",
  "interval": "hourly",
  "source": "nws",
  "tol": 1.0
}
```

### City/station mapping

`weather_utils.py` contains four parallel lookup tables that must stay in sync when adding cities:
- `CITIES` — display name → (lat, lon, tz) for the city centre
- `CLI_LOCATIONS` — display name → ICAO airport code (e.g. `"NYC"`, `"ORD"`) for CLI reports
- `CLI_STATION_COORDS` — display name → (lat, lon) of the official ASOS station used by NWS CLI
- `CLI_STATION_IDS` — display name → ICAO ID (e.g. `"KNYC"`)

When adding a new city:
1. Add entry to `CITIES`
2. Add entry to `CLI_LOCATIONS` (look up the city's airport code)
3. Add entry to `CLI_STATION_COORDS` (the ASOS station's lat/lon, often the same as the airport)
4. Add entry to `CLI_STATION_IDS`

`CLI_LOCATIONS`, `CLI_STATION_COORDS` and `CLI_STATION_IDS` override `CITIES` coordinates when fetching so API lookups resolve to the correct observation station.
