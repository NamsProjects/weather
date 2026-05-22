'use strict';

// ── Stats helpers ─────────────────────────────────────────────────────────────
function filterToCLIWindows(rows, windows) {
  if (!windows || !windows.length) return rows;
  return rows.filter(row => {
    const t = new Date(row.time).getTime();
    return windows.some(w => t >= new Date(w.start).getTime() && t <= new Date(w.end).getTime());
  });
}

function computeStatsJS(rows) {
  if (!rows || !rows.length) return null;
  const minRow = rows.reduce((a, b) => a.temp < b.temp ? a : b);
  const maxRow = rows.reduce((a, b) => a.temp > b.temp ? a : b);
  return { min_temp: minRow.temp, min_time: minRow.time, max_temp: maxRow.temp, max_time: maxRow.time };
}

// ── Cards ─────────────────────────────────────────────────────────────────────
function renderCards() {
  if (!S.obs) return;
  const r = S.obs;

  $('val-station').textContent = r.station || '—';
  $('sub-station').textContent = '';
  $('val-records').textContent = r.data ? r.data.length : '—';
  $('sub-records').textContent = r.interval || '';

  let stats;
  if (S.cliWindowOnly && r.cli_windows && r.cli_windows.length && r.data) {
    const filtered = filterToCLIWindows(r.data, r.cli_windows);
    stats = computeStatsJS(filtered);
    $('lbl-min').textContent = 'MIN  ·  CLI WIN';
    $('lbl-max').textContent = 'MAX  ·  CLI WIN';
  } else {
    stats = r;
    $('lbl-min').textContent = 'Min Temp';
    $('lbl-max').textContent = 'Max Temp';
  }

  if (stats && stats.min_temp != null) {
    $('val-min').textContent = stats.min_temp + sym();
    $('sub-min').textContent = fmtTime(stats.min_time);
    $('dur-min').textContent = r.min_duration_str ? 'held ' + r.min_duration_str : '';
    $('val-max').textContent = stats.max_temp + sym();
    $('sub-max').textContent = fmtTime(stats.max_time);
    $('dur-max').textContent = r.max_duration_str ? 'held ' + r.max_duration_str : '';
  } else {
    ['val-min','sub-min','dur-min','val-max','sub-max','dur-max']
      .forEach(id => $(id).textContent = id.startsWith('val') ? 'N/A' : '');
  }
}

// ── Forecast status strip ──────────────────────────────────────────────────────
function renderFcStrip() {
  const parts = [];

  if (S.forecast === 'loading') {
    parts.push(`<span class="fc-loading"><span class="fc-dot" style="background:#f97316"></span>NWS forecast loading…</span>`);
  } else if (S.forecast && S.forecast.rows && S.forecast.rows.length) {
    const ut = S.forecast.update_time
      ? ' · updated ' + new Date(S.forecast.update_time).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})
      : '';
    parts.push(`<span class="fc-ok"><span class="fc-dot" style="background:#f97316;display:inline-block"></span>NWS forecast${ut}</span>`);
  } else if (S.forecast === 'error') {
    parts.push(`<span class="fc-err"><span class="fc-dot" style="background:var(--danger)"></span>NWS forecast failed</span>`);
  }

  if (S.omFcast === 'loading') {
    parts.push(`<span class="fc-loading"><span class="fc-dot" style="background:#fff"></span>Open-Meteo loading…</span>`);
  } else if (S.omFcast && S.omFcast.rows && S.omFcast.rows.length) {
    const hdr = S.omFcast.header_text || 'Open-Meteo HRRR/GFS';
    const fallbackWarn = S.omFcast.coord_source === 'city_center' ? ' ⚠ city coords (no station)' : '';
    parts.push(`<span class="fc-ok"><span class="fc-dot" style="background:#e2e8f0;display:inline-block"></span>${hdr}${fallbackWarn}</span>`);
  } else if (S.omFcast === 'error') {
    parts.push(`<span class="fc-err"><span class="fc-dot" style="background:var(--danger)"></span>Open-Meteo failed</span>`);
  }

  if (S.omObs === 'loading') {
    parts.push(`<span class="fc-loading"><span class="fc-dot" style="background:#e2e8f0"></span>OM Observed loading…</span>`);
  } else if (S.omObs && S.omObs.rows && S.omObs.rows.length) {
    const fallbackWarn = S.omObs.coord_source === 'city_center' ? ' ⚠ city coords (no station)' : '';
    parts.push(`<span class="fc-ok"><span class="fc-dot" style="background:#e2e8f0;display:inline-block"></span>OM Observed (${S.omObs.rows.length} pts)${fallbackWarn}</span>`);
  } else if (S.omObs === 'error') {
    parts.push(`<span class="fc-err"><span class="fc-dot" style="background:var(--danger)"></span>OM Observed failed</span>`);
  }

  if (S.compare === 'loading') {
    parts.push(`<span class="fc-loading"><span class="fc-dot" style="background:var(--violet)"></span>Compare loading…</span>`);
  } else if (S.compare && S.compare.rows && S.compare.rows.length) {
    parts.push(`<span class="fc-ok"><span class="fc-dot" style="background:var(--violet);display:inline-block"></span>${S.compare.offset_days}d prior (${S.compare.rows.length} pts)</span>`);
  } else if (S.compare === 'error') {
    parts.push(`<span class="fc-err"><span class="fc-dot" style="background:var(--danger)"></span>Compare failed</span>`);
  }

  if (S.omCompare === 'loading') {
    parts.push(`<span class="fc-loading"><span class="fc-dot" style="background:#2dd4bf"></span>OM Prior loading…</span>`);
  } else if (S.omCompare && S.omCompare.rows && S.omCompare.rows.length) {
    parts.push(`<span class="fc-ok"><span class="fc-dot" style="background:#2dd4bf;display:inline-block"></span>OM ${S.omCompare.offset_days}d prior (${S.omCompare.rows.length} pts)</span>`);
  } else if (S.omCompare === 'error') {
    parts.push(`<span class="fc-err"><span class="fc-dot" style="background:var(--danger)"></span>OM Prior failed</span>`);
  }

  if (S.mos === 'loading') {
    parts.push(`<span class="fc-loading"><span class="fc-dot" style="background:#facc15"></span>MOS loading…</span>`);
  } else if (S.mos && S.mos.gfs) {
    const gfs = S.mos.gfs;
    const rt = gfs.runtime
      ? ' · run ' + new Date(gfs.runtime).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})
      : '';
    const lavRows = S.mos.lav && S.mos.lav.rows && S.mos.lav.rows.length;
    const lavTag = lavRows ? ' + LAMP' : '';
    parts.push(`<span class="fc-ok"><span class="fc-dot" style="background:#facc15;display:inline-block"></span>GFS-MOS${lavTag}${rt} · ${S.mos.station}</span>`);
  } else if (S.mos === 'error') {
    parts.push(`<span class="fc-err"><span class="fc-dot" style="background:var(--danger)"></span>MOS failed</span>`);
  }

  if (S.cityDst === false) {
    parts.push(`<span class="fc-nodst" title="No daylight saving time — CLI window is 00:00–23:59; forecast cap at midnight">⚠ No DST · CLI window 00:00–23:59 · cap midnight</span>`);
  }

  $('fc-strip').innerHTML = parts.join('');
}
