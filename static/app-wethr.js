'use strict';

// ── Wethr.net live Push API integration ───────────────────────────────────────
// Populates the MIN · WETHR and MAX · WETHR top stat cards from the live SSE stream.
// SSE stream delivers ~2–5 min latency official METAR/HF-METAR observations.

// All 21 app cities mapped to their Wethr station codes.
const WETHR_STATIONS = {
  'New York City':  'KNYC',
  'Chicago':        'KMDW',
  'Los Angeles':    'KLAX',
  'Houston':        'KHOU',
  'Miami':          'KMIA',
  'Seattle':        'KSEA',
  'Denver':         'KDEN',
  'Boston':         'KBOS',
  'Atlanta':        'KATL',
  'Phoenix':        'KPHX',
  'Dallas':         'KDFW',
  'Philadelphia':   'KPHL',
  'San Francisco':  'KSFO',
  'Minneapolis':    'KMSP',
  'Detroit':        'KDTW',
  'Las Vegas':      'KLAS',
  'Oklahoma City':  'KOKC',
  'Austin':         'KAUS',
  'Washington DC':  'KDCA',
  'San Antonio':    'KSAT',
  'New Orleans':    'KMSY',
};

// ── SSE state ─────────────────────────────────────────────────────────────────
let _wethrES        = null;
let _wethrESCity    = null;
let _wethrState     = { temp: null, high: null, highTime: null, low: null, lowTime: null, updated: null };

// ── Seed daily high/low from REST observations (runs once per city connection) ─
async function _fetchWethrDailyStats(city) {
  const station = WETHR_STATIONS[city];
  if (!station) return;
  const tz = S.cityTz || 'America/New_York';
  let startIso, endIso;
  try {
    const cityNow = luxon.DateTime.now().setZone(tz);
    startIso = cityNow.startOf('day').toUTC().toISO();
    endIso   = cityNow.toUTC().toISO();
  } catch {
    const now = new Date();
    endIso   = now.toISOString();
    startIso = new Date(now - 24 * 3600000).toISOString();
  }
  try {
    const resp = await fetch('/api/observed/wethr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ station, start: startIso, end: endIso, units: 'F' }),
    });
    if (!resp.ok) return;
    const data = await resp.json();
    const rows = (data.rows || []).filter(r => r.temp != null);
    if (!rows.length) return;
    const highRow = rows.reduce((a, b) => b.temp > a.temp ? b : a);
    const lowRow  = rows.reduce((a, b) => b.temp < a.temp ? b : a);
    if (_wethrESCity !== city) return;  // city changed while fetching
    if (_wethrState.high === null) { _wethrState.high = highRow.temp; _wethrState.highTime = highRow.time; }
    if (_wethrState.low  === null) { _wethrState.low  = lowRow.temp;  _wethrState.lowTime  = lowRow.time;  }
    renderWethrTopCards();
  } catch {}
}

// ── Top stat cards for Wethr day high/low ────────────────────────────────────
function renderWethrTopCards() {
  const elLow  = document.getElementById('val-wethr-low');
  const elHigh = document.getElementById('val-wethr-high');
  if (!elLow || !elHigh) return;

  const st = _wethrState;
  const useCelsius = S.units === 'C';

  function toDisp(f) {
    if (f === null || f === undefined) return '—';
    const val = useCelsius ? ((f - 32) * 5 / 9) : f;
    return val.toFixed(1) + sym();
  }

  function fmtTs(ts) {
    if (!ts) return '';
    const raw = /[Z+\-]\d*$/.test(ts) ? ts : ts + 'Z';
    return new Date(raw).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  elLow.textContent  = toDisp(st.low);
  elHigh.textContent = toDisp(st.high);
  document.getElementById('sub-wethr-low').textContent  = fmtTs(st.lowTime);
  document.getElementById('sub-wethr-high').textContent = fmtTs(st.highTime);
}

// ── SSE connection ─────────────────────────────────────────────────────────────
function startWethrLive(city) {
  const station = WETHR_STATIONS[city];
  if (!station) { stopWethrLive(); return; }

  // Already connected to this city
  if (_wethrES && _wethrESCity === city) return;

  stopWethrLive();
  _wethrESCity = city;
  _wethrState  = { temp: null, high: null, highTime: null, low: null, lowTime: null, updated: null };
  renderWethrTopCards();

  _fetchWethrDailyStats(city);

  const url = `/api/wethr/stream?station=${station}`;
  _wethrES = new EventSource(url);

  _wethrES.addEventListener('observation', e => {
    try {
      const d = JSON.parse(e.data);
      console.log('[wethr] observation keys:', Object.keys(d), d);
      if (d.temperature_fahrenheit !== undefined && d.temperature_fahrenheit !== null) {
        _wethrState.temp = d.temperature_fahrenheit;
      }
      // wethr_high/low are nested: { nws: { value_f, value_c, time_utc }, wu: {...} }
      if (d.wethr_high?.nws?.value_f !== undefined && d.wethr_high.nws.value_f !== null) {
        _wethrState.high = d.wethr_high.nws.value_f;
        _wethrState.highTime = d.wethr_high.nws.time_utc || null;
      }
      if (d.wethr_low?.nws?.value_f !== undefined && d.wethr_low.nws.value_f !== null) {
        _wethrState.low = d.wethr_low.nws.value_f;
        _wethrState.lowTime = d.wethr_low.nws.time_utc || null;
      }
      const rawTs = d.observation_time_utc || new Date().toISOString();
      // Ensure UTC interpretation — append Z if no timezone offset present
      _wethrState.updated = /[Z+\-]\d*$/.test(rawTs) ? rawTs : rawTs + 'Z';
      renderWethrTopCards();
    } catch {}
  });

  // new_high / new_low fire when running day extreme changes
  _wethrES.addEventListener('new_high', e => {
    try {
      const d = JSON.parse(e.data);
      if (d.value_f !== undefined) {
        _wethrState.high = d.value_f;
        _wethrState.highTime = d.time_utc || null;
        renderWethrTopCards();
      }
    } catch {}
  });

  _wethrES.addEventListener('new_low', e => {
    try {
      const d = JSON.parse(e.data);
      if (d.value_f !== undefined) {
        _wethrState.low = d.value_f;
        _wethrState.lowTime = d.time_utc || null;
        renderWethrTopCards();
      }
    } catch {}
  });

  _wethrES.addEventListener('error', (e) => {
    console.warn('[wethr] SSE error/disconnect, readyState:', _wethrES?.readyState, e);
  });
}

function stopWethrLive() {
  if (_wethrES) { _wethrES.close(); _wethrES = null; }
  _wethrESCity = null;
}

// ── Re-render on units toggle (F ↔ C) ─────────────────────────────────────────
document.getElementById('units-toggle').addEventListener('click', () => {
  // S.units is updated by app-controls.js before this fires (same-tick listeners run in order)
  // Use a microtask to ensure S.units has been updated first.
  Promise.resolve().then(() => renderWethrTopCards());
});

