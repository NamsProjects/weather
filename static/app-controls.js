'use strict';

// ── Tab switching ─────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('hidden', c.id !== 'tab-' + tab));
    if (tab === 'kalshi' && S.mos) renderMosGuidance();
  });
});

// ── Presets ───────────────────────────────────────────────────────────────────
const DT_FMT = "yyyy-MM-dd'T'HH:mm";

// Range covering the last hour of the previous CLI day + the selected CLI day.
// DST detection mirrors weather_utils.compute_cli_windows / forecast_cap_utc:
// check .isInDST at noon on the anchor (selected) date — stable proxy.
//   DST active:  start = anchor 12 AM,        end = anchor +1d 1 AM
//   Std time:    start = anchor -1d 11 PM,    end = anchor +1d 12 AM
function cliDayRange(anchor) {
  const noonAnchor = anchor.set({ hour: 12, minute: 0, second: 0, millisecond: 0 });
  const isDst = noonAnchor.isInDST;
  if (isDst) {
    return {
      start: anchor.startOf('day'),
      end:   anchor.startOf('day').plus({ days: 1, hours: 1 }),
    };
  }
  return {
    start: anchor.startOf('day').minus({ hours: 1 }),
    end:   anchor.startOf('day').plus({ days: 1 }),
  };
}

// "Today" in CLI-day terms is not always the same as today's calendar date.
// During DST, the CLI day opens at 01:00 local — so at 00:00–00:59 we're still
// inside *yesterday's* CLI day (which runs 01:00 yesterday → 01:00 today).
// Mirror compute_cli_windows.anchor_date: subtract a day when DST + hour < 1.
function currentCliAnchor() {
  const now = luxon.DateTime.now().setZone(S.cityTz);
  const isDstAtNoon = now.startOf('day').set({ hour: 12 }).isInDST;
  if (isDstAtNoon && now.hour < 1) {
    return now.startOf('day').minus({ days: 1 });
  }
  return now.startOf('day');
}

function applyPreset(key) {
  const todayAnchor = currentCliAnchor();
  let anchor;
  if (key === 'today')          anchor = todayAnchor;
  else if (key === 'yesterday') anchor = todayAnchor.minus({ days: 1 });
  else return;

  const { start, end } = cliDayRange(anchor);
  $('start-input').value = start.toFormat(DT_FMT);
  $('end-input').value   = end.toFormat(DT_FMT);
}

document.querySelectorAll('.preset-btn').forEach(btn =>
  btn.addEventListener('click', () => applyPreset(btn.dataset.p))
);

// ── Toggle switches ───────────────────────────────────────────────────────────
function buildToggle(trackId, onLabel, offLabel, getState, setState) {
  const track = $(trackId);
  function refresh() {
    const on = getState();
    track.classList.toggle('on', on);
    $(onLabel).classList.toggle('on', !on);
    $(offLabel).classList.toggle('on', on);
  }
  function click() { setState(!getState()); refresh(); }
  track.addEventListener('click', click);
  $(onLabel).addEventListener('click', click);
  $(offLabel).addEventListener('click', click);
  refresh();
}

buildToggle('units-toggle',  'lbl-f',   'lbl-c',
  () => S.units === 'C',
  v  => { S.units = v ? 'C' : 'F'; }
);
buildToggle('source-toggle', 'lbl-nws', 'lbl-iem',
  () => S.source === 'IEM',
  v  => { S.source = v ? 'IEM' : 'NWS'; }
);

// ── Sidebar controls ──────────────────────────────────────────────────────────
$('interval-select').addEventListener('change', e => S.interval = e.target.value);
$('tol-input').addEventListener('change', e => { S.tolerance = parseFloat(e.target.value) || 1.0; });

$('cli-window-only').addEventListener('change', e => {
  S.cliWindowOnly = e.target.checked;
  if (S.obs) renderCards();
});

$('forecast-enable').addEventListener('change', e => {
  S.forecastEnabled = e.target.checked;
});

$('compare-enable').addEventListener('change', e => {
  S.compareEnabled = e.target.checked;
  $('compare-status').textContent = '';
});
$('compare-offset').addEventListener('change', e => S.compareOffset = parseInt(e.target.value));

// ── Cities ────────────────────────────────────────────────────────────────────
const cityTimezones = {};  // city name → IANA tz, populated on load
const cityDstMap    = {};  // city name → boolean DST flag

async function loadCities() {
  try {
    const cities = await (await fetch('/api/cities')).json();
    cities.forEach(c => { cityTimezones[c.name] = c.tz; cityDstMap[c.name] = c.dst !== false; });
    const sel = $('city-select');
    cities.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.name; opt.textContent = c.name;
      if (c.name === 'New York City') opt.selected = true;
      sel.appendChild(opt);
    });
    $('city-select').addEventListener('change', e => {
      S.city   = e.target.value;
      S.cityTz  = cityTimezones[e.target.value] || S.cityTz;
      S.cityDst = cityDstMap[e.target.value] !== false;
      stopKalshiLive();

      // Wipe stale data + UI from the previous city so a failed fetch can't
      // leave the old city's chart/station/cards on screen with the new city name.
      S.fetchToken++;
      S.obs = S.forecast = S.omFcast = S.omObs = S.compare = S.omCompare = S.mos = null;
      S.cli = null; S.cliList = []; S.kalshi = null;
      S.nwsVersions = null; S.nwsVerSelected = {};

      clearChartView();
      if (typeof clearHighResView === 'function') clearHighResView();

      ['val-station','val-records','val-min','val-max',
       'sub-station','sub-records','sub-min','sub-max','dur-min','dur-max']
        .forEach(id => { const el = $(id); if (el) el.textContent = '—'; });

      ['hr-station','hr-start','hr-end'].forEach(id => {
        const el = $(id); if (el) el.value = '';
      });

      if (typeof renderFcStrip === 'function') renderFcStrip();
      if (typeof renderNwsVersionList === 'function') renderNwsVersionList();
      if (typeof ntUpdateCityDisplay === 'function') ntUpdateCityDisplay();
    });
    S.city   = sel.value;
    S.cityTz  = cityTimezones[S.city] || S.cityTz;
    S.cityDst = cityDstMap[S.city] !== false;
  } catch {
    setStatus('Could not load city list.', 'err');
  }
}
