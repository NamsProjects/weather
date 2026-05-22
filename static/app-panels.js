'use strict';

// ── NWS Forecast Versions ────────────────────────────────────────────────────

async function fetchNwsVersions() {
  if (!S.city || !S.obs) {
    $('nws-ver-status').textContent = 'Fetch observations first.';
    return;
  }

  // Use CLI window dates (actual observation period) when available so the 1 AM
  // next-day forecast cap in the form end input doesn't pull in next-day versions.
  let startDate, endDate;
  if (S.obs.cli_windows && S.obs.cli_windows.length) {
    startDate = S.obs.cli_windows[0].start.slice(0, 10);
    endDate   = S.obs.cli_windows[S.obs.cli_windows.length - 1].start.slice(0, 10);
  } else {
    const start = $('start-input').value;
    const end   = $('end-input').value;
    if (!start || !end) { $('nws-ver-status').textContent = 'Fetch observations first.'; return; }
    startDate = start.slice(0, 10);
    endDate   = end.slice(0, 10);
  }

  $('nws-ver-status').textContent = 'Fetching…';
  $('nws-ver-fetch-btn').disabled = true;

  try {
    const res = await fetch('/api/forecast/nws-versions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        city: S.city,
        start: startDate,
        end: endDate,
        units: S.units,
      }),
    });
    const data = await res.json();

    if (data.error) {
      $('nws-ver-status').textContent = `Error: ${data.error}`;
      return;
    }

    S.nwsVersions = data;
    S.nwsVerSelected = {};

    // Auto-select newest version per date
    const vbd = data.versions_by_date || {};
    let total = 0;
    for (const [dateStr, versions] of Object.entries(vbd)) {
      versions.forEach((ver, idx) => {
        const key = `${dateStr}|${idx}`;
        S.nwsVerSelected[key] = (idx === versions.length - 1);
      });
      total += versions.length;
    }

    const dateCount = Object.keys(vbd).length;
    $('nws-ver-status').textContent = `${total} version(s) across ${dateCount} date(s)`;
    renderNwsVersionList();
    renderChart();
  } catch (e) {
    $('nws-ver-status').textContent = `Network error: ${e.message}`;
  } finally {
    $('nws-ver-fetch-btn').disabled = false;
  }
}

let _nwsVerExpanded = false;

function _buildVersionRow(dateStr, idx, ver, color, isLatest) {
  const key = `${dateStr}|${idx}`;
  const row = document.createElement('label');
  row.style.cssText = 'display:flex;align-items:center;gap:6px;cursor:pointer;margin-bottom:2px';

  const swatch = document.createElement('span');
  swatch.style.cssText = `display:inline-block;width:10px;height:10px;border-radius:2px;background:${color};flex-shrink:0`;

  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = !!S.nwsVerSelected[key];
  cb.style.accentColor = color;
  cb.onchange = () => { S.nwsVerSelected[key] = cb.checked; renderChart(); };

  const lbl = document.createElement('span');
  lbl.style.cssText = `font-size:9px;font-family:Consolas,monospace;color:${isLatest ? '#e2e8f0' : '#888'}`;

  let labelText = ver.label + (isLatest ? ' ← latest' : '');
  if (ver.issued_at) {
    let localTime = '';
    const m = ver.issued_at.match(/~(\d{2})z/);
    if (m && typeof luxon !== 'undefined') {
      const utcHour = parseInt(m[1], 10);
      const dt = luxon.DateTime.fromObject(
        { year: parseInt(dateStr.slice(0,4)), month: parseInt(dateStr.slice(5,7)), day: parseInt(dateStr.slice(8,10)), hour: utcHour },
        { zone: 'UTC' }
      ).setZone(S.cityTz);
      localTime = `  ~${dt.toFormat('HH:mm')} local`;
    }
    if (localTime) labelText += localTime;
    else labelText += `  ${ver.issued_at}`;
  }
  lbl.textContent = labelText;

  row.appendChild(swatch);
  row.appendChild(cb);
  row.appendChild(lbl);
  return row;
}

function renderNwsVersionList() {
  const container = $('nws-ver-list');
  container.innerHTML = '';

  const vbd = S.nwsVersions && S.nwsVersions.versions_by_date;
  if (!vbd || !Object.keys(vbd).length) {
    container.style.display = 'none';
    return;
  }
  container.style.display = 'block';

  const palette = ['#c4b5fd','#a78bfa','#8b5cf6','#7c3aed','#6d28d9'];

  for (const [dateStr, versions] of Object.entries(vbd).sort()) {
    const n = versions.length;

    const hdr = document.createElement('div');
    hdr.style.cssText = 'font-size:9px;color:#a78bfa;font-family:Consolas,monospace;margin:4px 0 2px';
    hdr.textContent = dateStr + ` (${n})`;
    container.appendChild(hdr);

    // Latest version first (highest index = newest)
    const latestIdx = n - 1;
    container.appendChild(
      _buildVersionRow(dateStr, latestIdx, versions[latestIdx],
        palette[Math.min(latestIdx, palette.length - 1)], true)
    );

    if (n > 1) {
      // Expand/collapse toggle for older versions
      const toggleBtn = document.createElement('button');
      toggleBtn.className = 'ghost-btn';
      toggleBtn.style.cssText = 'font-size:9px;padding:2px 0;color:#666;width:100%;text-align:left;margin:2px 0';
      toggleBtn.textContent = _nwsVerExpanded ? `▲ hide older` : `▼ ${n - 1} older`;

      const olderWrap = document.createElement('div');
      olderWrap.style.display = _nwsVerExpanded ? 'block' : 'none';

      // Older versions newest-first (n-2 down to 0)
      for (let idx = n - 2; idx >= 0; idx--) {
        olderWrap.appendChild(
          _buildVersionRow(dateStr, idx, versions[idx],
            palette[Math.min(idx, palette.length - 1)], false)
        );
      }

      toggleBtn.onclick = () => {
        _nwsVerExpanded = !_nwsVerExpanded;
        toggleBtn.textContent = _nwsVerExpanded ? `▲ hide older` : `▼ ${n - 1} older`;
        olderWrap.style.display = _nwsVerExpanded ? 'block' : 'none';
      };

      container.appendChild(toggleBtn);
      container.appendChild(olderWrap);
    }
  }
}

$('nws-ver-fetch-btn').addEventListener('click', fetchNwsVersions);

// ── Kalshi markets ───────────────────────────────────────────────────────────
async function fetchKalshi() {
  const city = S.city;
  if (!city) return;

  $('kalshi-status').textContent = 'Fetching Kalshi contracts…';
  $('kalshi-summary').innerHTML = '';
  $('kalshi-panel-high').innerHTML = '';
  $('kalshi-panel-low').innerHTML = '';

  try {
    const fcast_high = _extractForecastHigh();
    const fcast_low = _extractForecastLow();

    const res = await fetch('/api/kalshi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        city,
        forecast_high: fcast_high,
        forecast_low: fcast_low,
      }),
    });
    const data = await res.json();

    if (data.error) {
      $('kalshi-status').textContent = `Error: ${data.error}`;
      return;
    }

    renderKalshi(data);
  } catch (e) {
    $('kalshi-status').textContent = `Network error: ${e.message}`;
  }
}

function _extractForecastHigh() {
  if (S.forecast && S.forecast.rows && S.forecast.rows.length) {
    const temps = S.forecast.rows.map(r => r.temp);
    return Math.max(...temps);
  }
  if (S.omFcast && S.omFcast.rows && S.omFcast.rows.length) {
    const temps = S.omFcast.rows.map(r => r.temp);
    return Math.max(...temps);
  }
  if (S.obs && S.obs.max_temp != null) return S.obs.max_temp;
  return null;
}

function _extractForecastLow() {
  if (S.forecast && S.forecast.rows && S.forecast.rows.length) {
    const temps = S.forecast.rows.map(r => r.temp);
    return Math.min(...temps);
  }
  if (S.omFcast && S.omFcast.rows && S.omFcast.rows.length) {
    const temps = S.omFcast.rows.map(r => r.temp);
    return Math.min(...temps);
  }
  if (S.obs && S.obs.min_temp != null) return S.obs.min_temp;
  return null;
}

function _tempHitsBracket(temp, subtitle) {
  if (temp == null) return false;
  const s = subtitle.replace('°', '').toLowerCase();
  if (s.includes('below')) {
    try {
      const v = parseFloat(s.split()[0]);
      return temp <= v;
    } catch { return false; }
  }
  if (s.includes('above')) {
    try {
      const v = parseFloat(s.split()[0]);
      return temp >= v;
    } catch { return false; }
  }
  const parts = s.split(' to ');
  if (parts.length === 2) {
    try {
      const lo = parseFloat(parts[0]);
      const hi = parseFloat(parts[1]);
      return temp >= lo && temp <= hi;
    } catch { return false; }
  }
  return false;
}

function renderKalshi(data) {
  const high = data.high_markets || [];
  const low  = data.low_markets  || [];
  const total = high.length + low.length;

  S.kalshi = data;

  if (total === 0) {
    $('kalshi-status').textContent = `No open contracts for ${data.city} today.`;
    return;
  }

  $('kalshi-summary').innerHTML = '';
  renderMosGuidance();
  renderKalshiPanel($('kalshi-panel-high'), 'HIGH TEMP CONTRACTS', high);
  renderKalshiPanel($('kalshi-panel-low'),  'LOW TEMP CONTRACTS',  low);

  $('kalshi-status').textContent = `✓ ${data.city} · ${high.length} high / ${low.length} low contracts`;
  startKalshiLive(data.city);
}

// ── MOS guidance block (above market tables) ──────────────────────────────────
function renderMosGuidance() {
  const el = $('kalshi-summary');
  if (!el) return;

  if (!S.mos || S.mos === 'loading') {
    el.innerHTML = `<div class="mos-guidance mos-loading">MOS loading…</div>`;
    return;
  }
  if (S.mos === 'error') {
    el.innerHTML = `<div class="mos-guidance mos-error">MOS unavailable</div>`;
    return;
  }

  const sym_ = sym();
  const gfs  = S.mos.gfs  || {};
  const lav  = S.mos.lav  || {};

  function nextNx(nxArr, type) {
    if (!nxArr || !nxArr.length) return null;
    const now = Date.now();
    const upcoming = nxArr.filter(v => v.type === type && new Date(v.time).getTime() >= now);
    if (upcoming.length) return upcoming[0];
    const past = nxArr.filter(v => v.type === type);
    return past.length ? past[past.length - 1] : null;
  }

  const gfsHigh = nextNx(gfs.n_x_vals, 'high');
  const gfsLow  = nextNx(gfs.n_x_vals, 'low');

  function rowMinMax(rows) {
    if (!rows || !rows.length) return null;
    const temps = rows.map(r => r.temp).filter(v => v != null);
    if (!temps.length) return null;
    return { lo: Math.min(...temps), hi: Math.max(...temps) };
  }

  const lavR  = rowMinMax(lav.rows);
  const nwsR  = (S.forecast  && S.forecast.rows)  ? rowMinMax(S.forecast.rows)  : null;
  const omFR  = (S.omFcast   && S.omFcast.rows)   ? rowMinMax(S.omFcast.rows)   : null;
  const omObR = (S.omObs     && S.omObs.rows)      ? rowMinMax(S.omObs.rows)     : null;

  function fmtRun(iso) {
    if (!iso) return '?';
    try {
      return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  }

  function kpiCell(label, hi, lo, hiColor, loColor) {
    const hiDisp = hi != null ? `${hi}${sym_}` : '—';
    const loDisp = lo != null ? `${lo}${sym_}` : '—';
    return `
      <div class="kpi-cell">
        <span class="kpi-cell-label">${label}</span>
        <div class="kpi-cell-vals">
          <span class="kpi-val" style="color:${hiColor}">${hiDisp}</span>
          <span class="kpi-sep">/</span>
          <span class="kpi-val" style="color:${loColor}">${loDisp}</span>
        </div>
      </div>`;
  }

  const stationLabel = S.mos.station || '';
  const gfsRunLabel  = gfs.runtime ? `GFS run ${fmtRun(gfs.runtime)}` : '';
  const lavRunLabel  = lav.runtime ? `LAMP run ${fmtRun(lav.runtime)}` : '';
  const metaStr = [stationLabel, gfsRunLabel, lavRunLabel].filter(Boolean).join(' · ');

  const nwsObsHi = S.obs ? S.obs.max_temp : null;
  const nwsObsLo = S.obs ? S.obs.min_temp : null;

  el.innerHTML = `
    <div class="mos-guidance mos-slicer">
      <div class="mos-header">
        <span class="mos-title">WEATHER SUMMARY</span>
        <span class="mos-meta">${metaStr}</span>
        <span class="kpi-col-heads">
          <span class="kpi-col-head">HIGH</span>
          <span class="kpi-col-head-sep">/</span>
          <span class="kpi-col-head">LOW</span>
        </span>
      </div>
      <div class="kpi-slicer">
        <div class="kpi-section">
          <span class="kpi-section-title">OBSERVED</span>
          ${kpiCell('NWS', nwsObsHi, nwsObsLo, '#facc15', '#7ec8e3')}
          ${omObR ? kpiCell('Open-Meteo', omObR.hi, omObR.lo, '#facc15', '#7ec8e3') : ''}
        </div>
        <div class="kpi-divider"></div>
        <div class="kpi-section">
          <span class="kpi-section-title">FORECAST</span>
          ${nwsR ? kpiCell('NWS Gridpoint', nwsR.hi, nwsR.lo, '#4f8ef7', '#4f8ef7') : ''}
          ${omFR ? kpiCell('Open-Meteo',    omFR.hi, omFR.lo, '#4f8ef7', '#4f8ef7') : ''}
        </div>
        <div class="kpi-divider"></div>
        <div class="kpi-section">
          <span class="kpi-section-title">MOS</span>
          ${kpiCell('GFS', gfsHigh ? gfsHigh.value : null, gfsLow ? gfsLow.value : null, '#a78bfa', '#a78bfa')}
          ${lavR ? kpiCell('LAMP', lavR.hi, lavR.lo, '#4ade80', '#4ade80') : ''}
        </div>
      </div>
    </div>`;
}

// ── Live feed (SSE) ───────────────────────────────────────────────────────────
let _kalshiES = null;
let _kalshiESCity = null;

function startKalshiLive(city) {
  if (_kalshiES && _kalshiESCity === city) return;
  stopKalshiLive();
  _kalshiESCity = city;
  _kalshiES = new EventSource(`/api/kalshi/stream?city=${encodeURIComponent(city)}`);

  _kalshiES.onmessage = e => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.error) {
        console.warn('[Kalshi live]', msg.error);
        stopKalshiLive();  // WS not configured — stop reconnection loop
        return;
      }
      // Only mark live when real ticker data arrives
      const el = $('kalshi-status');
      if (el && !el.textContent.includes('●')) el.textContent += '  ·  live ●';
      if (msg.city === S.city) patchKalshiTicker(msg.ticker, msg.data);
    } catch {}
  };

  _kalshiES.addEventListener('error', () => {
    const el = $('kalshi-status');
    if (el) el.textContent = el.textContent.replace(/\s+·\s+live ●/, '');
  });
}

function stopKalshiLive() {
  if (_kalshiES) { _kalshiES.close(); _kalshiES = null; _kalshiESCity = null; }
  const el = $('kalshi-status');
  if (el) el.textContent = el.textContent.replace(/\s+·\s+live ●/, '');
}

function patchKalshiTicker(ticker, data) {
  const row = document.querySelector(`.kalshi-row[data-ticker="${CSS.escape(ticker)}"]`);
  if (!row) return;

  // Kalshi ticker WS payload uses *_dollars strings (e.g. "0.480").
  // REST exposes last_price_dollars; WS exposes price_dollars — accept either.
  // WS never emits no_bid_dollars; in a binary market: no_bid = 1 - yes_ask.
  const priceStr  = data.price_dollars  ?? data.last_price_dollars;
  const yesBidStr = data.yes_bid_dollars;
  const yesAskStr = data.yes_ask_dollars;
  const noBidStr  = data.no_bid_dollars
    ?? (yesAskStr != null ? String(1 - parseFloat(yesAskStr)) : null);

  const updates = {};
  if (priceStr  != null) updates.pct     = Math.round(parseFloat(priceStr) * 100) + '%';
  if (yesBidStr != null) updates.yes_bid = _dollarsTocents(yesBidStr);
  if (noBidStr  != null) updates.no_bid  = _dollarsTocents(noBidStr);

  let changed = false;
  for (const [field, val] of Object.entries(updates)) {
    const cell = row.querySelector(`[data-field="${field}"]`);
    if (cell && cell.textContent !== val) { cell.textContent = val; changed = true; }
  }

  if (changed) {
    row.classList.remove('tick-flash');
    void row.offsetWidth;
    row.classList.add('tick-flash');
  }
}

function renderKalshiPanel(panelEl, title, markets) {
  panelEl.innerHTML = '';
  if (!markets.length) {
    panelEl.style.display = 'none';
    return;
  }
  panelEl.style.display = '';

  let expanded = panelEl._expanded || false;
  let sortMode = panelEl._sortMode || 'chance'; // 'chance' | 'temp'

  const tempSorted   = [...markets]; // API returns temp-sorted
  const chanceSorted = [...markets].sort((a, b) =>
    (parseFloat(b.last_price_dollars) || 0) - (parseFloat(a.last_price_dollars) || 0)
  );

  const hdr = document.createElement('div');
  hdr.className = 'kalshi-panel-header';
  hdr.innerHTML = `
    <span class="kalshi-panel-title">${title}</span>
    <div style="display:flex;gap:6px;align-items:center">
      <button class="kalshi-sort-btn">sort: ${sortMode === 'chance' ? '% chance' : 'temp'}</button>
      <button class="kalshi-expand-btn">${expanded ? '◂ collapse' : '▸ expand'}</button>
    </div>`;
  panelEl.appendChild(hdr);

  const expandBtn = hdr.querySelector('.kalshi-expand-btn');
  const sortBtn   = hdr.querySelector('.kalshi-sort-btn');

  const scrollWrap = document.createElement('div');
  scrollWrap.className = 'kalshi-table-scroll';
  panelEl.appendChild(scrollWrap);

  function getSorted() {
    return sortMode === 'chance' ? chanceSorted : tempSorted;
  }

  function buildTable() {
    scrollWrap.innerHTML = '';

    const rowCls = expanded ? 'expanded' : 'compact';
    const table  = document.createElement('div');
    table.className = 'kalshi-table';

    const hdrRow = document.createElement('div');
    hdrRow.className = `kalshi-row header ${rowCls}`;
    const compactHdrs = ['Range', '% Chance', 'Yes ¢', 'No ¢'];
    const extraHdrs   = ['Volume', 'OI', 'Closes'];
    (expanded ? [...compactHdrs, ...extraHdrs] : compactHdrs).forEach(h => {
      const c = document.createElement('div');
      c.className = 'kalshi-cell header';
      c.textContent = h;
      hdrRow.appendChild(c);
    });
    table.appendChild(hdrRow);

    getSorted().forEach(m => {
      const row = document.createElement('div');
      row.className = `kalshi-row ${rowCls}`;

      const subtitle = m.yes_sub_title || '—';
      const yes_bid  = _dollarsTocents(m.yes_bid_dollars);
      const pct_str  = m.last_price_dollars ? Math.round(parseFloat(m.last_price_dollars)*100)+'%' : '—';

      const compactCols = [
        { text: subtitle, cls: 'label', field: '' },
        { text: pct_str,  cls: '',      field: 'pct' },
        { text: yes_bid,  cls: 'yes',   field: 'yes_bid' },
        { text: _dollarsTocents(m.no_bid_dollars), cls: 'no', field: 'no_bid' },
      ];
      const extraCols = [
        { text: _fmt_volume(m.volume_fp),        cls: 'vol', field: '' },
        { text: _fmt_volume(m.open_interest_fp), cls: 'vol', field: '' },
        { text: _fmt_close_time(m.close_time),   cls: '',    field: '' },
      ];
      row.dataset.ticker = m.ticker || '';
      (expanded ? [...compactCols, ...extraCols] : compactCols).forEach(col => {
        const c = document.createElement('div');
        c.className = 'kalshi-cell ' + col.cls;
        c.textContent = col.text;
        if (col.field) c.dataset.field = col.field;
        row.appendChild(c);
      });
      table.appendChild(row);
    });

    scrollWrap.appendChild(table);
  }

  buildTable();

  expandBtn.addEventListener('click', () => {
    expanded = !expanded;
    panelEl._expanded = expanded;
    expandBtn.textContent = expanded ? '◂ collapse' : '▸ expand';
    buildTable();
  });

  sortBtn.addEventListener('click', () => {
    sortMode = sortMode === 'chance' ? 'temp' : 'chance';
    panelEl._sortMode = sortMode;
    sortBtn.textContent = `sort: ${sortMode === 'chance' ? '% chance' : 'temp'}`;
    buildTable();
  });
}

function _fmt_volume(val) {
  try {
    const v = parseFloat(val);
    if (v >= 1_000_000) return `$${(v/1_000_000).toFixed(1)}M`;
    if (v >= 1_000) return `$${(v/1_000).toFixed(1)}K`;
    return `$${v.toFixed(0)}`;
  } catch { return '—'; }
}

function _dollarsTocents(val) {
  try {
    const cents = Math.round(parseFloat(val) * 100);
    return cents + '¢';
  } catch { return '—'; }
}

function _fmt_close_time(close_time_str) {
  if (!close_time_str) return '—';
  try {
    const ct = new Date(close_time_str);
    const now = new Date();
    const delta = ct - now;
    if (delta <= 0) return 'CLOSED';
    const h = Math.floor(delta / 3600000);
    const m = Math.floor((delta % 3600000) / 60000);
    if (h > 0) return `${h}h ${m}m`;
    const s = Math.floor((delta % 60000) / 1000);
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  } catch { return '—'; }
}

// ── High-Res tab ─────────────────────────────────────────────────────────────
let hrChart = null;

// Wipe the High-Res chart + state. Called on city change so stale obs
// from a previous city don't linger when the new city's fetch fails.
function clearHighResView() {
  if (hrChart) { try { hrChart.destroy(); } catch {} hrChart = null; }
  HR.station = ''; HR.start = ''; HR.end = '';
  const status = $('hr-status'); if (status) status.textContent = '';
}

const HR = {
  station: '',
  start:   '',
  end:     '',
  source:  'nws',
  rows:    null,
};

function hrGetSource() {
  const el = document.querySelector('input[name="hr-source"]:checked');
  return el ? el.value : 'nws';
}

async function fetchHighRes() {
  const station = $('hr-station').value.trim().toUpperCase();
  const start   = $('hr-start').value.trim();
  const end     = $('hr-end').value.trim();
  const source  = hrGetSource();

  if (!station || !start || !end) {
    hrSetStatus('Station, start, and end are required.', 'err');
    return;
  }

  HR.station = station;
  HR.start   = start;
  HR.end     = end;
  HR.source  = source;

  const btn = $('hr-fetch-btn');
  btn.disabled = true;
  hrSetStatus(`Fetching ${source === 'iem' ? 'IEM METAR+SPECI' : 'NWS METAR'} for ${station}…`);

  try {
    const res = await fetch('/api/highres', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ station, start, end, source, units: S.units }),
    });
    const data = await res.json();
    btn.disabled = false;

    if (data.error) {
      hrSetStatus('Error: ' + data.error, 'err');
      return;
    }

    HR.rows = data.rows || [];
    if (!HR.rows.length) {
      hrSetStatus('No observations returned for this period.');
      return;
    }

    renderHighResChart(HR.rows, station, source);
  } catch (e) {
    btn.disabled = false;
    hrSetStatus('Network error: ' + e.message, 'err');
  }
}

function hrSetStatus(msg, cls = '') {
  const el = $('hr-status');
  el.textContent = msg;
  el.className = 'hr-status ' + cls;
}

function renderHighResChart(rows, station, source) {
  const placeholder = $('hr-placeholder');
  placeholder.style.display = 'none';

  const sym_ = sym();

  const times  = rows.map(r => new Date(r.time));
  const temps  = rows.map(r => r.temp);
  const dews   = rows.map(r => r.dewpoint);
  const hasDew = dews.some(d => d !== null && d !== undefined);

  let minVal = Infinity, maxVal = -Infinity, minIdx = 0, maxIdx = 0;
  temps.forEach((t, i) => {
    if (t < minVal) { minVal = t; minIdx = i; }
    if (t > maxVal) { maxVal = t; maxIdx = i; }
  });
  const minTime = times[minIdx];
  const maxTime = times[maxIdx];

  let dewAtMin = null;
  if (hasDew) {
    const validDewPairs = rows
      .map((r, i) => ({ t: times[i].getTime(), d: r.dewpoint }))
      .filter(p => p.d !== null && p.d !== undefined);
    if (validDewPairs.length) {
      const mt = minTime.getTime();
      let lo = validDewPairs[0], hi = validDewPairs[validDewPairs.length - 1];
      for (let i = 0; i < validDewPairs.length - 1; i++) {
        if (validDewPairs[i].t <= mt && validDewPairs[i+1].t >= mt) {
          lo = validDewPairs[i]; hi = validDewPairs[i+1]; break;
        }
      }
      if (hi.t === lo.t) {
        dewAtMin = lo.d;
      } else {
        const frac = (mt - lo.t) / (hi.t - lo.t);
        dewAtMin = lo.d + frac * (hi.d - lo.d);
      }
    }
  }

  const tempPts = rows.map((r, i) => ({ x: times[i], y: r.temp }));
  const datasets = [];

  if (hasDew) {
    const dewPts = rows
      .map((r, i) => r.dewpoint !== null && r.dewpoint !== undefined
        ? { x: times[i], y: r.dewpoint } : null)
      .filter(Boolean);
    datasets.push({
      label: 'Dew point (floor)',
      data: dewPts,
      borderColor: '#a78bfa',
      backgroundColor: 'rgba(167,139,250,0.10)',
      borderDash: [5, 4],
      borderWidth: 1.2,
      pointRadius: 0,
      fill: '+1',
      tension: 0,
      order: 2,
    });
  }

  const showDots = rows.length <= 300;
  datasets.push({
    label: `Observed (${sym_})`,
    data: tempPts,
    borderColor: '#38bdf8',
    backgroundColor: 'rgba(56,189,248,0.12)',
    borderWidth: 1.5,
    pointRadius: showDots ? 3 : 0,
    pointHoverRadius: 5,
    pointBackgroundColor: '#38bdf8',
    pointBorderColor: '#0f1117',
    pointBorderWidth: 0.8,
    fill: hasDew ? false : true,
    tension: 0,
    order: 1,
  });

  const annotations = {
    minPt: {
      type: 'point',
      xValue: minTime, yValue: minVal,
      backgroundColor: '#3ecf8e', radius: 6,
      borderColor: '#fff', borderWidth: 1.5,
    },
    minLbl: {
      type: 'label',
      xValue: minTime, yValue: minVal,
      content: buildMinLabel(minVal, sym_, minTime, dewAtMin),
      color: '#3ecf8e',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: 6, yAdjust: hasDew ? -52 : -28,
      backgroundColor: 'transparent',
      textAlign: 'left',
    },
    maxPt: {
      type: 'point',
      xValue: maxTime, yValue: maxVal,
      backgroundColor: '#f5a623', radius: 6,
      borderColor: '#fff', borderWidth: 1.5,
    },
    maxLbl: {
      type: 'label',
      xValue: maxTime, yValue: maxVal,
      content: [`${maxVal.toFixed(2)}${sym_}`, fmtTime(maxTime.toISOString())],
      color: '#f5a623',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: 6, yAdjust: 12,
      backgroundColor: 'transparent',
      textAlign: 'left',
    },
  };

  if (hrChart) { hrChart.destroy(); hrChart = null; }

  const canvas = $('hr-chart');
  hrChart = new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          type: 'time',
          min: times[0],
          max: times[times.length - 1],
          adapters: { date: { zone: S.cityTz || 'UTC' } },
          time: {
            tooltipFormat: 'MMM d  HH:mm',
            displayFormats: { hour: 'MMM d HH:mm', day: 'MMM d' },
          },
          grid: { color: '#2d3148' },
          ticks: { color: '#6b7280', maxTicksLimit: 8, font: { family: 'Consolas,monospace', size: 10 } },
        },
        y: {
          grid: { color: '#2d3148' },
          ticks: {
            color: '#6b7280',
            font: { family: 'Consolas,monospace', size: 10 },
            callback: v => v + sym_,
          },
        },
      },
      plugins: {
        legend: {
          labels: { color: '#6b7280', font: { family: 'Consolas,monospace', size: 10 }, boxWidth: 16 },
        },
        tooltip: {
          backgroundColor: '#1a1d27',
          borderColor: '#2d3148', borderWidth: 1,
          titleColor: '#e8eaf0', bodyColor: '#e8eaf0',
          titleFont: { family: 'Consolas,monospace', size: 11 },
          bodyFont:  { family: 'Consolas,monospace', size: 11 },
          callbacks: {
            label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}${sym_}`,
          },
        },
        annotation: { annotations },
        zoom: {
          pan:  { enabled: true, mode: 'x' },
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: 'x' },
        },
      },
    },
  });

  renderHrStats(minVal, maxVal, minTime, maxTime, dewAtMin, rows.length, source);

  const srcTag = source === 'iem' ? 'METAR+SPECI obs' : 'METAR obs';
  hrSetStatus(
    `${rows.length} ${srcTag}  ·  ` +
    `True low ${minVal.toFixed(2)}${sym_} at ${fmtTime(minTime.toISOString())}  ·  ` +
    `True high ${maxVal.toFixed(2)}${sym_} at ${fmtTime(maxTime.toISOString())}`,
    'ok'
  );
}

function buildMinLabel(minVal, sym_, minTime, dewAtMin) {
  const lines = [`${minVal.toFixed(2)}${sym_}`, fmtTime(minTime.toISOString())];
  if (dewAtMin !== null && dewAtMin !== undefined) {
    const gap = minVal - dewAtMin;
    lines.push(`floor ${dewAtMin.toFixed(1)}${sym_} (gap ${gap.toFixed(1)}°)`);
  }
  return lines;
}

function renderHrStats(minVal, maxVal, minTime, maxTime, dewAtMin, count, source) {
  const sym_ = sym();
  const row  = $('hr-stats-row');
  row.innerHTML = '';

  function card(label, value, color, sub) {
    const el = document.createElement('div');
    el.className = 'hr-stat-card';
    el.innerHTML =
      `<div class="hr-stat-label">${label}</div>` +
      `<div class="hr-stat-value" style="color:${color}">${value}</div>` +
      (sub ? `<div class="hr-stat-sub">${sub}</div>` : '');
    row.appendChild(el);
  }

  card('Obs Low',  `${minVal.toFixed(2)}${sym_}`, '#3ecf8e', fmtTime(minTime.toISOString()));

  if (dewAtMin !== null && dewAtMin !== undefined) {
    const gap = minVal - dewAtMin;
    let floorSub;
    if (gap <= 0.5)      floorSub = 'dew pt = obs — true min likely at obs';
    else if (gap <= 1.5) floorSub = `true min likely ${dewAtMin.toFixed(1)}–${minVal.toFixed(1)}${sym_}`;
    else                 floorSub = `wide gap — true min could be ${gap.toFixed(1)}° lower`;
    card('Dew Pt Floor', `${dewAtMin.toFixed(1)}${sym_}`, '#a78bfa', floorSub);
  }

  card('Obs High', `${maxVal.toFixed(2)}${sym_}`, '#f5a623', fmtTime(maxTime.toISOString()));

  if (S.cli && !S.cli.error) {
    if (S.cli.low_temp != null) {
      const cl = S.units === 'C' ? ((S.cli.low_temp - 32) * 5/9) : S.cli.low_temp;
      const diff = minVal - cl;
      const match = Math.abs(diff) < 0.6 ? '✓ match' : `gap: ${diff >= 0 ? '+' : ''}${diff.toFixed(2)}${sym_}`;
      card('CLI Official Low', `${S.cli.low_temp}°F`, '#7ec8e3', match);
    }
    if (S.cli.high_temp != null) {
      const ch = S.units === 'C' ? ((S.cli.high_temp - 32) * 5/9) : S.cli.high_temp;
      const diff = maxVal - ch;
      const match = Math.abs(diff) < 0.6 ? '✓ match' : `gap: ${diff >= 0 ? '+' : ''}${diff.toFixed(2)}${sym_}`;
      card('CLI Official High', `${S.cli.high_temp}°F`, '#f5a623', match);
    }
  }
}

function autoPopulateHighRes(result, startStr, endStr) {
  const station = result && result.station;
  if (!station) return;
  $('hr-station').value = station;
  if (startStr) $('hr-start').value = startStr;
  if (endStr)   $('hr-end').value   = endStr;
  hrSetStatus(`Auto-fetching raw obs for ${station}…`);
  setTimeout(fetchHighRes, 400);
}

$('hr-fetch-btn').addEventListener('click', fetchHighRes);
$('hr-reset-zoom').addEventListener('click', () => { if (hrChart) hrChart.resetZoom(); });

// ── Init ──────────────────────────────────────────────────────────────────────
loadCities().then(() => applyPreset('today'));
