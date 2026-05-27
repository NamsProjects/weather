'use strict';

// ── Main fetch ────────────────────────────────────────────────────────────────
async function doFetch() {
  const start = $('start-input').value;
  const end   = $('end-input').value;
  if (!start || !end) { setStatus('Set a start and end date/time.', 'err'); return; }

  S.fetchToken++;
  const token = S.fetchToken;

  S.obs = S.forecast = S.omFcast = S.omObs = S.wethrObs = S.compare = S.omCompare = S.mos = null;
  S.nwsVersions = null; S.nwsVerSelected = {};
  renderFcStrip();
  renderNwsVersionList();

  const btn = $('fetch-btn');
  btn.disabled = true;
  btn.textContent = 'Fetching…';
  setStatus('Contacting NWS API…');

  ['val-station','val-records','val-min','val-max',
   'sub-station','sub-records','sub-min','sub-max','dur-min','dur-max']
    .forEach(id => $(id).textContent = '—');

  try {
    const res = await fetch('/api/observations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        city: S.city, start, end,
        units: S.units, interval: S.interval,
        source: S.source.toLowerCase(), tol: S.tolerance,
        browser_tz: S.cityTz,
      }),
    });
    const result = await res.json();
    if (token !== S.fetchToken) return;

    if (result.error) { setStatus('Error: ' + result.error, 'err'); return; }

    S.obs = result;
    if (result.timezone) S.cityTz = result.timezone;
    renderCards();
    renderChart();
    $('json-text').value = JSON.stringify(result, null, 2);
    setStatus(`Done — ${(result.data||[]).length} records from ${result.station}.`, 'ok');
    autoPopulateHighRes(result, result.query_start || start, end);
    fetchNwsVersions().catch(() => {});
    startWethrLive(S.city);
  } catch (e) {
    if (token !== S.fetchToken) return;
    setStatus('Network error: ' + e.message, 'err');
    return;
  } finally {
    btn.disabled = false;
    btn.textContent = '▶  Fetch Data';
  }

  // Use the actual last obs time so forecast starts from "now", not query end.
  let forecastEnd = end;
  if (S.obs && S.obs.data && S.obs.data.length) {
    const lastObsTime = S.obs.data[S.obs.data.length - 1].time;
    try {
      forecastEnd = lastObsTime.replace('Z', '+00:00');
    } catch (e) {
      console.log('[WARN] Failed to process obs timestamp, using user end:', e);
    }
  }

  if (S.forecastEnabled) {
    S.forecast = 'loading';
    S.omFcast  = 'loading';
  }
  S.omObs    = 'loading';
  S.mos      = 'loading';
  if (S.compareEnabled) { S.compare = 'loading'; S.omCompare = 'loading'; }
  renderFcStrip();

  fetchKalshi().catch(() => {});

  fetch('/api/mos', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      city: S.city, units: S.units, browser_tz: S.cityTz,
      end: luxon.DateTime.fromFormat(end, DT_FMT, { zone: S.cityTz }).toUTC().toISO(),
    }),
  }).then(r => r.json()).then(data => {
    if (token !== S.fetchToken) return;
    S.mos = data.error ? 'error' : data;
    renderFcStrip();
    renderChart();
  }).catch(() => {
    if (token === S.fetchToken) { S.mos = 'error'; renderFcStrip(); }
  });

  const parallel = [];

  if (S.forecastEnabled) {
    parallel.push(
      fetch('/api/forecast', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ city: S.city, start, end: forecastEnd, units: S.units, browser_tz: S.cityTz }),
      }).then(r => r.json()).then(data => {
        if (token !== S.fetchToken) return;
        S.forecast = data.error ? 'error' : data;
        renderFcStrip();
        renderChart();
      }).catch(() => {
        if (token === S.fetchToken) { S.forecast = 'error'; renderFcStrip(); }
      }),

      fetch('/api/forecast/openmeteo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ city: S.city, start, end: forecastEnd, units: S.units, browser_tz: S.cityTz }),
      }).then(r => r.json()).then(data => {
        if (token !== S.fetchToken) return;
        S.omFcast = data.error ? 'error' : data;
        renderFcStrip();
        renderChart();
      }).catch(() => {
        if (token === S.fetchToken) { S.omFcast = 'error'; renderFcStrip(); }
      })
    );
  }

  const wethrStation = typeof WETHR_STATIONS !== 'undefined' ? WETHR_STATIONS[S.city] : null;
  if (wethrStation) {
    parallel.push(
      fetch('/api/observed/wethr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ station: wethrStation, start, end, units: S.units }),
      }).then(r => r.json()).then(data => {
        if (token !== S.fetchToken) return;
        S.wethrObs = data.error ? 'error' : data;
        renderChart();
      }).catch(() => {
        if (token === S.fetchToken) S.wethrObs = 'error';
      })
    );
  }

  parallel.push(
    fetch('/api/observed/openmeteo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ city: S.city, start, end, units: S.units, browser_tz: S.cityTz }),
    }).then(r => r.json()).then(data => {
      if (token !== S.fetchToken) return;
      S.omObs = data.error ? 'error' : data;
      renderFcStrip();
      renderChart();
    }).catch(() => {
      if (token === S.fetchToken) { S.omObs = 'error'; renderFcStrip(); }
    }),

    fetch('/api/cli', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ city: S.city }),
    }).then(r => r.json()).then(data => {
      if (token !== S.fetchToken) return;
      renderCLI(data);
    }).catch(() => {}),

    fetch('/api/cli/list', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ city: S.city }),
    }).then(r => r.json()).then(data => {
      if (token !== S.fetchToken) return;
      updateCliPicker(data.products || []);
    }).catch(() => {})
  );

  if (S.compareEnabled) {
    const cmp = fetch('/api/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        city: S.city, start, end: forecastEnd,
        offset_days: S.compareOffset, units: S.units, browser_tz: S.cityTz,
      }),
    }).then(r => r.json()).then(data => {
      if (token !== S.fetchToken) return;
      S.compare = data.error ? 'error' : data;
      $('compare-status').textContent = S.compare === 'error'
        ? 'Compare fetch failed'
        : `✓ ${data.rows.length} pts (${data.offset_days}d prior)`;
      renderFcStrip();
      renderChart();
    }).catch(() => {
      if (token === S.fetchToken) {
        S.compare = 'error';
        $('compare-status').textContent = 'Compare fetch failed';
        renderFcStrip();
      }
    });
    parallel.push(cmp);

    const omCmp = fetch('/api/compare/openmeteo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        city: S.city, start, end: forecastEnd,
        offset_days: S.compareOffset, units: S.units, browser_tz: S.cityTz,
      }),
    }).then(r => r.json()).then(data => {
      if (token !== S.fetchToken) return;
      S.omCompare = data.error ? 'error' : data;
      renderFcStrip();
      renderChart();
    }).catch(() => {
      if (token === S.fetchToken) { S.omCompare = 'error'; renderFcStrip(); }
    });
    parallel.push(omCmp);
  }

  await Promise.allSettled(parallel);
}

$('fetch-btn').addEventListener('click', doFetch);
