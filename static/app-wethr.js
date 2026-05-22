'use strict';

// ── Wethr.net live Push API integration ───────────────────────────────────────
// Displays real-time temperature + running day high/low in the High-Res tab.
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

// Cities where the Wethr station differs from the CLI-reported station.
const WETHR_CLI_MISMATCH = {};

// ── SSE state ─────────────────────────────────────────────────────────────────
let _wethrES        = null;
let _wethrESCity    = null;
let _wethrState     = { temp: null, high: null, low: null, updated: null };

// ── Render ────────────────────────────────────────────────────────────────────
function renderWethrCard() {
  const row     = document.getElementById('wethr-live-row');
  if (!row) return;

  // Use the actually-connected city (_wethrESCity) so the label always matches
  // the live stream, not S.city which may not have updated yet on city change.
  const city    = _wethrESCity || S.city;
  const station = WETHR_STATIONS[city];
  if (!station) {
    row.innerHTML = '';
    return;
  }

  const st      = _wethrState;
  const useCelsius = S.units === 'C';

  function toDisp(f) {
    if (f === null || f === undefined) return '—';
    const val = useCelsius ? ((f - 32) * 5 / 9) : f;
    return val.toFixed(1) + sym();
  }

  const mismatch = WETHR_CLI_MISMATCH[city];
  const connected = _wethrES !== null;
  const dotColor  = connected ? '#4caf50' : '#888';
  const dotPulse  = connected && st.temp !== null ? 'wethr-dot-pulse' : '';

  const updatedTxt = st.updated
    ? new Date(st.updated).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—';

  row.innerHTML = `
    <div class="wethr-card">
      <div class="wethr-card-header">
        <span class="wethr-dot ${dotPulse}" style="background:${dotColor}"></span>
        <span class="wethr-label">LIVE · ${station}</span>
        <span class="wethr-source">wethr.net Push</span>
      </div>
      <div class="wethr-values">
        <div class="wethr-kpi">
          <div class="wethr-kpi-label">Current Temp</div>
          <div class="wethr-kpi-value wethr-temp">${toDisp(st.temp)}</div>
          <div class="wethr-kpi-sub">as of ${updatedTxt}</div>
        </div>
        <div class="wethr-kpi">
          <div class="wethr-kpi-label">Day High</div>
          <div class="wethr-kpi-value wethr-high">${toDisp(st.high)}</div>
        </div>
        <div class="wethr-kpi">
          <div class="wethr-kpi-label">Day Low</div>
          <div class="wethr-kpi-value wethr-low">${toDisp(st.low)}</div>
        </div>
      </div>
      ${mismatch ? `<div class="wethr-mismatch">⚠ ${mismatch}</div>` : ''}
    </div>
  `;
}

// ── SSE connection ─────────────────────────────────────────────────────────────
function startWethrLive(city) {
  const station = WETHR_STATIONS[city];
  if (!station) { stopWethrLive(); return; }

  // Already connected to this city
  if (_wethrES && _wethrESCity === city) return;

  stopWethrLive();
  _wethrESCity = city;
  _wethrState  = { temp: null, high: null, low: null, updated: null };

  const url = `/api/wethr/stream?station=${station}`;
  _wethrES = new EventSource(url);
  renderWethrCard();  // render after _wethrES is set so dot shows green

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
      }
      if (d.wethr_low?.nws?.value_f !== undefined && d.wethr_low.nws.value_f !== null) {
        _wethrState.low = d.wethr_low.nws.value_f;
      }
      const rawTs = d.observation_time_utc || new Date().toISOString();
      // Ensure UTC interpretation — append Z if no timezone offset present
      _wethrState.updated = /[Z+\-]\d*$/.test(rawTs) ? rawTs : rawTs + 'Z';
      renderWethrCard();
    } catch {}
  });

  // new_high / new_low fire when running day extreme changes
  _wethrES.addEventListener('new_high', e => {
    try {
      const d = JSON.parse(e.data);
      if (d.value_f !== undefined) { _wethrState.high = d.value_f; renderWethrCard(); }
    } catch {}
  });

  _wethrES.addEventListener('new_low', e => {
    try {
      const d = JSON.parse(e.data);
      if (d.value_f !== undefined) { _wethrState.low = d.value_f; renderWethrCard(); }
    } catch {}
  });

  _wethrES.addEventListener('error', (e) => {
    console.warn('[wethr] SSE error/disconnect, readyState:', _wethrES?.readyState, e);
    // Connection dropped — clear dot, keep last values, retry handled by browser
    renderWethrCard();
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
  Promise.resolve().then(() => renderWethrCard());
});

// ── Init on page load ─────────────────────────────────────────────────────────
startWethrLive(S.city);
