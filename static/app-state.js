'use strict';

// ── Central state ─────────────────────────────────────────────────────────────
const S = {
  units:          'F',
  source:         'NWS',
  interval:       '30min',
  tolerance:      1.0,
  cliWindowOnly:  true,
  forecastEnabled: true,
  compareEnabled: false,
  compareOffset:  1,
  fetchToken:     0,
  city:           'New York City',

  cityTz:      'America/New_York',  // IANA tz of selected city, set on fetch
  cityDst:     true,               // whether selected city observes DST
  browserTz:   'UTC',               // Browser's local timezone (auto-detected)
  obs:         null,   // full /api/observations result
  forecast:    null,   // {rows, update_time}
  omFcast:     null,   // {rows, header_text, model_runs}
  omObs:       null,   // {rows}  — Open-Meteo HRRR analysis for observed window
  compare:     null,   // {rows, offset_days}
  omCompare:   null,   // {rows, offset_days}  — OM HRRR analysis for prior period
  cli:         null,   // current CLI result
  cliList:     [],     // [{id, issuanceTime, label}, ...]
  kalshi:      null,   // last /api/kalshi response {city, high_markets, low_markets, ...}
  mos:         null,   // {city, station, gfs:{rows,n_x_vals,runtime}, lav:{rows,n_x_vals,runtime}}
  wethrObs:    null,   // {rows} — Wethr METAR/HF-METAR/SPECI obs for main chart
  nwsVersions:     null,   // {versions_by_date: {date: [{label,index,times,temps}]}}
  nwsVerSelected:  {},     // {"date|idx": bool}
  nwsVerEnabled:   false,
};

// ── Detect browser timezone ────────────────────────────────────────────────────
try {
  S.browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
} catch {
  S.browserTz = 'UTC';
}

// ── DOM helpers ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function setStatus(msg, cls = '') {
  const el = $('main-status');
  el.textContent = msg;
  el.className = 'status-bar ' + cls;
}

function pad(n) { return String(n).padStart(2, '0'); }

function toDatetimeLocal(d) {
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}` +
         `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function sym() { return S.units === 'F' ? '°F' : '°C'; }

function fmtTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleString([], { month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' });
  } catch { return iso; }
}
