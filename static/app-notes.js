'use strict';

// ── Notes module state ────────────────────────────────────────────────────────
const NS = {
  mode:        'high',  // 'high' | 'low'
  snaps:       [],      // loaded from server, newest first
  selectedId:  null,
  saveDebounce: null,
  // Track Chart.js instances created in the detail pane so we can destroy
  // them before re-rendering (otherwise canvases leak and break legend state).
  chartInstances: [],  // [{key, chart}]
};

// ── Server I/O ────────────────────────────────────────────────────────────────
async function ntLoad() {
  try {
    const res = await fetch('/api/notes');
    const data = await res.json();
    NS.snaps = Array.isArray(data.notes) ? data.notes : [];
  } catch { NS.snaps = []; }
}

async function ntSave(snap) {
  try {
    await fetch('/api/notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(snap),
    });
  } catch {}
}

async function ntDelete(id) {
  try {
    await fetch(`/api/notes/${encodeURIComponent(id)}`, { method: 'DELETE' });
  } catch {}
}

async function ntPatchText(id, text) {
  const res = await fetch(`/api/notes/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note_text: text }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

async function ntPatchAppendChart(id, imgDataUrl, rows) {
  try {
    await fetch(`/api/notes/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        append_chart_img: imgDataUrl,
        taken_at: new Date().toLocaleString(),
        rows: rows || null,
        units: S.units,
        city_tz: S.cityTz,
        city: S.city,
      }),
    });
  } catch {}
}

// Bundle current live S.* rows for an interactive replay later. Mirrors the
// fields captured by ntDoSnap so the interactive renderer can read either an
// appended entry's `rows` or the snap-level fields uniformly.
function _currentRowsBundle() {
  return {
    obs_rows:          S.obs?.data                   || [],
    obs_stats:         S.obs ? {
                         min_temp: S.obs.min_temp, min_time: S.obs.min_time,
                         max_temp: S.obs.max_temp, max_time: S.obs.max_time,
                       } : null,
    obs_query_start:   S.obs?.query_start || null,
    obs_cli_windows:   S.obs?.cli_windows || [],
    forecast_rows:     S.forecast?.rows              || [],
    om_rows:           S.omFcast?.rows               || [],
    om_obs_rows:       S.omObs?.rows                 || [],
    compare_rows:      S.compare?.rows               || [],
    compare_offset:    S.compare?.offset_days || null,
    om_compare_rows:   S.omCompare?.rows             || [],
    om_compare_offset: S.omCompare?.offset_days || null,
    mos_gfs_rows:      S.mos?.gfs?.rows              || [],
    mos_lav_rows:      S.mos?.lav?.rows              || [],
    wethr_rows:        (S.wethrObs && S.wethrObs !== 'error') ? (S.wethrObs.rows || []) : [],
    wethr_stats:       (S.wethrObs && S.wethrObs !== 'error' && S.wethrObs.rows?.length)
                         ? computeStatsJS(S.wethrObs.rows)
                         : null,
    nws_versions:      S.nwsVersions?.versions_by_date || {},
    nws_ver_selected:  { ...(S.nwsVerSelected || {}) },
    cli_window_only:   !!S.cliWindowOnly,
    forecast_enabled:  !!S.forecastEnabled,
    start:             $('start-input').value || '',
    end:               $('end-input').value   || '',
  };
}

async function ntRemoveChartImg(snap, key) {
  // key: 'original' | numeric index into snap.chart_imgs
  if (key === 'original') {
    snap.chart_img = null;
  } else {
    snap.chart_imgs.splice(key, 1);
  }
  try {
    await fetch(`/api/notes/${encodeURIComponent(snap.id)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remove_chart_img: key }),
    });
  } catch {}
  ntRenderDetail(snap);
}

// ── Mode toggle ───────────────────────────────────────────────────────────────
function ntSetMode(mode) {
  NS.mode = mode;
  $('nt-type-high').classList.toggle('active', mode === 'high');
  $('nt-type-low').classList.toggle('active',  mode === 'low');
}

function ntUpdateCityDisplay() {
  const el = $('nt-city-display');
  if (!el) return;
  const snap = NS.snaps.find(s => s.id === NS.selectedId);
  el.textContent = snap ? (snap.city || '—') : (S.city || '—');
}

// ── Forecast value extraction ─────────────────────────────────────────────────
function ntForecastVals() {
  let nwsHigh = null, nwsLow = null, omHigh = null, omLow = null;

  if (S.forecast && S.forecast.rows && S.forecast.rows.length) {
    const t = S.forecast.rows.map(r => r.temp).filter(v => v != null);
    if (t.length) { nwsHigh = Math.max(...t); nwsLow = Math.min(...t); }
  }
  if (S.omFcast && S.omFcast.rows && S.omFcast.rows.length) {
    const t = S.omFcast.rows.map(r => r.temp).filter(v => v != null);
    if (t.length) { omHigh = Math.max(...t); omLow = Math.min(...t); }
  }
  if (nwsHigh == null && S.obs) { nwsHigh = S.obs.max_temp; nwsLow = S.obs.min_temp; }

  return { nwsHigh, nwsLow, omHigh, omLow };
}

function ntDateLabel() {
  try {
    const v = $('start-input').value;
    if (!v) return '';
    return new Date(v).toLocaleDateString([], { month: 'short', day: 'numeric' });
  } catch { return ''; }
}

// ── Refresh data (forecasts + Kalshi) ─────────────────────────────────────────
async function ntRefreshData() {
  const start = $('start-input').value;
  const end   = $('end-input').value;
  if (!start || !end || !S.city) return;

  const parallel = [
    fetch('/api/forecast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ city: S.city, start, end, units: S.units, browser_tz: S.cityTz }),
    }).then(r => r.json()).then(d => {
      if (!d.error) { S.forecast = d; renderFcStrip(); renderChart(); }
    }).catch(() => {}),

    fetch('/api/forecast/openmeteo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ city: S.city, start, end, units: S.units, browser_tz: S.cityTz }),
    }).then(r => r.json()).then(d => {
      if (!d.error) { S.omFcast = d; renderFcStrip(); renderChart(); }
    }).catch(() => {}),
  ];

  await Promise.allSettled(parallel);
  // Let chart repaint before we snapshot the canvas
  await new Promise(r => setTimeout(r, 600));
  await fetchKalshi();
}

// ── Take a snap ───────────────────────────────────────────────────────────────
async function ntDoSnap(withRefresh) {
  const refreshBtn = $('nt-refresh-snap');
  const snapBtn    = $('nt-snap-btn');

  if (withRefresh) {
    refreshBtn.disabled = true;
    refreshBtn.textContent = '⟳ Refreshing…';
  } else {
    snapBtn.disabled = true;
    snapBtn.textContent = '⟳ Snapping…';
  }

  try {
    if (withRefresh) await ntRefreshData();

    const vals   = ntForecastVals();
    const mode   = NS.mode;
    const isHigh = mode === 'high';

    const fval   = isHigh ? (vals.nwsHigh ?? vals.omHigh) : (vals.nwsLow ?? vals.omLow);
    const nwsVal = isHigh ? vals.nwsHigh : vals.nwsLow;
    const omVal  = isHigh ? vals.omHigh  : vals.omLow;

    // Grab Kalshi markets — prefer already-loaded S.kalshi, else re-fetch
    let kalshiData = S.kalshi;
    if (!kalshiData || kalshiData.city !== S.city) {
      try {
        const r = await fetch('/api/kalshi', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            city: S.city,
            forecast_high: vals.nwsHigh ?? vals.omHigh,
            forecast_low:  vals.nwsLow  ?? vals.omLow,
          }),
        });
        kalshiData = await r.json();
        S.kalshi = kalshiData;
        renderKalshi(kalshiData);
      } catch { kalshiData = { high_markets: [], low_markets: [] }; }
    }

    const markets = isHigh
      ? (kalshiData.high_markets || [])
      : (kalshiData.low_markets  || []);

    // Capture chart canvas
    let chartImg = null;
    try {
      const canvas = $('temp-chart');
      if (canvas) chartImg = canvas.toDataURL('image/png');
    } catch {}

    const snap = {
      id:             new Date().toISOString(),
      city:           S.city || '—',
      start:          $('start-input').value || '',
      end:            $('end-input').value   || '',
      mode,
      units:          S.units || 'F',
      city_tz:        S.cityTz || 'UTC',
      forecast_val:   fval    != null ? Math.round(fval)   : null,
      nws_val:        nwsVal  != null ? Math.round(nwsVal) : null,
      om_val:         omVal   != null ? Math.round(omVal)  : null,
      station:        S.obs ? (S.obs.station || null) : null,
      interval:       S.interval || 'hourly',
      date_label:     ntDateLabel(),
      chart_img:      chartImg,
      kalshi_markets: markets,
      note_text:      '',
      saved_at:       new Date().toISOString(),
      // ── Raw time-series data ────────────────────────────────────────────
      obs_rows:         (S.obs?.data)                  || [],
      obs_stats:        S.obs ? {
                          min_temp: S.obs.min_temp, min_time: S.obs.min_time,
                          max_temp: S.obs.max_temp, max_time: S.obs.max_time,
                        } : null,
      obs_query_start:  S.obs?.query_start || null,
      obs_cli_windows:  S.obs?.cli_windows || [],
      forecast_rows:    (S.forecast?.rows)             || [],
      om_rows:          (S.omFcast?.rows)              || [],
      om_obs_rows:      (S.omObs?.rows)                || [],
      compare_rows:     (S.compare?.rows)              || [],
      compare_offset:   S.compare?.offset_days || null,
      om_compare_rows:  (S.omCompare?.rows)            || [],
      om_compare_offset: S.omCompare?.offset_days || null,
      mos_gfs_rows:     (S.mos?.gfs?.rows)             || [],
      mos_lav_rows:     (S.mos?.lav?.rows)             || [],
      wethr_rows:       (S.wethrObs && S.wethrObs !== 'error') ? (S.wethrObs.rows || []) : [],
      wethr_stats:      (S.wethrObs && S.wethrObs !== 'error' && S.wethrObs.rows?.length)
                          ? computeStatsJS(S.wethrObs.rows)
                          : null,
      nws_versions:     S.nwsVersions?.versions_by_date || {},
      nws_ver_selected: { ...(S.nwsVerSelected || {}) },
      cli_window_only:  !!S.cliWindowOnly,
      forecast_enabled: !!S.forecastEnabled,
    };

    NS.snaps.unshift(snap);
    await ntSave(snap);
    ntRenderList();
    ntSelectSnap(snap.id);
    ntUpdateFooter();

  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = '□ Refresh + Snap';
    snapBtn.disabled = false;
    snapBtn.textContent = '□ Snap';
  }
}

// ── Render snapshot list (left panel) ─────────────────────────────────────────
function ntRenderList() {
  const list = $('nt-snap-list');
  list.innerHTML = '<div class="nt-snap-list-hdr">SNAPSHOTS</div>';

  if (!NS.snaps.length) {
    const emp = document.createElement('div');
    emp.className = 'nt-snap-empty';
    emp.textContent = 'No snapshots yet';
    list.appendChild(emp);
    return;
  }

  NS.snaps.forEach(snap => {
    const card = document.createElement('div');
    card.className = 'nt-snap-card' + (snap.id === NS.selectedId ? ' selected' : '');
    card.dataset.id = snap.id;

    const isHigh    = snap.mode === 'high';
    const timeStr   = ntFmtTime(snap.saved_at);
    const fStr      = snap.forecast_val != null ? `${snap.forecast_val}°F forecast` : 'no forecast';
    const badgeCls  = isHigh ? 'nt-badge-high' : 'nt-badge-low';
    const badgeTxt  = isHigh ? 'HIGH' : 'LOW';
    const thumbHtml = snap.chart_img
      ? `<img src="${snap.chart_img}" class="nt-thumb-img" alt="chart" />`
      : '<div class="nt-thumb-empty">no preview</div>';

    card.innerHTML = `
      <div class="nt-card-top">
        <div class="nt-card-info">
          <div class="nt-card-city">${snap.city}</div>
          <div class="nt-card-time">${timeStr}</div>
          <div class="nt-card-fcast">${fStr}</div>
        </div>
        <span class="nt-badge ${badgeCls}">${badgeTxt}</span>
      </div>
      <div class="nt-thumb">${thumbHtml}</div>
    `;
    card.addEventListener('click', () => ntSelectSnap(snap.id));
    list.appendChild(card);
  });
}

// ── Select and render detail (right panel) ────────────────────────────────────
function ntSelectSnap(id) {
  NS.selectedId = id;
  document.querySelectorAll('.nt-snap-card')
    .forEach(c => c.classList.toggle('selected', c.dataset.id === id));

  const snap = NS.snaps.find(s => s.id === id);
  if (snap) { ntRenderDetail(snap); ntUpdateCityDisplay(); }
}

// Destroy any Chart.js instances currently attached to the detail pane.
// Called before innerHTML wipes (which would orphan them).
function _destroySnapCharts() {
  NS.chartInstances.forEach(({ chart }) => { try { chart.destroy(); } catch {} });
  NS.chartInstances = [];
}

// Return the row bundle used to render an entry interactively.
// 'original' → snap-level rows; numeric idx → appended entry's `rows`.
// Returns null if the entry has no rows (legacy appended entries without rows).
function _rowBundleForEntry(snap, key) {
  if (key === 'original') {
    return {
      obs_rows:          snap.obs_rows          || [],
      obs_stats:         snap.obs_stats         || null,
      obs_query_start:   snap.obs_query_start   || null,
      obs_cli_windows:   snap.obs_cli_windows   || [],
      forecast_rows:     snap.forecast_rows     || [],
      om_rows:           snap.om_rows           || [],
      om_obs_rows:       snap.om_obs_rows       || [],
      compare_rows:      snap.compare_rows      || [],
      compare_offset:    snap.compare_offset    || null,
      om_compare_rows:   snap.om_compare_rows   || [],
      om_compare_offset: snap.om_compare_offset || null,
      mos_gfs_rows:      snap.mos_gfs_rows      || [],
      mos_lav_rows:      snap.mos_lav_rows      || [],
      nws_versions:      snap.nws_versions      || {},
      nws_ver_selected:  snap.nws_ver_selected  || null,
      cli_window_only:   snap.cli_window_only   != null ? snap.cli_window_only : true,
      forecast_enabled:  snap.forecast_enabled  != null ? snap.forecast_enabled : true,
      start:             snap.start || '',
      end:               snap.end   || '',
      units:             snap.units   || 'F',
      city_tz:           snap.city_tz || S.cityTz || 'UTC',
      city:              snap.city,
    };
  }
  const entry = snap.chart_imgs?.[key];
  if (!entry || !entry.rows) return null;
  return {
    ...entry.rows,
    units:   entry.units   || snap.units   || 'F',
    city_tz: entry.city_tz || snap.city_tz || S.cityTz || 'UTC',
    city:    snap.city,
  };
}

// Returns true if this entry has enough row data to render interactively.
// renderChart() requires obs, so observations must be present.
function _entryHasInteractive(snap, key) {
  const bundle = _rowBundleForEntry(snap, key);
  if (!bundle) return false;
  return !!(bundle.obs_rows?.length);
}

// Render a snap interactively by delegating to the same renderChart() the
// live page uses. We reshape the bundle into the obs/forecast/etc. objects
// renderChart expects, and pass `isSnap: true` so it doesn't clobber globals
// or the live chart's DOM titles.
function _renderSnapChart(canvas, bundle) {
  const obs = bundle.obs_rows?.length || bundle.obs_cli_windows?.length ? {
    data:         bundle.obs_rows  || [],
    cli_windows:  bundle.obs_cli_windows || [],
    query_start:  bundle.obs_query_start || null,
    min_temp:     bundle.obs_stats?.min_temp,
    min_time:     bundle.obs_stats?.min_time,
    max_temp:     bundle.obs_stats?.max_temp,
    max_time:     bundle.obs_stats?.max_time,
  } : null;

  return renderChart({
    isSnap:          true,
    canvas,
    obs,
    forecast:        bundle.forecast_rows?.length ? { rows: bundle.forecast_rows } : null,
    omFcast:         bundle.om_rows?.length       ? { rows: bundle.om_rows }       : null,
    omObs:           bundle.om_obs_rows?.length   ? { rows: bundle.om_obs_rows }   : null,
    compare:         bundle.compare_rows?.length
                       ? { rows: bundle.compare_rows, offset_days: bundle.compare_offset }
                       : null,
    omCompare:       bundle.om_compare_rows?.length
                       ? { rows: bundle.om_compare_rows, offset_days: bundle.om_compare_offset }
                       : null,
    mos: (bundle.mos_gfs_rows?.length || bundle.mos_lav_rows?.length) ? {
      gfs: { rows: bundle.mos_gfs_rows || [] },
      lav: { rows: bundle.mos_lav_rows || [] },
    } : null,
    nwsVersions:     bundle.nws_versions && Object.keys(bundle.nws_versions).length
                       ? { versions_by_date: bundle.nws_versions }
                       : null,
    nwsVerSelected:  bundle.nws_ver_selected || _allNwsVersionsSelected(bundle.nws_versions),
    units:           bundle.units,
    cityTz:          bundle.city_tz,
    forecastEnabled: bundle.forecast_enabled !== false,
    cliWindowOnly:   bundle.cli_window_only !== false,
    cityTitle:       bundle.city,
    startInputVal:   bundle.start,
    endInputVal:     bundle.end,
  });
}

// Legacy snaps don't have nws_ver_selected — fall back to "all selected" so
// the lines render rather than disappearing.
function _allNwsVersionsSelected(versionsByDate) {
  const selected = {};
  if (!versionsByDate) return selected;
  for (const [dateStr, versions] of Object.entries(versionsByDate)) {
    versions.forEach((_, idx) => { selected[`${dateStr}|${idx}`] = true; });
  }
  return selected;
}

function ntRenderDetail(snap) {
  _destroySnapCharts();
  const detail = $('nt-detail');
  detail.innerHTML = '';

  const isHigh     = snap.mode === 'high';
  const modeColor  = isHigh ? 'var(--warning)' : 'var(--accent2)';
  const modeLbl    = isHigh ? 'HIGH' : 'LOW';
  const fval       = snap.forecast_val != null ? `${snap.forecast_val}°F` : '—';
  const nwsStr     = snap.nws_val  != null ? `${snap.nws_val}°F`  : '—';
  const omStr      = snap.om_val   != null ? `${snap.om_val}°F`   : '—';
  const station    = snap.station  || '—';
  const interval   = snap.interval || '—';
  const dateLabel  = snap.date_label || '';

  // ── Header strip ────────────────────────────────────────────────────────
  const strip = document.createElement('div');
  strip.className = 'nt-strip';
  strip.innerHTML = `
    <div class="nt-strip-left">
      <span class="nt-strip-label">CHART + KALSHI SNAPSHOT</span>
      <span class="nt-strip-mode" style="color:${modeColor}">${modeLbl} ${fval}</span>
      <span class="nt-strip-item">NWS: <b>${nwsStr}</b></span>
      <span class="nt-strip-item">OM: <b>${omStr}</b></span>
      <span class="nt-strip-meta">${station} · ${interval} · ${dateLabel}</span>
    </div>
    <button class="nt-del-btn" data-id="${snap.id}" title="Delete snapshot">✕</button>
  `;
  strip.querySelector('.nt-del-btn').addEventListener('click', e => {
    e.stopPropagation();
    ntDeleteSnap(snap.id);
  });

  // Add Chart button in the strip
  const addChartBtn = document.createElement('button');
  addChartBtn.className = 'nt-add-chart-btn';
  addChartBtn.textContent = '📷 Add Chart';
  addChartBtn.title = 'Append current chart to this snapshot';
  addChartBtn.addEventListener('click', async () => {
    addChartBtn.disabled = true;
    addChartBtn.textContent = '⟳ Saving…';
    try {
      const canvas = $('temp-chart');
      if (!canvas) { addChartBtn.textContent = 'No chart'; setTimeout(() => { addChartBtn.disabled = false; addChartBtn.textContent = '📷 Add Chart'; }, 1500); return; }
      const imgDataUrl = canvas.toDataURL('image/png');
      const rows  = _currentRowsBundle();
      const entry = {
        img: imgDataUrl,
        taken_at: new Date().toLocaleString(),
        rows,
        units:   S.units,
        city_tz: S.cityTz,
      };
      if (!snap.chart_imgs) snap.chart_imgs = [];
      snap.chart_imgs.push(entry);
      await ntPatchAppendChart(snap.id, imgDataUrl, rows);
      ntRenderDetail(snap);
    } finally {
      addChartBtn.disabled = false;
      addChartBtn.textContent = '📷 Add Chart';
    }
  });
  strip.querySelector('.nt-strip-left').appendChild(addChartBtn);

  detail.appendChild(strip);

  // ── Chart images (original + any appended) ───────────────────────────────
  const allImgs = [];
  if (snap.chart_img) allImgs.push({ img: snap.chart_img, key: 'original', label: '#1 original', city: snap.city });
  (snap.chart_imgs || []).forEach((e, i) => {
    const num = allImgs.length + 1;
    allImgs.push({ img: e.img, key: i, label: `#${num}  ${e.taken_at || ''}`, city: e.city || snap.city });
  });

  const chartArea = document.createElement('div');
  chartArea.className = 'nt-chart-area';
  function makeChartItem(entry, removeKey, label, fullWidth) {
    const wrap = document.createElement('div');
    wrap.className = fullWidth ? 'nt-chart-single-wrap' : 'nt-chart-multi-item';

    const hdr = document.createElement('div');
    hdr.className = 'nt-chart-multi-label';

    const hdrText = document.createElement('span');
    hdrText.textContent = label;
    hdr.appendChild(hdrText);

    if (entry.city) {
      const cityBadge = document.createElement('span');
      cityBadge.className = 'nt-chart-city-badge';
      cityBadge.textContent = entry.city;
      hdr.appendChild(cityBadge);
    }

    const canInteract = _entryHasInteractive(snap, removeKey);

    const modeBtn = document.createElement('button');
    modeBtn.className = 'nt-chart-mode-btn';
    modeBtn.textContent = '▶ Interactive';
    modeBtn.title = 'Switch to interactive chart with togglable series';
    if (!canInteract) {
      modeBtn.disabled = true;
      modeBtn.title = 'No row data saved for this chart — interactive mode unavailable';
    }
    hdr.appendChild(modeBtn);

    const rmBtn = document.createElement('button');
    rmBtn.className = 'nt-chart-rm-btn';
    rmBtn.textContent = '× Remove';
    rmBtn.addEventListener('click', () => ntRemoveChartImg(snap, removeKey));
    hdr.appendChild(rmBtn);

    // Body container — toggles between <img> (PNG) and <canvas> (interactive).
    const body = document.createElement('div');
    body.className = 'nt-chart-body';

    const img = document.createElement('img');
    img.src = entry.img;
    img.className = fullWidth ? 'nt-chart-img' : 'nt-chart-img-multi';
    img.alt = label;
    body.appendChild(img);

    // State: 'image' or 'interactive'
    let mode = 'image';
    let chartInstance = null;

    modeBtn.addEventListener('click', () => {
      if (!canInteract) return;
      if (mode === 'image') {
        const bundle = _rowBundleForEntry(snap, removeKey);
        if (!bundle) return;
        // Capture the image's currently rendered box so the interactive canvas
        // takes the exact same physical space — avoids any layout shift on toggle.
        const imgRect = img.getBoundingClientRect();
        const lockedW = Math.round(imgRect.width);
        const lockedH = Math.round(imgRect.height);
        body.innerHTML = '';
        const canvasWrap = document.createElement('div');
        canvasWrap.className = fullWidth
          ? 'nt-chart-canvas-wrap'
          : 'nt-chart-canvas-wrap nt-chart-canvas-wrap-multi';
        if (lockedW > 0 && lockedH > 0) {
          canvasWrap.style.width  = lockedW + 'px';
          canvasWrap.style.height = lockedH + 'px';
        }
        const canvas = document.createElement('canvas');
        canvasWrap.appendChild(canvas);
        body.appendChild(canvasWrap);
        try {
          chartInstance = _renderSnapChart(canvas, bundle);
          NS.chartInstances.push({ key: removeKey, chart: chartInstance });
          mode = 'interactive';
          modeBtn.textContent = '📷 Snapshot';
          modeBtn.title = 'Switch back to the saved PNG snapshot';
        } catch (e) {
          console.error('Failed to render interactive snap chart:', e);
          body.innerHTML = '';
          body.appendChild(img);
        }
      } else {
        if (chartInstance) {
          try { chartInstance.destroy(); } catch {}
          NS.chartInstances = NS.chartInstances.filter(c => c.chart !== chartInstance);
          chartInstance = null;
        }
        body.innerHTML = '';
        body.appendChild(img);
        mode = 'image';
        modeBtn.textContent = '▶ Interactive';
        modeBtn.title = 'Switch to interactive chart with togglable series';
      }
    });

    wrap.appendChild(hdr);
    wrap.appendChild(body);
    return wrap;
  }

  if (allImgs.length === 0) {
    chartArea.innerHTML = `<div class="nt-chart-empty"><div>No snapshot yet</div></div>`;
  } else if (allImgs.length === 1) {
    chartArea.appendChild(makeChartItem(allImgs[0], allImgs[0].key, allImgs[0].label, true));
  } else {
    chartArea.className = 'nt-chart-area nt-chart-multi';
    allImgs.forEach(entry => chartArea.appendChild(makeChartItem(entry, entry.key, entry.label, false)));
  }
  detail.appendChild(chartArea);

  // ── Kalshi contracts table ───────────────────────────────────────────────
  const markets = snap.kalshi_markets || [];
  if (markets.length) {
    const sec = document.createElement('div');
    sec.className = 'nt-kalshi-sec';

    const hdr = document.createElement('div');
    hdr.className = 'nt-kalshi-hdr';
    hdr.textContent = `KALSHI ${modeLbl} CONTRACTS — ${snap.city.toUpperCase()}`;
    sec.appendChild(hdr);

    // Sort by % chance descending to match the live panel default
    const sorted = markets.slice().sort((a, b) => {
      const ap = parseFloat(a.last_price_dollars) || 0;
      const bp = parseFloat(b.last_price_dollars) || 0;
      return bp - ap;
    });

    const scrollWrap = document.createElement('div');
    scrollWrap.className = 'kalshi-table-scroll';

    const table = document.createElement('div');
    table.className = 'kalshi-table';

    const hdrRow = document.createElement('div');
    hdrRow.className = 'kalshi-row header';
    ['Range', '% Chance', 'Yes ¢', 'No ¢', 'Volume', 'OI', 'Closes'].forEach(h => {
      const c = document.createElement('div');
      c.className = 'kalshi-cell header';
      c.textContent = h;
      hdrRow.appendChild(c);
    });
    table.appendChild(hdrRow);

    // Star the contract matching the day's most extreme value so far.
    // Prefer wethr observations (live METAR/SPECI) over NWS obs, and the
    // latest NWS forecast version (purple line) over the static forecast_val.
    // Whichever side is more extreme wins.
    const wethrExtreme = isHigh ? snap.wethr_stats?.max_temp : snap.wethr_stats?.min_temp;
    const obsExtreme   = wethrExtreme != null
                           ? wethrExtreme
                           : (isHigh ? snap.obs_stats?.max_temp : snap.obs_stats?.min_temp);

    let latestFcExtreme = null;
    const vbd = snap.nws_versions || {};
    const dateKeys = Object.keys(vbd).sort();
    if (dateKeys.length) {
      const versions = vbd[dateKeys[dateKeys.length - 1]] || [];
      const latest = versions[versions.length - 1];
      if (latest && latest.temps?.length) {
        const temps = latest.temps.filter(v => v != null);
        if (temps.length) latestFcExtreme = isHigh ? Math.max(...temps) : Math.min(...temps);
      }
    }
    const fcExtreme = latestFcExtreme != null ? latestFcExtreme : snap.forecast_val;

    let effectiveVal = null;
    if (obsExtreme != null && fcExtreme != null) {
      effectiveVal = isHigh ? Math.max(obsExtreme, fcExtreme) : Math.min(obsExtreme, fcExtreme);
    } else {
      effectiveVal = obsExtreme != null ? obsExtreme : fcExtreme;
    }

    sorted.forEach(m => {
      const isHit  = effectiveVal != null && ntContractHits(m, effectiveVal);
      const pct_str = m.last_price_dollars
        ? Math.round(parseFloat(m.last_price_dollars) * 100) + '%'
        : '—';
      const row = document.createElement('div');
      row.className = 'kalshi-row' + (isHit ? ' hit' : '');
      [
        { text: (m.yes_sub_title || '—') + (isHit ? ' ★' : ''), cls: 'label' + (isHit ? ' hit' : '') },
        { text: pct_str,                                  cls: '' },
        { text: _dollarsTocents(m.yes_bid_dollars),       cls: 'yes' },
        { text: _dollarsTocents(m.no_bid_dollars),        cls: 'no' },
        { text: _fmt_volume(m.volume_fp),                 cls: 'vol' },
        { text: _fmt_volume(m.open_interest_fp),          cls: 'vol' },
        { text: _fmt_close_time(m.close_time),            cls: '' },
      ].forEach(col => {
        const c = document.createElement('div');
        c.className = 'kalshi-cell ' + col.cls;
        c.textContent = col.text;
        row.appendChild(c);
      });
      table.appendChild(row);
    });

    scrollWrap.appendChild(table);
    sec.appendChild(scrollWrap);
    detail.appendChild(sec);
  }

  // ── Notes text area ───────────────────────────────────────────────────────
  const noteSec = document.createElement('div');
  noteSec.className = 'nt-note-sec';

  const noteHdr = document.createElement('div');
  noteHdr.className = 'nt-note-hdr';
  noteHdr.innerHTML = `
    <span class="nt-note-lbl">NOTES</span>
    <span class="nt-note-saved" id="nt-note-saved">&#9679; auto-saved</span>
  `;
  noteSec.appendChild(noteHdr);

  const ta = document.createElement('textarea');
  ta.className = 'nt-note-ta';
  ta.value = snap.note_text || '';
  ta.placeholder = 'Free-form notes…';
  ta.addEventListener('input', () => {
    snap.note_text = ta.value;
    const ind = $('nt-note-saved');
    if (ind) { ind.style.color = 'var(--muted)'; ind.textContent = '● saving…'; }
    clearTimeout(NS.saveDebounce);
    NS.saveDebounce = setTimeout(async () => {
      try {
        await ntPatchText(snap.id, ta.value);
        const ind2 = $('nt-note-saved');
        if (ind2) { ind2.style.color = 'var(--success)'; ind2.textContent = '● auto-saved'; }
      } catch {
        const ind2 = $('nt-note-saved');
        if (ind2) { ind2.style.color = '#e55'; ind2.textContent = '● save failed'; }
      }
    }, 800);
  });
  noteSec.appendChild(ta);
  detail.appendChild(noteSec);
}

// ── Contract highlight logic ──────────────────────────────────────────────────
function ntContractHits(m, fval) {
  const s = (m.yes_sub_title || '').replace(/°/g, '').toLowerCase();
  const below = s.match(/^([\d.]+)\s*or below|below\s*([\d.]+)/);
  if (below) {
    const v = parseFloat(below[1] ?? below[2]);
    return !isNaN(v) && fval <= v;
  }
  const above = s.match(/^([\d.]+)\s*or above|above\s*([\d.]+)/);
  if (above) {
    const v = parseFloat(above[1] ?? above[2]);
    return !isNaN(v) && fval >= v;
  }
  const range = s.match(/([\d.]+)\s*to\s*([\d.]+)/);
  if (range) {
    const lo = parseFloat(range[1]);
    const hi = parseFloat(range[2]);
    return !isNaN(lo) && !isNaN(hi) && fval >= lo && fval <= hi;
  }
  return false;
}

// ── Delete snap ───────────────────────────────────────────────────────────────
async function ntDeleteSnap(id) {
  NS.snaps = NS.snaps.filter(s => s.id !== id);
  await ntDelete(id);
  if (NS.selectedId === id) NS.selectedId = NS.snaps.length ? NS.snaps[0].id : null;
  ntRenderList();
  if (NS.selectedId) {
    ntSelectSnap(NS.selectedId);
  } else {
    _destroySnapCharts();
    $('nt-detail').innerHTML = `<div class="nt-detail-empty"><div>No snapshot yet</div></div>`;
  }
  ntUpdateFooter();
}

// ── Export PNG ────────────────────────────────────────────────────────────────
function ntExport() {
  const snap = NS.snaps.find(s => s.id === NS.selectedId);
  if (!snap || !snap.chart_img) return;
  const a = document.createElement('a');
  a.href     = snap.chart_img;
  a.download = `${snap.city.replace(/\s+/g, '_')}_${snap.mode}_${snap.date_label || snap.id.slice(0,10)}.png`;
  a.click();
}

// ── Copy note text ────────────────────────────────────────────────────────────
async function ntCopy() {
  const snap = NS.snaps.find(s => s.id === NS.selectedId);
  if (!snap) return;
  let txt = `${snap.city} ${snap.mode.toUpperCase()} – ${snap.date_label}\n`;
  if (snap.nws_val != null) txt += `NWS: ${snap.nws_val}°F  OM: ${snap.om_val ?? '—'}°F\n`;
  if (snap.note_text) txt += `\n${snap.note_text}`;
  try {
    await navigator.clipboard.writeText(txt);
    const btn = $('nt-copy-btn');
    if (btn) { btn.textContent = '✓ Copied'; setTimeout(() => { btn.textContent = '□ Copy note'; }, 1500); }
  } catch {}
}

// ── Footer ────────────────────────────────────────────────────────────────────
function ntUpdateFooter() {
  const el = $('nt-snap-count');
  if (el) el.textContent = `${NS.snaps.length} snap${NS.snaps.length !== 1 ? 's' : ''}`;
}

// ── Time formatter ────────────────────────────────────────────────────────────
function ntFmtTime(iso) {
  try {
    const d   = new Date(iso);
    const now = new Date();
    const yest = new Date(now); yest.setDate(now.getDate() - 1);
    const t = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    if (d.toDateString() === now.toDateString())  return `Today ${t}`;
    if (d.toDateString() === yest.toDateString()) return `Yesterday ${t}`;
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + t;
  } catch { return iso; }
}

// ── Wire up tab switch ────────────────────────────────────────────────────────
(function () {
  document.querySelectorAll('.tab-btn[data-tab="notes"]').forEach(btn => {
    btn.addEventListener('click', () => {
      ntUpdateCityDisplay();
    });
  });
})();

// ── Event listeners ───────────────────────────────────────────────────────────
$('nt-type-high').addEventListener('click',    () => ntSetMode('high'));
$('nt-type-low').addEventListener('click',     () => ntSetMode('low'));
$('nt-refresh-snap').addEventListener('click', () => ntDoSnap(true));
$('nt-snap-btn').addEventListener('click',     () => ntDoSnap(false));
$('nt-export-btn').addEventListener('click',   ntExport);
$('nt-copy-btn').addEventListener('click',     ntCopy);

// ── Init ──────────────────────────────────────────────────────────────────────
(async function ntInit() {
  await ntLoad();
  ntRenderList();
  ntUpdateCityDisplay();
  ntUpdateFooter();
  if (NS.snaps.length) ntSelectSnap(NS.snaps[0].id);
})();
