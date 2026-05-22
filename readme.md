# Weather Forecast Dashboard

A Flask web app for cross-referencing multi-source weather forecasts against Kalshi prediction market contract prices to identify mispricings.

## What it does

- Pulls observed temperature data from NWS and IEM (ASOS station network) across 20 US cities
- Overlays 13 forecast model sources: NWS gridpoint, Open-Meteo (HRRR→GFS best-match), and MOS (GFS-MOS + LAMP)
- Streams live Kalshi contract prices via WebSocket
- Lets you compare forecast model spread against market-implied temperatures to spot edges

## Setup

```powershell
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your credentials:

```powershell
Copy-Item .env.example .env
```

Run the server:

```powershell
python server.py
# Open http://localhost:5000
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `KALSHI_KEY_ID` | Kalshi API key UUID — from kalshi.com/account/api |
| `KALSHI_KEY_FILE` | Path to your Kalshi RSA private key PEM file |
| `WETHR_API_KEY` | wethr.net API key for live observation stream |

## Data sources

| Source | Type | What it provides |
|--------|------|-----------------|
| **NWS** (`api.weather.gov`) | Observed + forecast | Hourly ASOS observations, gridpoint hourly forecast, official CLI daily reports (what Kalshi contracts settle against) |
| **IEM** (Iowa Environmental Mesonet) | Observed | More complete ASOS archive — includes SPECIs that NWS occasionally drops; used for high-res mode and as NWS fallback |
| **ASOS** | Sensor network | The underlying sensor infrastructure at airports that both NWS and IEM serve data from; publishes METAR (hourly) and SPECI (condition-triggered) reports |
| **Open-Meteo** | Forecast | HRRR (first ~18 hrs, 3 km resolution) → GFS handoff; independent from NWS forecast pipeline |
| **MOS** (via IEM) | Forecast | Model Output Statistics — statistically bias-corrected GFS output; GFS-MOS (4x/day, 7-day) and LAMP (hourly, 38-hr) |
| **Kalshi** | Prediction market | Live contract prices for daily high/low temperature markets; streamed via WebSocket |
| **wethr.net** | Observed (live) | Real-time observation stream |

## Cities supported

20 US cities: New York City, Chicago, Los Angeles, Houston, Miami, Seattle, Denver, Boston, Atlanta, Phoenix, Dallas, Philadelphia, San Francisco, Minneapolis, Detroit, Las Vegas, Oklahoma City, Austin, Washington DC, San Antonio, New Orleans
