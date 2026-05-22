'use strict';

// ── CLI rendering ─────────────────────────────────────────────────────────────
function renderCLI(result) {
  if (!result) return;
  S.cli = result;

  if (result.error) {
    $('cli-status').textContent = 'CLI error: ' + result.error;
    return;
  }

  $('cli-city').textContent = S.city ? '— ' + S.city : '';

  const issued = result.issued_at
    ? (() => { try { return new Date(result.issued_at).toUTCString().replace(' GMT','') + ' UTC'; } catch { return result.issued_at; } })()
    : '—';

  $('cli-high').textContent   = result.high_temp != null ? result.high_temp + '°F' : '—';
  $('cli-low').textContent    = result.low_temp  != null ? result.low_temp  + '°F' : '—';
  $('cli-precip').textContent = result.precip != null ? result.precip : '—';
  $('cli-precip').className   = 'cli-card-value' + (result.precip && result.precip !== '0.00' ? ' ok' : '');
  $('cli-date').textContent   = result.valid_date || '—';
  $('cli-issued').textContent = issued;
  $('cli-raw').textContent    = result.raw_text || '(no text)';
  $('cli-status').textContent = `Loaded · valid: ${result.valid_date || '?'} · issued: ${issued}`;
}

function updateCliPicker(products) {
  S.cliList = products || [];
  const sel = $('cli-picker');
  sel.innerHTML = '';
  if (!S.cliList.length) {
    sel.innerHTML = '<option>— no reports available —</option>';
    return;
  }
  S.cliList.forEach((p, i) => {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = p.label;
    sel.appendChild(opt);
  });
}

$('cli-load-btn').addEventListener('click', async () => {
  const idx = parseInt($('cli-picker').value);
  if (isNaN(idx) || !S.cliList.length) return;
  $('cli-status').textContent = 'Loading report…';
  try {
    const res = await fetch('/api/cli/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ city: S.city, index: idx }),
    });
    renderCLI(await res.json());
  } catch (e) {
    $('cli-status').textContent = 'Load failed: ' + e.message;
  }
});
