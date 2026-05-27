'use strict';

let chart = null;

// Wipe the chart canvas, title, and placeholder back to first-load state.
// Used when the city changes so stale data doesn't linger if the next fetch fails.
function clearChartView() {
  if (chart) { try { chart.destroy(); } catch {} chart = null; }
  const title = $('chart-city-title'); if (title) title.textContent = '';
  const placeholder = $('chart-placeholder'); if (placeholder) placeholder.style.display = '';
}

// Linear interpolation of forecast rows at targetMs. Returns null if out of range.
function interpolateForecast(rows, targetMs) {
  for (let i = 0; i < rows.length - 1; i++) {
    const aMs = new Date(rows[i].time).getTime();
    const bMs = new Date(rows[i + 1].time).getTime();
    if (aMs <= targetMs && bMs >= targetMs) {
      const t = bMs === aMs ? 0 : (targetMs - aMs) / (bMs - aMs);
      return rows[i].temp + t * (rows[i + 1].temp - rows[i].temp);
    }
  }
  return null;
}

// Pick a y-offset (pixels) for a label at (timeMs, temp).
// Direction is determined by comparing temp against ALL markers near that time
// (allMarkers = pre-collected list of every {ms, temp} that will be labelled):
//   highest temp at that x → label above (+ve y), lowest → below (-ve y).
// Spacing is then resolved against already-placed anchors to prevent overlap.
// Sign convention: positive yAdjust = above the data point, negative = below.
function pickYAdjust(timeMs, temp, placed, allMarkers, threshH = 2.5, yPixThresh = 16) {
  const threshMs = threshH * 3600 * 1000;

  // Determine preferred direction from the full set of nearby markers.
  const nearbyAll = allMarkers.filter(m => Math.abs(m.ms - timeMs) < threshMs && m.temp !== temp);
  let preferAbove = true;
  if (nearbyAll.length > 0) {
    preferAbove = temp >= Math.max(...nearbyAll.map(m => m.temp));
  }

  // Ladder: try preferred direction first, then flip, stepping further each pair.
  const base = 14, step = 18;
  const dir = preferAbove ? 1 : -1;
  const candidates = [];
  for (let i = 0; i <= 5; i++) {
    candidates.push( dir * (base + i * step));
    candidates.push(-dir * (base + i * step));
  }

  for (const yOff of candidates) {
    const conflict = placed.some(p =>
      Math.abs(p.ms - timeMs) < threshMs && Math.abs((p.yOff ?? 0) - yOff) < yPixThresh
    );
    if (!conflict) return yOff;
  }
  return dir * base;
}

// Mirror of weather_chart.py _edge_x_offset(): flip label to the left when the
// marker falls in the rightmost rightPct fraction of the x-axis.
function edgeXAdjust(timeMs, xMinMs, xMaxMs, rightPct = 0.70) {
  const span = xMaxMs - xMinMs;
  if (span <= 0) return { xAdjust: 6, textAlign: 'left' };
  const pos = timeMs - xMinMs;
  return pos / span > rightPct
    ? { xAdjust: -6, textAlign: 'right' }
    : { xAdjust: 6,  textAlign: 'left'  };
}

// xMin/xMax are the chart axis edges so red shading fills to the chart border,
// not just to the first/last observation timestamp. `obs` defaults to the live
// global so existing callers keep working; the notes view passes the snap's
// frozen obs object instead.
function buildCliAnnotations(xMin, xMax, obs = S.obs) {
  if (!obs || !obs.cli_windows || !obs.cli_windows.length) {
    return {};
  }
  const annotations = {};
  // Drop windows whose start is before xMin — those represent the *previous*
  // CLI day bleeding into the chart's leading hour. We want that leading hour
  // to render as the red "trailing hour of previous CLI day" zone instead.
  const wins = obs.cli_windows.filter(w => new Date(w.start) >= xMin);
  if (!wins.length) return {};

  wins.forEach((w, i) => {
    annotations[`cli_win_${i}`] = {
      type: 'box',
      drawTime: 'beforeDatasetsDraw',
      xMin: new Date(w.start),
      xMax: new Date(w.end),
      backgroundColor: 'rgba(62,207,142,0.07)',
      borderColor: 'rgba(62,207,142,0.22)',
      borderWidth: 1,
    };
  });

  const firstWS = new Date(wins[0].start);
  const lastWE  = new Date(wins[wins.length - 1].end);

  if (xMin < firstWS) {
    annotations['pre_win'] = {
      type: 'box', drawTime: 'afterDatasetsDraw',
      xMin: xMin, xMax: firstWS,
      backgroundColor: 'rgba(224,82,82,0.18)', borderWidth: 0,
    };
  }
  if (xMax > lastWE) {
    annotations['post_win'] = {
      type: 'box', drawTime: 'afterDatasetsDraw',
      xMin: lastWE, xMax: xMax,
      backgroundColor: 'rgba(224,82,82,0.18)', borderWidth: 0,
    };
  }
  for (let i = 0; i < wins.length - 1; i++) {
    annotations[`gap_${i}`] = {
      type: 'box', drawTime: 'afterDatasetsDraw',
      xMin: new Date(wins[i].end), xMax: new Date(wins[i + 1].start),
      backgroundColor: 'rgba(224,82,82,0.18)', borderWidth: 0,
    };
  }
  return annotations;
}

// Render the temperature chart. With no args it reads the live globals (S.*,
// the input fields) — original behavior. The notes view passes an opts bundle
// with `isSnap: true` so the function uses the snap's frozen state and doesn't
// mutate the live `chart` global or DOM titles.
function renderChart(opts = {}) {
  const isSnap = !!opts.isSnap;

  const {
    canvas        = $('temp-chart'),
    obs           = S.obs,
    forecast      = S.forecast,
    omFcast       = S.omFcast,
    omObs         = S.omObs,
    wethrObs      = S.wethrObs,
    compare       = S.compare,
    omCompare     = S.omCompare,
    mos           = S.mos,
    nwsVersions   = S.nwsVersions,
    nwsVerSelected = S.nwsVerSelected || {},
    units         = S.units,
    cityTz        = S.cityTz,
    forecastEnabled = S.forecastEnabled,
    cliWindowOnly = S.cliWindowOnly,
    cityTitle     = S.city,
    startInputVal = $('start-input')?.value,
    endInputVal   = $('end-input')?.value,
  } = opts;

  if (!obs) return null;

  const localSym = () => units === 'F' ? '°F' : '°C';

  if (!isSnap) {
    $('chart-placeholder').style.display = 'none';
    $('chart-city-title').textContent = cityTitle || '';
  }

  const obsPts = (obs.data || []).map(r => ({ x: new Date(r.time), y: r.temp }));
  // Use city-local midnight (from server) so xMin and annotations stay in the
  // city's timezone regardless of where the browser is.
  const xMin = obs.query_start
    ? new Date(obs.query_start)
    : luxon.DateTime.fromFormat(startInputVal, DT_FMT, { zone: cityTz }).toJSDate();
  if (obsPts.length && obsPts[0].x > xMin) {
    obsPts.unshift({ x: xMin, y: obsPts[0].y });
  }
  const datasets = [];

  datasets.push({
    label: `Observed (${localSym()})`,
    data: obsPts,
    borderColor: '#f97316',
    backgroundColor: '#f9731622',
    pointRadius: obsPts.length > 120 ? 0 : 2,
    pointHoverRadius: 4,
    borderWidth: 1.8,
    fill: true,
    tension: 0,
    order: 10,
  });

  let refStats;
  if (cliWindowOnly && obs.cli_windows && obs.cli_windows.length && obs.data) {
    refStats = computeStatsJS(filterToCLIWindows(obs.data, obs.cli_windows));
  }
  refStats = refStats || {
    min_temp: obs.min_temp, min_time: obs.min_time,
    max_temp: obs.max_temp, max_time: obs.max_time,
  };

  if (refStats.min_temp != null && obsPts.length) {
    datasets.push({
      label: `Min ${refStats.min_temp}${localSym()}`,
      data: obsPts.map(p => ({ x: p.x, y: refStats.min_temp })),
      borderColor: '#f9731688',
      borderDash: [4, 4],
      borderWidth: 1,
      pointRadius: 0,
      fill: false,
      order: 20,
    });
  }
  if (refStats.max_temp != null && obsPts.length) {
    datasets.push({
      label: `Max ${refStats.max_temp}${localSym()}`,
      data: obsPts.map(p => ({ x: p.x, y: refStats.max_temp })),
      borderColor: '#f5a62388',
      borderDash: [4, 4],
      borderWidth: 1,
      pointRadius: 0,
      fill: false,
      order: 20,
    });
  }

  if (forecastEnabled && forecast && forecast.rows && forecast.rows.length) {
    datasets.push({
      label: 'NWS Forecast',
      data: forecast.rows.map(r => ({ x: new Date(r.time), y: r.temp })),
      borderColor: '#f97316',
      backgroundColor: 'transparent',
      borderDash: [6, 4],
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      fill: false,
      tension: 0,
      order: 5,
    });
  }

  if (forecastEnabled && omFcast && omFcast.rows && omFcast.rows.length) {
    datasets.push({
      label: 'Open-Meteo (HRRR/GFS)',
      data: omFcast.rows.map(r => ({ x: new Date(r.time), y: r.temp })),
      borderColor: '#e2e8f0',
      backgroundColor: 'transparent',
      borderDash: [4, 5],
      borderWidth: 1.6,
      pointRadius: 0,
      pointHoverRadius: 4,
      fill: false,
      tension: 0,
      order: 4,
    });
  }

  if (compare && compare.rows && compare.rows.length) {
    const cmpPts = compare.rows.map(r => ({ x: new Date(r.time), y: r.temp }));
    datasets.push({
      label: `Prior (${compare.offset_days}d)`,
      data: cmpPts,
      borderColor: '#a78bfa',
      backgroundColor: 'transparent',
      borderDash: [8, 4, 2, 4],
      borderWidth: 1.6,
      pointRadius: 0,
      pointHoverRadius: 4,
      fill: false,
      tension: 0.2,
      order: 6,
    });
  }

  if (omCompare && omCompare.rows && omCompare.rows.length) {
    const omCmpPts = omCompare.rows.map(r => ({ x: new Date(r.time), y: r.temp }));
    datasets.push({
      label: `OM Prior (${omCompare.offset_days}d)`,
      data: omCmpPts,
      borderColor: '#2dd4bf',
      backgroundColor: 'transparent',
      borderDash: [8, 4, 2, 4],
      borderWidth: 1.6,
      pointRadius: 0,
      pointHoverRadius: 4,
      fill: false,
      tension: 0.2,
      order: 7,
    });
  }

  // NWS forecast versions (purple dash-dot) — Wethr archival
  if (nwsVersions && nwsVersions.versions_by_date) {
    const palette = ['#c4b5fd','#a78bfa','#8b5cf6','#7c3aed','#6d28d9'];
    for (const [dateStr, versions] of Object.entries(nwsVersions.versions_by_date)) {
      const n = versions.length;
      versions.forEach((ver, idx) => {
        const key = `${dateStr}|${idx}`;
        if (!nwsVerSelected[key]) return;
        const pts = ver.times.map((t, i) => ({ x: new Date(t), y: ver.temps[i] }));
        const recency = n > 1 ? (idx + 1) / n : 1.0;
        datasets.push({
          label: `NWS ${ver.label}`,
          data: pts,
          borderColor: palette[Math.min(idx, palette.length - 1)],
          backgroundColor: 'transparent',
          borderDash: [6, 3, 1, 3],
          borderWidth: 1.4,
          pointRadius: 0,
          pointHoverRadius: 3,
          fill: false,
          tension: 0,
          order: 5,
          animation: false,
        });
      });
    }
  }

  // GFS-MOS forecast line (gold dashed)
  if (forecastEnabled && mos && mos.gfs && mos.gfs.rows && mos.gfs.rows.length) {
    datasets.push({
      label: 'GFS-MOS',
      data: mos.gfs.rows.map(r => ({ x: new Date(r.time), y: r.temp })),
      borderColor: '#facc15',
      backgroundColor: 'transparent',
      borderDash: [3, 5],
      borderWidth: 1.5,
      pointRadius: 0,
      pointHoverRadius: 4,
      fill: false,
      tension: 0,
      order: 3,
    });
  }

  // LAMP forecast line (green solid) — hourly, most current, shorter range
  if (forecastEnabled && mos && mos.lav && mos.lav.rows && mos.lav.rows.length) {
    datasets.push({
      label: 'LAMP',
      data: mos.lav.rows.map(r => ({ x: new Date(r.time), y: r.temp })),
      borderColor: '#4ade80',
      backgroundColor: 'transparent',
      borderDash: [2, 4],
      borderWidth: 1.3,
      pointRadius: 0,
      pointHoverRadius: 4,
      fill: false,
      tension: 0,
      order: 2,
    });
  }

  if (omObs && omObs.rows && omObs.rows.length) {
    datasets.push({
      label: 'OM Observed',
      data: omObs.rows.map(r => ({ x: new Date(r.time), y: r.temp })),
      borderColor: '#e2e8f0',
      backgroundColor: '#e2e8f014',
      borderWidth: 1.4,
      pointRadius: 0,
      pointHoverRadius: 4,
      fill: true,
      tension: 0,
      order: 9,
    });
  }

  if (wethrObs && wethrObs.rows && wethrObs.rows.length) {
    datasets.push({
      label: 'Wethr Obs',
      data: wethrObs.rows.map(r => ({ x: new Date(r.time), y: r.temp })),
      borderColor: '#34d399',
      backgroundColor: '#34d39914',
      borderWidth: 1.2,
      pointRadius: 0,
      pointHoverRadius: 4,
      fill: false,
      tension: 0,
      order: 8,
    });
  }

  if (!isSnap && chart) { chart.destroy(); chart = null; }

  // The CLI daily window ends at ~00:59 AM; +1 min lands exactly on the 01:00 AM
  // boundary that the forecast cap also uses.  Fall back to last forecast row
  // (no padding) if CLI windows are unavailable.
  const endInputDate = luxon.DateTime.fromFormat(endInputVal, DT_FMT, { zone: cityTz }).toJSDate();
  let xMax = endInputDate;
  if (forecastEnabled) {
    if (obs && obs.cli_windows && obs.cli_windows.length) {
      const lastWinEnd = new Date(obs.cli_windows[obs.cli_windows.length - 1].end);
      const cap1AM = new Date(lastWinEnd.getTime() + 60 * 1000);
      if (cap1AM > xMax) xMax = cap1AM;
    } else {
      for (const rows of [
        (forecast && forecast.rows) ? forecast.rows : [],
        (omFcast  && omFcast.rows)  ? omFcast.rows  : [],
      ]) {
        if (rows.length) {
          const lastT = new Date(rows[rows.length - 1].time);
          if (lastT > xMax) xMax = lastT;
        }
      }
    }
  }

  // Extend prior-period lines to xMax (mirrors how orange extends left to xMin).
  for (const ds of datasets) {
    if (ds.label && ds.label.startsWith('Prior') || ds.label && ds.label.startsWith('OM Prior')) {
      const pts = ds.data;
      if (pts.length && pts[pts.length - 1].x < xMax) {
        pts.push({ x: xMax, y: pts[pts.length - 1].y });
      }
    }
  }

  const annotations = buildCliAnnotations(xMin, xMax, obs);

  // Shared placed-anchor list for overlap avoidance (mirrors _placed_anchors in weather_chart.py)
  const xMinMs = xMin instanceof Date ? xMin.getTime() : +xMin;
  const xMaxMs = xMax instanceof Date ? xMax.getTime() : +xMax;
  const placedAnchors = []; // [{ms, temp, yOff}]

  // Pre-collect every marker position (time + temp) across all series so that
  // pickYAdjust can determine label direction by relative temp rank at each x,
  // without needing to know what has been placed so far.
  const allMarkerPositions = [];
  {
    const _addMinMax = rows => {
      const inRangeRows = (rows || []).filter(r => {
        const ms = +new Date(r.time);
        return ms >= xMinMs && ms <= xMaxMs;
      });
      if (!inRangeRows.length) return;
      const mn = inRangeRows.reduce((a, b) => a.temp < b.temp ? a : b);
      const mx = inRangeRows.reduce((a, b) => a.temp > b.temp ? a : b);
      allMarkerPositions.push({ ms: +new Date(mn.time), temp: mn.temp });
      allMarkerPositions.push({ ms: +new Date(mx.time), temp: mx.temp });
    };
    if (forecastEnabled) {
      _addMinMax(forecast?.rows);
      _addMinMax(omFcast?.rows);
      _addMinMax(mos?.gfs?.rows);
      _addMinMax(mos?.lav?.rows);
    }
    _addMinMax(omObs?.rows);
    _addMinMax(wethrObs?.rows);
    if (refStats?.min_temp != null) {
      allMarkerPositions.push({ ms: +new Date(refStats.min_time), temp: refStats.min_temp });
      allMarkerPositions.push({ ms: +new Date(refStats.max_time), temp: refStats.max_temp });
    }
  }

  // Forecast rows can extend past the chart's xMax (e.g. GFS-MOS is capped
  // server-side with a small buffer, LAMP isn't capped at all). Marker points
  // must only consider rows that actually fall inside the visible chart range,
  // otherwise dots/labels end up rendered outside the plot area.
  const inRange = rows => (rows || []).filter(r => {
    const ms = +new Date(r.time);
    return ms >= xMinMs && ms <= xMaxMs;
  });

  // NWS forecast min/max markers
  const nwsRowsInRange = inRange(forecast?.rows);
  if (forecastEnabled && nwsRowsInRange.length) {
    const nwsRows = nwsRowsInRange;
    const nwsMinRow = nwsRows.reduce((a, b) => a.temp < b.temp ? a : b);
    const nwsMaxRow = nwsRows.reduce((a, b) => a.temp > b.temp ? a : b);
    const nwsMinX = new Date(nwsMinRow.time);
    const nwsMaxX = new Date(nwsMaxRow.time);
    const nwsMinEdge = edgeXAdjust(nwsMinX.getTime(), xMinMs, xMaxMs);
    const nwsMaxEdge = edgeXAdjust(nwsMaxX.getTime(), xMinMs, xMaxMs);
    const nwsMinY = pickYAdjust(nwsMinX.getTime(), nwsMinRow.temp, placedAnchors, allMarkerPositions);
    const nwsMaxY = pickYAdjust(nwsMaxX.getTime(), nwsMaxRow.temp, placedAnchors, allMarkerPositions);
    annotations['nws_min_pt'] = {
      type: 'point',
      xValue: nwsMinX, yValue: nwsMinRow.temp,
      backgroundColor: '#f97316', radius: 5,
      borderColor: '#fff', borderWidth: 1,
    };
    annotations['nws_min_lbl'] = {
      type: 'label',
      xValue: nwsMinX, yValue: nwsMinRow.temp,
      content: `${nwsMinRow.temp}${localSym()}`,
      color: '#f97316',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: nwsMinEdge.xAdjust, yAdjust: nwsMinY,
      backgroundColor: 'transparent', textAlign: nwsMinEdge.textAlign,
    };
    annotations['nws_max_pt'] = {
      type: 'point',
      xValue: nwsMaxX, yValue: nwsMaxRow.temp,
      backgroundColor: '#f97316', radius: 5,
      borderColor: '#fff', borderWidth: 1,
    };
    annotations['nws_max_lbl'] = {
      type: 'label',
      xValue: nwsMaxX, yValue: nwsMaxRow.temp,
      content: `${nwsMaxRow.temp}${localSym()}`,
      color: '#f97316',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: nwsMaxEdge.xAdjust, yAdjust: nwsMaxY,
      backgroundColor: 'transparent', textAlign: nwsMaxEdge.textAlign,
    };
    placedAnchors.push({ ms: nwsMinX.getTime(), temp: nwsMinRow.temp, yOff: nwsMinY });
    placedAnchors.push({ ms: nwsMaxX.getTime(), temp: nwsMaxRow.temp, yOff: nwsMaxY });
  }

  // Open-Meteo observed min/max markers
  // ── Placed BEFORE om forecast so placedAnchors is populated when forecast
  //    min is evaluated. omobs_max is hardcoded ABOVE the line (-20) because
  //    it structurally coincides with om forecast min (both sit at the
  //    obs/forecast handoff point).
  const omObsRowsInRange = inRange(omObs?.rows);
  if (omObsRowsInRange.length) {
    const omObsRows = omObsRowsInRange;
    const omObsMinRow = omObsRows.reduce((a, b) => a.temp < b.temp ? a : b);
    const omObsMaxRow = omObsRows.reduce((a, b) => a.temp > b.temp ? a : b);
    const omObsMinX = new Date(omObsMinRow.time);
    const omObsMaxX = new Date(omObsMaxRow.time);
    const omObsMinEdge = edgeXAdjust(omObsMinX.getTime(), xMinMs, xMaxMs);
    const omObsMaxEdge = edgeXAdjust(omObsMaxX.getTime(), xMinMs, xMaxMs);
    const omObsMinY = pickYAdjust(omObsMinX.getTime(), omObsMinRow.temp, placedAnchors, allMarkerPositions);
    const omObsMaxY = pickYAdjust(omObsMaxX.getTime(), omObsMaxRow.temp, placedAnchors, allMarkerPositions);
    annotations['omobs_min_pt'] = {
      type: 'point',
      xValue: omObsMinX, yValue: omObsMinRow.temp,
      backgroundColor: '#e2e8f0', radius: 5,
      borderColor: '#0f1117', borderWidth: 1,
    };
    annotations['omobs_min_lbl'] = {
      type: 'label',
      xValue: omObsMinX, yValue: omObsMinRow.temp,
      content: `${omObsMinRow.temp}${localSym()}`,
      color: '#e2e8f0',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: omObsMinEdge.xAdjust, yAdjust: omObsMinY,
      backgroundColor: 'transparent', textAlign: omObsMinEdge.textAlign,
    };
    annotations['omobs_max_pt'] = {
      type: 'point',
      xValue: omObsMaxX, yValue: omObsMaxRow.temp,
      backgroundColor: '#e2e8f0', radius: 5,
      borderColor: '#0f1117', borderWidth: 1,
    };
    annotations['omobs_max_lbl'] = {
      type: 'label',
      xValue: omObsMaxX, yValue: omObsMaxRow.temp,
      content: `${omObsMaxRow.temp}${localSym()}`,
      color: '#e2e8f0',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: omObsMaxEdge.xAdjust, yAdjust: omObsMaxY,
      backgroundColor: 'transparent', textAlign: omObsMaxEdge.textAlign,
    };
    placedAnchors.push({ ms: omObsMinX.getTime(), temp: omObsMinRow.temp, yOff: omObsMinY });
    placedAnchors.push({ ms: omObsMaxX.getTime(), temp: omObsMaxRow.temp, yOff: omObsMaxY });
  }

  // Wethr observed CLI-window min/max markers (emerald — mirrors NWS observed cliWindowOnly logic)
  const wethrObsRows = inRange(wethrObs?.rows);
  if (wethrObsRows.length) {
    let wethrStats;
    if (cliWindowOnly && obs?.cli_windows?.length) {
      const cliRows = filterToCLIWindows(wethrObsRows, obs.cli_windows);
      if (cliRows.length) wethrStats = computeStatsJS(cliRows);
    }
    wethrStats = wethrStats || computeStatsJS(wethrObsRows);
    if (wethrStats) {
      const wMinX = new Date(wethrStats.min_time);
      const wMaxX = new Date(wethrStats.max_time);
      const wMinEdge = edgeXAdjust(wMinX.getTime(), xMinMs, xMaxMs);
      const wMaxEdge = edgeXAdjust(wMaxX.getTime(), xMinMs, xMaxMs);
      const wMinY = pickYAdjust(wMinX.getTime(), wethrStats.min_temp, placedAnchors, allMarkerPositions);
      const wMaxY = pickYAdjust(wMaxX.getTime(), wethrStats.max_temp, placedAnchors, allMarkerPositions);
      annotations['wethr_min_pt'] = {
        type: 'point', xValue: wMinX, yValue: wethrStats.min_temp,
        backgroundColor: '#34d399', radius: 5,
        borderColor: '#0f1117', borderWidth: 1,
      };
      annotations['wethr_min_lbl'] = {
        type: 'label', xValue: wMinX, yValue: wethrStats.min_temp,
        content: `${wethrStats.min_temp}${localSym()}`,
        color: '#34d399',
        font: { size: 9, family: 'Consolas,monospace' },
        xAdjust: wMinEdge.xAdjust, yAdjust: wMinY,
        backgroundColor: 'transparent', textAlign: wMinEdge.textAlign,
      };
      annotations['wethr_max_pt'] = {
        type: 'point', xValue: wMaxX, yValue: wethrStats.max_temp,
        backgroundColor: '#34d399', radius: 5,
        borderColor: '#0f1117', borderWidth: 1,
      };
      annotations['wethr_max_lbl'] = {
        type: 'label', xValue: wMaxX, yValue: wethrStats.max_temp,
        content: `${wethrStats.max_temp}${localSym()}`,
        color: '#34d399',
        font: { size: 9, family: 'Consolas,monospace' },
        xAdjust: wMaxEdge.xAdjust, yAdjust: wMaxY,
        backgroundColor: 'transparent', textAlign: wMaxEdge.textAlign,
      };
      placedAnchors.push({ ms: wMinX.getTime(), temp: wethrStats.min_temp, yOff: wMinY });
      placedAnchors.push({ ms: wMaxX.getTime(), temp: wethrStats.max_temp, yOff: wMaxY });
    }
  }

  // Open-Meteo forecast min/max markers
  // ── om_min is hardcoded BELOW the line (+20) because it structurally
  //    coincides with omobs_max at the obs/forecast handoff point.
  const omFcastRowsInRange = inRange(omFcast?.rows);
  if (forecastEnabled && omFcastRowsInRange.length) {
    const omRows = omFcastRowsInRange;
    const omMinRow = omRows.reduce((a, b) => a.temp < b.temp ? a : b);
    const omMaxRow = omRows.reduce((a, b) => a.temp > b.temp ? a : b);
    const omMinX = new Date(omMinRow.time);
    const omMaxX = new Date(omMaxRow.time);
    const omMinEdge = edgeXAdjust(omMinX.getTime(), xMinMs, xMaxMs);
    const omMaxEdge = edgeXAdjust(omMaxX.getTime(), xMinMs, xMaxMs);
    const omMinY = pickYAdjust(omMinX.getTime(), omMinRow.temp, placedAnchors, allMarkerPositions);
    const omMaxY = pickYAdjust(omMaxX.getTime(), omMaxRow.temp, placedAnchors, allMarkerPositions);
    annotations['om_min_pt'] = {
      type: 'point',
      xValue: omMinX, yValue: omMinRow.temp,
      backgroundColor: '#e2e8f0', radius: 5,
      borderColor: '#0f1117', borderWidth: 1,
    };
    annotations['om_min_lbl'] = {
      type: 'label',
      xValue: omMinX, yValue: omMinRow.temp,
      content: `${omMinRow.temp}${localSym()}`,
      color: '#e2e8f0',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: omMinEdge.xAdjust, yAdjust: omMinY,
      backgroundColor: 'transparent', textAlign: omMinEdge.textAlign,
    };
    annotations['om_max_pt'] = {
      type: 'point',
      xValue: omMaxX, yValue: omMaxRow.temp,
      backgroundColor: '#e2e8f0', radius: 5,
      borderColor: '#0f1117', borderWidth: 1,
    };
    annotations['om_max_lbl'] = {
      type: 'label',
      xValue: omMaxX, yValue: omMaxRow.temp,
      content: `${omMaxRow.temp}${localSym()}`,
      color: '#e2e8f0',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: omMaxEdge.xAdjust, yAdjust: omMaxY,
      backgroundColor: 'transparent', textAlign: omMaxEdge.textAlign,
    };
    placedAnchors.push({ ms: omMinX.getTime(), temp: omMinRow.temp, yOff: omMinY });
    placedAnchors.push({ ms: omMaxX.getTime(), temp: omMaxRow.temp, yOff: omMaxY });
  }

  // GFS-MOS min/max markers (gold)
  const mosRowsInRange = inRange(mos?.gfs?.rows);
  if (forecastEnabled && mosRowsInRange.length) {
    const mosRows = mosRowsInRange;
    const mosMinRow = mosRows.reduce((a, b) => a.temp < b.temp ? a : b);
    const mosMaxRow = mosRows.reduce((a, b) => a.temp > b.temp ? a : b);
    const mosMinX = new Date(mosMinRow.time);
    const mosMaxX = new Date(mosMaxRow.time);
    const mosMinEdge = edgeXAdjust(mosMinX.getTime(), xMinMs, xMaxMs);
    const mosMaxEdge = edgeXAdjust(mosMaxX.getTime(), xMinMs, xMaxMs);
    const mosMinY = pickYAdjust(mosMinX.getTime(), mosMinRow.temp, placedAnchors, allMarkerPositions);
    const mosMaxY = pickYAdjust(mosMaxX.getTime(), mosMaxRow.temp, placedAnchors, allMarkerPositions);
    annotations['mos_min_pt'] = {
      type: 'point', xValue: mosMinX, yValue: mosMinRow.temp,
      backgroundColor: '#facc15', radius: 5,
      borderColor: '#0f1117', borderWidth: 1,
    };
    annotations['mos_min_lbl'] = {
      type: 'label', xValue: mosMinX, yValue: mosMinRow.temp,
      content: `${mosMinRow.temp}${localSym()}`,
      color: '#facc15', font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: mosMinEdge.xAdjust, yAdjust: mosMinY,
      backgroundColor: 'transparent', textAlign: mosMinEdge.textAlign,
    };
    annotations['mos_max_pt'] = {
      type: 'point', xValue: mosMaxX, yValue: mosMaxRow.temp,
      backgroundColor: '#facc15', radius: 5,
      borderColor: '#0f1117', borderWidth: 1,
    };
    annotations['mos_max_lbl'] = {
      type: 'label', xValue: mosMaxX, yValue: mosMaxRow.temp,
      content: `${mosMaxRow.temp}${localSym()}`,
      color: '#facc15', font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: mosMaxEdge.xAdjust, yAdjust: mosMaxY,
      backgroundColor: 'transparent', textAlign: mosMaxEdge.textAlign,
    };
    placedAnchors.push({ ms: mosMinX.getTime(), temp: mosMinRow.temp, yOff: mosMinY });
    placedAnchors.push({ ms: mosMaxX.getTime(), temp: mosMaxRow.temp, yOff: mosMaxY });
  }

  if (forecastEnabled && forecast && forecast.rows && forecast.rows.length && obsPts.length) {
    const nowX = obsPts[obsPts.length - 1].x;
    annotations['now_line'] = {
      type: 'line',
      xMin: nowX, xMax: nowX,
      borderColor: '#f9731688',
      borderWidth: 1.2,
      borderDash: [4, 3],
      label: {
        content: 'now',
        display: true,
        position: 'start',
        color: '#f97316',
        font: { size: 9, family: 'Consolas,monospace' },
        backgroundColor: 'transparent',
        padding: { top: 2, bottom: 2, left: 3, right: 3 },
        yAdjust: -8,
      },
    };
  }

  if (refStats && refStats.min_temp != null && refStats.min_time) {
    const minX = new Date(refStats.min_time);
    const maxX = new Date(refStats.max_time);
    const obsMinEdge = edgeXAdjust(minX.getTime(), xMinMs, xMaxMs);
    const obsMaxEdge = edgeXAdjust(maxX.getTime(), xMinMs, xMaxMs);
    const obsMinY = pickYAdjust(minX.getTime(), refStats.min_temp, placedAnchors, allMarkerPositions);
    const obsMaxY = pickYAdjust(maxX.getTime(), refStats.max_temp, placedAnchors, allMarkerPositions);
    annotations['obs_min_pt'] = {
      type: 'point',
      xValue: minX, yValue: refStats.min_temp,
      backgroundColor: '#f97316', radius: 5,
      borderColor: '#fff', borderWidth: 1,
    };
    annotations['obs_min_lbl'] = {
      type: 'label',
      xValue: minX, yValue: refStats.min_temp,
      content: `${refStats.min_temp}${localSym()}`,
      color: '#f97316',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: obsMinEdge.xAdjust, yAdjust: obsMinY,
      backgroundColor: 'transparent', textAlign: obsMinEdge.textAlign,
    };
    annotations['obs_max_pt'] = {
      type: 'point',
      xValue: maxX, yValue: refStats.max_temp,
      backgroundColor: '#f5a623', radius: 5,
      borderColor: '#fff', borderWidth: 1,
    };
    annotations['obs_max_lbl'] = {
      type: 'label',
      xValue: maxX, yValue: refStats.max_temp,
      content: `${refStats.max_temp}${localSym()}`,
      color: '#f5a623',
      font: { size: 9, family: 'Consolas,monospace' },
      xAdjust: obsMaxEdge.xAdjust, yAdjust: obsMaxY,
      backgroundColor: 'transparent', textAlign: obsMaxEdge.textAlign,
    };
    placedAnchors.push({ ms: minX.getTime(), temp: refStats.min_temp, yOff: obsMinY });
    placedAnchors.push({ ms: maxX.getTime(), temp: refStats.max_temp, yOff: obsMaxY });
  }

  const grid   = '#2d3148';
  const tick   = '#6b7280';

  // Dynamic Y-axis bounds: snap to multiples of 5, with enough headroom for labels.
  const _allTemps = datasets.flatMap(ds => ds.data.map(p => p.y)).filter(v => v != null && isFinite(v));
  let yAxisMin, yAxisMax;
  if (_allTemps.length) {
    const _tMax = Math.max(..._allTemps);
    const _tMin = Math.min(..._allTemps);
    yAxisMax = Math.ceil(_tMax / 5) * 5 + 5;
    yAxisMin = Math.floor(_tMin / 5) * 5 - 5;
  }

  const newChart = new Chart(canvas, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      layout: { padding: { top: 14, right: 42, bottom: 16, left: 42 } },
      interaction: { mode: 'x', intersect: false },
      scales: {
        x: {
          type: 'time',
          min: xMin,
          max: xMax,
          adapters: { date: { zone: cityTz || 'UTC' } },
          time: {
            tooltipFormat: 'MMM d  HH:mm',
            displayFormats: { hour: 'MMM d HH:mm', day: 'MMM d' },
          },
          grid: { color: grid },
          ticks: { color: tick, maxTicksLimit: 8, font: { family: 'Consolas,monospace', size: 10 } },
        },
        y: {
          ...(yAxisMin != null ? { min: yAxisMin } : {}),
          ...(yAxisMax != null ? { max: yAxisMax } : {}),
          grid: { color: grid },
          ticks: {
            color: tick,
            font: { family: 'Consolas,monospace', size: 10 },
            callback: v => v + localSym(),
          },
        },
      },
      plugins: {
        legend: {
          onClick(e, legendItem, legend) {
            Chart.defaults.plugins.legend.onClick.call(this, e, legendItem, legend);
            // Use the chart instance attached to the legend (works for both
            // the live chart and per-snap interactive charts).
            const c = legend.chart;
            const label = c.data.datasets[legendItem.datasetIndex]?.label || '';
            const visible = c.isDatasetVisible(legendItem.datasetIndex);
            const annotMap = {
              'NWS Forecast':          ['nws_min_pt','nws_min_lbl','nws_max_pt','nws_max_lbl','now_line'],
              'Open-Meteo (HRRR/GFS)': ['om_min_pt','om_min_lbl','om_max_pt','om_max_lbl'],
              'OM Observed':           ['omobs_min_pt','omobs_min_lbl','omobs_max_pt','omobs_max_lbl'],
              'Observed (°F)':         ['obs_min_pt','obs_min_lbl','obs_max_pt','obs_max_lbl'],
              'Observed (°C)':         ['obs_min_pt','obs_min_lbl','obs_max_pt','obs_max_lbl'],
              'GFS-MOS':               ['mos_min_pt','mos_min_lbl','mos_max_pt','mos_max_lbl'],
              'LAMP':                  ['lamp_min_pt','lamp_min_lbl','lamp_max_pt','lamp_max_lbl'],
            };
            const keys = annotMap[label] || [];
            const annots = c.options.plugins.annotation.annotations;
            keys.forEach(k => { if (k in annots) annots[k].display = visible; });
            c.update('none');
          },
          labels: { color: tick, font: { family: 'Consolas,monospace', size: 10 }, boxWidth: 16 },
        },
        tooltip: {
          backgroundColor: '#1a1d27',
          borderColor: '#2d3148', borderWidth: 1,
          titleColor: '#e8eaf0', bodyColor: '#e8eaf0',
          titleFont: { family: 'Consolas,monospace', size: 11 },
          bodyFont:  { family: 'Consolas,monospace', size: 11 },
          callbacks: {
            label: ctx => {
              const lbl = ctx.dataset.label;
              if (/^(Min|Max) /.test(lbl)) return ` ${lbl}`;
              return ` ${lbl}: ${ctx.parsed.y}${localSym()}`;
            },
          },
        },
        annotation: { clip: false, annotations },
      },
    },
  });

  if (!isSnap) chart = newChart;
  return newChart;
}
