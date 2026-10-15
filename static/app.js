/* TasteFit front end. Vanilla JS, no build step. Streams /tenant (broker), /run (founder) and /demo (sample)
 * as Server-Sent Events; City Scan comes from /scan and /scan/unit. Qloo data lives in this page only. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const money = (n, cur) => n == null ? '–' : cur
  ? new Intl.NumberFormat('en', { style: 'currency', currency: cur, maximumFractionDigits: 0 }).format(n)
  : new Intl.NumberFormat('en', { maximumFractionDigits: 0 }).format(n);
const icon = (id, cls = 'i') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
const md = text => window.DOMPurify && window.marked ? DOMPurify.sanitize(marked.parse(text || '')) : `<pre>${esc(text)}</pre>`;
const LOCAL_LIMIT = 10;

/* Example points to start from. They are map points, not vacancy listings. */
const EXAMPLES = [
  { id: 'paris-oberkampf', name: 'Oberkampf, Paris', street: 'Example point near Rue Oberkampf', location: 'Oberkampf, Paris', lat: 48.8652, lon: 2.3752 },
  { id: 'paris-marais', name: 'Le Marais, Paris', street: 'Example point near Rue de Bretagne', location: 'Le Marais, Paris', lat: 48.8630, lon: 2.3622 },
  { id: 'chicago-wicker', name: 'Wicker Park, Chicago', street: 'Example point near Milwaukee Avenue', location: 'Wicker Park, Chicago', lat: 41.9088, lon: -87.6796 },
  { id: 'chicago-logan', name: 'Logan Square, Chicago', street: 'Example point near Logan Boulevard', location: 'Logan Square, Chicago', lat: 41.9233, lon: -87.7084 },
];
const SCAN_CITIES = { paris: [48.8566, 2.3522], chicago: [41.8781, -87.6298], philadelphia: [39.9526, -75.1652], casablanca: [33.5731, -7.5898] };

const ERRORS = {
  offline: ['Live taste data is not connected', 'This instance has no Qloo access right now. You can still read the sample memo, built from synthetic data.', true],
  internal: ['The analysis stopped before finishing', 'Something went wrong on our side. Run it again; if it repeats, try another point.', false],
  step_limit: ['The analysis ran out of steps', 'The agent kept asking without reaching a verdict. Try a broader location, such as the city name.', false],
  busy: ['TasteFit is busy', 'Too many analyses are running. Try again in a minute, or read the sample memo meanwhile.', true],
  dropped: ['The connection dropped', 'The stream ended before the result arrived. Run it again.', false],
  rejected: ['The request was not accepted', '', false],
  area: ['No taste data for this spot', '', false],
};

/* ---------------- state ---------------- */
const state = {
  mode: 'broker', unit: null, phase: 'empty', tab: 'shortlist', run: null,
  steps: [], unitInfo: null, shortlist: null, baseline: null, budget: null, memo: null, scorecard: null, geo: null,
  competitors: [], error: null, asking: null, streetRents: '', streetShort: false, concept: '', startedAt: 0, endedAt: 0, demo: null, portfolio: null, savedWhatIf: null,
};

const COPY = {
  broker: {
    'hero-title': 'Who should rent this unit?',
    'hero-lede': "Pick a storefront. An agent reads what the street already loves on Qloo's taste graph, checks what is already served nearby on open map data, and the rent each concept can carry.",
    'hero-cta': 'Pick a unit on the map', 'nav-cta': 'Open the map',
    'how-1': 'Pick the unit', 'how-1p': 'Choose an example point or drop a pin on any storefront. Add the asking rent if you have it.',
    'how-3': 'Take the memo to the meeting', 'how-3p': 'A one-page shortlist with a GO, ADJUST or NO-GO call per concept, the evidence behind it and the limits. Prints cleanly.',
  },
  founder: {
    'hero-title': 'Will your concept fit this street?',
    'hero-lede': "Name your concept and the neighbourhood. An agent checks it against what the area already loves on Qloo's taste graph, who you would compete with, and where demand concentrates.",
    'hero-cta': 'Pick a spot on the map', 'nav-cta': 'Open the map',
    'how-1': 'Name the concept, pick the spot', 'how-1p': 'Describe what you want to open in a few words, then name the neighbourhood you are considering.',
    'how-3': 'Decide before you sign', 'how-3p': 'A GO, ADJUST or NO-GO call with a fit score, the evidence behind it, the closest competitors and the limits.',
  },
};

/* ---------------- theme, mode, routing ---------------- */
const themeBtn = $('#theme');
function syncThemeLabel() {
  const dark = document.documentElement.dataset.theme === 'dark';
  themeBtn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
}
themeBtn.addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('tf-theme', next); } catch (e) {}
  syncThemeLabel();
});
syncThemeLabel();

function setMode(mode, { silent } = {}) {
  if (!COPY[mode]) return;
  state.mode = mode;
  document.documentElement.dataset.mode = mode;
  $$('.seg [role=radio]').forEach(b => { b.setAttribute('aria-checked', String(b.dataset.mode === mode)); b.tabIndex = b.dataset.mode === mode ? 0 : -1; });
  $$('[data-copy]').forEach(el => { const t = COPY[mode][el.dataset.copy]; if (t) el.textContent = t; });
  if (!silent && !$('#app').hidden) { resetRun(); renderPanel(); }
}
$$('.seg [role=radio]').forEach(b => {
  b.addEventListener('click', () => setMode(b.dataset.mode));
  b.addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
    e.preventDefault();
    const next = $$('.seg [role=radio]').find(x => x !== b);
    next.focus(); setMode(next.dataset.mode);
  });
});

function pinUnit(lat, lon, location = '') {
  if (lat == null) return { id: 'pin', name: location, street: 'Checked by neighbourhood name', location, lat: null, lon: null };
  return { id: 'pin', name: 'Your pin', street: `${lat.toFixed(4)}, ${lon.toFixed(4)}`, location, lat, lon, dropped: true };
}

function route() {
  const [path, query] = location.hash.replace(/^#/, '').split('?');
  const inApp = path === '/app';
  $('#landing').hidden = inApp; $('#app').hidden = !inApp;
  document.body.classList.toggle('in-app', inApp);
  if (!inApp) return;
  const q = new URLSearchParams(query || '');
  if (q.get('example') === 'wicker') {
    initMap();
    loadWicker();
    setTimeout(() => map && map.invalidateSize(), 50);
    return;
  }
  if (q.get('mode') && q.get('mode') !== state.mode) setMode(q.get('mode'), { silent: true });
  if (q.get('concept')) state.concept = q.get('concept').slice(0, 300);
  initMap();
  const lat = parseFloat(q.get('lat')), lon = parseFloat(q.get('lon'));
  const loc = (q.get('location') || '').slice(0, 120);
  const u = EXAMPLES.find(x => x.id === q.get('unit'))
    || (Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? pinUnit(lat, lon, loc)
      : state.mode === 'founder' && loc ? pinUnit(null, null, loc) : null);
  if (u && (!state.unit || state.unit.id !== u.id || state.unit.lat !== u.lat)) selectUnit(u, { fromRoute: true });
  else renderPanel();
  setTimeout(() => map && map.invalidateSize(), 50);
}
window.addEventListener('hashchange', route);

/* ---------------- map ---------------- */
let map, layerHeat, layerScan, dropMarker, scanLoaded = '';
function initMap() {
  if (map || !window.L) return;
  map = L.map('map', { zoomControl: true, scrollWheelZoom: true, keyboard: true }).setView([48.8645, 2.3765], 14);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  EXAMPLES.forEach(u => {
    const m = L.marker([u.lat, u.lon], {
      icon: L.divIcon({ className: '', html: `<div class="pin-unit" data-unit="${u.id}"></div>`, iconSize: [30, 30], iconAnchor: [15, 15] }),
      title: `${u.name}, ${u.street}`, alt: `${u.name}, ${u.street}`, riseOnHover: true, keyboard: true,
    }).addTo(map);
    m.bindTooltip(`${esc(u.name)}<br><span style="font-weight:500">Example point, not a listing</span>`, { className: 'tf-tip', direction: 'top', offset: [0, -16] });
    m.on('click', () => selectUnit(u));
  });
  layerHeat = L.layerGroup().addTo(map);
  layerScan = L.layerGroup();
  map.on('click', e => {
    if (state.phase === 'running') return;
    selectUnit(pinUnit(e.latlng.lat, e.latlng.lng));
  });
  map.on('moveend', () => { if ($('#scan-toggle').getAttribute('aria-pressed') === 'true') loadScan(); });
}
function placeDrop(u) {
  if (dropMarker) { dropMarker.remove(); dropMarker = null; }
  if (!map || !u || !u.dropped) return;
  dropMarker = L.marker([u.lat, u.lon], { icon: L.divIcon({ className: '', html: '<div class="pin-drop"></div>', iconSize: [22, 22], iconAnchor: [11, 22] }), keyboard: false }).addTo(map);
}
function highlightUnit(id) {
  $$('.pin-unit').forEach(p => p.classList.toggle('is-active', p.dataset.unit === id));
}
function drawHeat(points) {
  if (!map) return;
  layerHeat.clearLayers();
  const love = getComputedStyle(document.documentElement).getPropertyValue('--love').trim();
  (points || []).forEach(p => L.circleMarker([p.lat, p.lon], { radius: 5 + 6 * Math.min(1, p.w || 0), color: '#fff', weight: 2, fillColor: love, fillOpacity: 0.9 })
    .bindTooltip("Where the top concept's benchmark audience concentrates (Qloo)", { className: 'tf-tip' }).addTo(layerHeat));
}
function nearestScanCity() {
  const c = map.getCenter();
  const [slug, d] = Object.entries(SCAN_CITIES).map(([k, [la, lo]]) => [k, map.distance(c, [la, lo])]).sort((a, b) => a[1] - b[1])[0];
  return d < 60000 ? slug : null;
}
function shareNote(m) {
  return m && m.note ? `<p class="hint-line">${esc(m.note)}</p>` : '';
}
function rentTable(u) {
  const share = shareNote(u.market_share);
  if (!u.rent_ceiling) return `<p>${esc(u.note || u.reason || 'No rent ceiling for this cell.')}</p>${share}`;
  return `<table><tr><th>Tenant type</th><th>Max monthly rent</th></tr>${u.rent_ceiling.map(r => `<tr><td>${esc(r.label_en)}</td><td>${r.rent_max == null ? esc(r.note || 'Not viable') : esc(money(r.rent_max, u.currency))}</td></tr>`).join('')}</table>
    <p style="margin:8px 0 0;font-size:12px">${esc(u.note || '')}</p>${share}`;
}
async function loadScan() {
  const slug = nearestScanCity(), concept = $('#scan-concept') ? $('#scan-concept').value : 'specialty coffee';
  if (!slug) { layerScan.clearLayers(); scanLoaded = ''; toast('City Scan covers Paris, Chicago, Philadelphia and Casablanca.'); return; }
  if (scanLoaded === slug + concept) return;
  scanLoaded = slug + concept;
  try {
    const res = await fetch('/scan?' + new URLSearchParams({ city: slug, concept, top: 25 }), { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error((await res.text()).trim() || 'The scan did not load.');
    const d = await res.json();
    layerScan.clearLayers();
    if (d.data_thin) { toast(d.reason || 'Not enough map data for this city.'); return; }
    d.top.forEach(t => {
      const opp = (t.opportunity || {}).point ?? 0;
      const c = L.circle([t.centroid.lat, t.centroid.lon], { radius: 220, color: '#15203B', weight: 1, fillColor: opp >= 65 ? '#2445A8' : opp >= 50 ? '#7D93D6' : '#C3CAD3', fillOpacity: 0.25 + opp / 300 });
      c.bindPopup(`<div class="scan-pop"><b>${esc(opp)}</b> / 100 opportunity for ${esc(d.label)}<div class="scan-rent">Loading the rent ceiling</div></div>`);
      c.on('popupopen', async e => {
        const box = e.popup.getElement().querySelector('.scan-rent');
        try {
          const r = await fetch('/scan/unit?' + new URLSearchParams({ city: slug, lat: t.centroid.lat, lon: t.centroid.lon }), { headers: { Accept: 'application/json' } });
          box.innerHTML = r.ok ? rentTable(await r.json()) : esc((await r.text()).trim());
        } catch (err) { box.textContent = 'Could not load the rent ceiling.'; }
      });
      layerScan.addLayer(c);
    });
    toast(`City Scan: top ${d.top.length} blocks for ${d.label} in ${d.city}. Click one for its rent ceiling.`);
  } catch (err) {
    scanLoaded = '';
    toast(err.message || 'The scan did not load.');
  }
}
$('#scan-toggle').addEventListener('click', e => {
  const on = e.currentTarget.getAttribute('aria-pressed') !== 'true';
  e.currentTarget.setAttribute('aria-pressed', String(on));
  $('.lg-scan-item').hidden = !on; $('#scan-concept').hidden = !on;
  if (!map) return;
  if (on) { layerScan.addTo(map); loadScan(); } else { layerScan.remove(); toast('City Scan hidden'); }
});
$('#scan-concept').addEventListener('change', () => loadScan());

/* ---------------- panel rendering ---------------- */
const panel = $('#panel-body');
function paint(html) { panel.innerHTML = html; panel.classList.remove('panel-enter'); void panel.offsetWidth; panel.classList.add('panel-enter'); }

function exampleFlag() {
  const flag = $('#sample-flag');
  flag.hidden = !state.demo;
  if (state.demo) flag.textContent = state.demo.label || 'Sample data';
}
function renderPanel() {
  exampleFlag();
  if (state.phase === 'empty' || !state.unit) return paint(emptyHTML());
  if (state.phase === 'selected') return paint(unitCardHTML(true));
  if (state.phase === 'error') return paint(unitCardHTML(false) + errorHTML() + traceHTML());
  return paintRun();
}

function emptyHTML() {
  const broker = state.mode === 'broker';
  return `<div><h2>${broker ? 'Pick a unit' : 'Pick a spot for your concept'}</h2>
    <p class="sub">Click anywhere on the map to drop a pin, or start from an example point. Example points are places on a map, not vacancy listings.</p></div>
    <ul class="unit-list">${EXAMPLES.map(u => `<li><button type="button" class="unit-btn" data-unit="${u.id}">
      <span class="pin-unit" aria-hidden="true"></span>
      <span><b>${esc(u.name)}</b><small>${esc(u.street)}</small></span></button></li>`).join('')}</ul>
    <p class="hint-line">Turn on City Scan to see opportunity and rent ceilings by block (Paris, Chicago, Philadelphia, Casablanca).</p>`;
}

function unitFactsHTML() {
  const i = state.unitInfo;
  const city = i ? `<span><b>${esc(i.city || '')}</b>${i.currency ? ', rent ceiling calibrated' : ', no rent ceiling'}</span>` : '';
  return `<div class="unit-facts">${city}${state.mode === 'broker' && state.phase !== 'selected' && state.asking ? `<span>Asking <b>${esc(money(state.asking, i && i.currency))}</b> a month</span>` : ''}</div>${shareNote(i && i.market_share)}`;
}

function unitCardHTML(withForm) {
  const u = state.unit, broker = state.mode === 'broker';
  const back = `<button type="button" class="icon-btn" data-action="back" aria-label="Back to all points">${icon('back')}</button>`;
  const form = !withForm ? '' : broker
    ? `<form id="run-form" novalidate>
        <div class="field"><label for="asking">Asking rent per month <span style="font-weight:400">(optional)</span></label>
          <input class="input num" id="asking" name="asking" inputmode="numeric" autocomplete="off" value="${esc(state.asking ?? '')}" placeholder="e.g. 4200" aria-describedby="asking-help">
          <span class="help" id="asking-help">In the city's currency. Used to check each concept against the rent ceiling.</span>
          <span class="field-error" id="asking-err" hidden>Enter the rent as a number, for example 4200.</span></div>
        <div class="field"><label for="street">Other rents on this street <span style="font-weight:400">(optional)</span></label>
          <input class="input" id="street" name="street" autocomplete="off" value="${esc(state.streetRents)}" placeholder="e.g. 3800, 5100" aria-describedby="street-help">
          <span class="help" id="street-help">Other monthly rents on this street, comma-separated. Stable Street needs 3 to 5 rents in total and reuses this run's concepts. No extra Qloo call.</span>
          <span class="field-error" id="street-err" hidden>Enter monthly rents separated by commas, for example 3800, 5100.</span></div>
        <button class="btn btn-hl btn-lg btn-block" type="submit">Find tenants for this unit</button></form>`
    : `<form id="run-form" novalidate>
        <div class="field"><label for="concept">Your concept</label>
          <input class="input" id="concept" name="concept" maxlength="300" autocomplete="off" value="${esc(state.concept)}" placeholder="e.g. a ramen counter">
          <span class="field-error" id="concept-err" hidden>Describe the concept in a few words, for example "a ramen counter".</span></div>
        <div class="field"><label for="location">Neighbourhood and city</label>
          <input class="input" id="location" name="location" maxlength="120" autocomplete="off" value="${esc(u.location)}" placeholder="e.g. Wicker Park, Chicago">
          <span class="field-error" id="location-err" hidden>Name the neighbourhood and city, for example "Oberkampf, Paris".</span></div>
        <button class="btn btn-hl btn-lg btn-block" type="submit">Check the fit here</button></form>`;
  return `<section class="unit-card" aria-labelledby="unit-title">
      <div class="unit-head"><div><h2 id="unit-title">${esc(state.unitInfo && state.unitInfo.area || u.name)}</h2><p class="sub">${esc(u.street)}</p></div>${state.phase === 'running' ? '' : back}</div>
      ${unitFactsHTML()}${form}</section>`;
}

function errorHTML() {
  const e = state.error;
  return `<div class="state state-error" role="alert"><h3>${esc(e.title)}</h3><p>${esc(e.text)}</p>
    <div class="btns"><button type="button" class="btn btn-ink btn-sm" data-action="run-again">Run it again</button>
    ${e.sample ? '<button type="button" class="btn btn-ghost btn-sm" data-action="sample-memo">Read the sample memo</button>' : ''}
    <button type="button" class="btn btn-ghost btn-sm" data-action="back">Pick another point</button></div></div>`;
}

/* ----- trace ----- */
const STEP_TITLE = {
  locate: () => 'Placed the unit on the City Scan grid',
  area: () => 'Read what the area already loves',
  entity_tags: () => 'Tagged the favourite places',
  audience_demographics: () => 'Read the audience age skew',
  candidates: () => 'Proposed candidate concepts',
  find_tags: () => 'Matched each concept to a Qloo tag',
  benchmarks: () => 'Found a city benchmark for each concept',
  rank: () => 'Ranked the benchmarks for this audience',
  supply: () => 'Counted who already serves each concept nearby',
  compare_audiences: () => 'Measured audience overlap',
  where_popular: () => "Mapped where the top concept's audience concentrates",
  trends: () => 'Checked whether interest is rising or fading',
  score: () => 'Scored and checked the rent ceiling',
  baseline: () => 'Asked the same model without Qloo',
  memo: () => 'Wrote the memo',
};
const PLAN = ['locate', 'area', 'entity_tags', 'audience_demographics', 'find_tags', 'candidates', 'benchmarks', 'rank', 'supply', 'compare_audiences', 'where_popular', 'trends', 'score', 'baseline', 'memo'];
const FOUNDER_TITLE = {
  find_tags: a => `Translated "${a.query || 'the concept'}" into taste tags`,
  area_taste: a => `Read what ${a.location || 'the area'} already loves`,
  competitors: () => 'Ranked places that match your concept',
  hotspots: a => `Found where ${a.place || 'the closest match'} is most loved`,
  site_math: () => 'Computed the demand centre and best spot',
};

function stepKey(s) {
  if (s.workflow === 'osm') return 'supply';
  if (s.workflow === 'recommend') return /^Benchmark/.test(s.purpose) ? 'benchmarks' : /^Existing/.test(s.purpose) ? 'supply' : 'area';
  return STEP_TITLE[s.workflow] ? s.workflow : 'area';
}
function pushTraceRow(key, data) {
  const last = state.steps[state.steps.length - 1];
  if (last && last.key === key && key !== 'error') {
    if (data.call) last.calls.push(data.call);
    if (data.out) last.out = data.out;
    if (data.wf) last.wf = data.wf;
    last.warn = last.warn || data.warn; last.isNew = true;
    return last;
  }
  const row = { key, title: data.title || STEP_TITLE[key](data), out: data.out || '', kind: data.kind || 'qloo', calls: data.call ? [data.call] : [], wf: data.wf, warn: data.warn, fail: data.fail, isNew: true };
  state.steps.push(row);
  announce(row.title);
  return row;
}
function announce(msg) { const el = $('#sr-live'); if (el) el.textContent = msg; }
const listOf = (n, k = 4) => n.slice(0, k).join(', ') + (n.length > k ? ` and ${n.length - k} more` : '');

function receiptHTML(c, i, total) {
  const input = Object.entries(c.input || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(Array.isArray(v) ? (v.length > 3 ? `${v.length} IDs` : v.join(', ')) : v)}</dd>`).join('');
  const status = c.skipped ? `skipped: ${c.skipped}` : c.error ? `failed: ${c.error}` : (c.status || 'ok') + (c.cached ? ', from cache' : '');
  return `<div class="receipt"><div class="receipt-head"><span>Qloo call ${i + 1}${total > 1 ? ` of ${total}` : ''}</span><span>${esc(c.workflow)}</span></div>
    <dl>${input}${c.choice ? `<dt>chose</dt><dd>${esc(c.choice)}</dd>` : ''}${c.alternatives && c.alternatives.length ? `<dt>over</dt><dd>${esc(c.alternatives.join(', '))}</dd>` : ''}
    <dt>returned</dt><dd>${c.names && c.names.length ? esc(listOf(c.names, 6)) : 'no results'}</dd>
    <dt>status</dt><dd>${esc(status)}${c.ms != null ? `, ${esc(c.ms)} ms` : ''}</dd></dl>
    <div class="receipt-foot">${esc(c.purpose)}. The API key stays on the server.</div></div>`;
}

function traceHTML() {
  const running = state.phase === 'running';
  const founder = state.mode === 'founder';
  const plan = founder ? 5 : PLAN.length;
  const done = state.steps.length;
  const rows = state.steps.map(s => {
    const cls = s.fail ? 'fail' : s.warn ? 'warn' : 'done';
    const wf = s.kind === 'qloo' ? `<span class="wf">${esc(s.wf || '')}${s.calls.length > 1 ? ` ×${s.calls.length}` : ''}</span>`
      : s.kind === 'llm' ? '<span class="wf wf-llm">model</span>'
      : s.kind === 'osm' ? '<span class="wf wf-local">OpenStreetMap</span>' : '<span class="wf wf-local">TasteFit</span>';
    const rec = s.calls.length ? `<details><summary>${icon('chev')}${s.calls.length > 1 ? `${s.calls.length} receipts` : 'Receipt'}</summary>${s.calls.map((c, j) => receiptHTML(c, j, s.calls.length)).join('')}</details>` : '';
    return `<li class="step ${cls}${s.isNew ? ' enter' : ''}"><span class="dot">${s.fail ? icon('x') : icon('check')}</span><div>
      <div class="step-title">${esc(s.title)} ${wf}</div>${s.out ? `<div class="step-out">${esc(s.out)}</div>` : ''}${rec}</div></li>`;
  }).join('');
  const live = running ? `<li class="step live"><span class="dot"></span><div><div class="step-title">${esc(nextTitle())}</div><div class="step-out">Working</div></div></li>` : '';
  const elapsed = Math.max(0, Math.round(((state.endedAt || Date.now()) - state.startedAt) / 1000));
  const calls = state.budget ? state.budget.qloo_calls : state.steps.reduce((a, s) => a + s.calls.filter(c => !c.cached && !c.skipped).length, 0);
  return `<section class="trace" aria-labelledby="trace-title">
    <div class="trace-head"><h3 id="trace-title">${running ? 'Agent at work' : 'Agent trace'}</h3>
      <div class="trace-meta">${founder ? '' : `<span class="num">${calls} Qloo ${calls === 1 ? 'call' : 'calls'}</span>`}<span class="clock" id="clock">${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')}</span>
      ${running ? `<button type="button" class="btn btn-quiet btn-sm" data-action="stop">${icon('stop')}Stop</button>` : ''}</div></div>
    <div class="meter" role="progressbar" aria-label="Analysis progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(100 * Math.min(done, plan) / plan)}"><i style="--p:${100 * Math.min(done, plan) / plan}"></i></div>
    <ol class="steps">${rows}${live}</ol></section>`;
}
function nextTitle() {
  if (state.mode === 'founder') return 'Choosing the next question to ask';
  const doneKeys = new Set(state.steps.map(s => s.key));
  const k = PLAN.find(p => !doneKeys.has(p));
  return k ? STEP_TITLE[k]({}) : 'Finishing';
}

/* ----- results ----- */
const badge = (call, size = '') => `<span class="badge badge-${call === 'GO' ? 'go' : call === 'ADJUST' ? 'adjust' : 'nogo'} ${size}">${esc(call)}</span>`;
const rentState = note => /90% range|Not viable/.test(note || '') ? 'bad' : /negotiate/.test(note || '') ? 'mid' : /within/.test(note || '') ? 'ok' : 'none';
function trendHTML(t) {
  if (!t) return '';
  const dir = t.change >= 3 ? 'up' : t.change <= -3 ? 'down' : 'flat';
  const word = { up: 'Rising', flat: 'Steady', down: 'Fading' }[dir];
  return `<span class="chip"><span class="trend trend-${dir}">${icon('trend')}${word}</span><b>${t.change > 0 ? '+' : ''}${esc(t.change)} pts</b><span class="k">in ${esc(t.weeks)} wk</span></span>`;
}
function stackHTML(c) {
  const W = state.shortlist.weights;
  if (!W) return '';
  const d = Math.round(W.demand * (c.demand ?? 0)), w = Math.round(W.white_space * (c.white_space ?? 0)), o = Math.round(W.overlap * (c.overlap ?? 0));
  return `<div class="stack"><div class="stack-bar" role="img" aria-label="Score ${esc(c.score)}: demand ${d}, white space ${w}, audience overlap ${o} points">
      <i class="seg-demand" style="width:${d}%"></i><i class="seg-space" style="width:${w}%"></i><i class="seg-overlap" style="width:${o}%"></i></div>
    <div class="stack-key"><span><i class="seg-demand"></i>Demand ${d}</span><span><i class="seg-space"></i>White space ${w}</span><span><i class="seg-overlap"></i>Overlap ${o}</span></div></div>`;
}
function supplyChip(c) {
  if (c.local_supply == null) return '';
  if (c.supply_source === 'OpenStreetMap') {
    return `<span class="chip" title="OpenStreetMap venues within 600 m, against the count the city's food mix predicts"><span class="k">Nearby (OSM)</span><b>${esc(c.local_supply)}</b><span class="k">vs ${esc(c.supply_expected)} expected</span></span>
      <span class="chip"><span class="k">P(under-supplied)</span><b>${esc((c.p_under ?? 0).toFixed(2))}</b></span>
      ${c.coverage === 'low' ? '<span class="chip chip-warn" title="The supply estimate sits near the city average because few venues are expected here or the city\'s blocks vary little. It is not a measure of how complete the map is.">Data coverage: low</span>' : ''}`;
  }
  return `<span class="chip"><span class="k">Nearby (Qloo)</span><b>${c.local_supply >= LOCAL_LIMIT ? '10+' : esc(c.local_supply)}</b></span>`;
}
function callsFor(c) {
  const bench = c.benchmark && c.benchmark.name;
  return state.steps.flatMap(s => s.calls).filter(x => (x.purpose || '').includes(c.concept) || (bench && x.purpose.includes(bench)) || (c.trend && x.purpose.includes(c.trend.brand)));
}
const FEATURE = { taste: ['Taste over-index', 'Qloo'], supply: ['Supply gap', 'OpenStreetMap'], audience: ['Audience fit', 'Qloo'], rent: ['Rent headroom', 'City Scan'] };
function breakdownHTML(b) {
  const max = Math.max(1, ...Object.values(b.phi).map(Math.abs));
  const rows = Object.entries(b.phi).map(([k, v]) => {
    const w = Math.round(50 * Math.abs(v) / max);
    return `<li><span>${esc(FEATURE[k][0])} <span class="k">${esc(FEATURE[k][1])}</span></span>
      <span class="wf-track"><i class="${v >= 0 ? 'wf-pos' : 'wf-neg'}" style="width:${w}%;${v >= 0 ? 'left:50%' : `left:${50 - w}%`}"></i></span>
      <b class="num">${v > 0 ? '+' : ''}${esc(v.toFixed(1))}</b></li>`;
  }).join('');
  return `<details class="card-more"><summary class="btn btn-quiet btn-sm">${icon('chev')}Why: score breakdown${b.qloo_share != null ? `, Qloo ${Math.round(100 * b.qloo_share)}%` : ''}</summary>
    <div class="breakdown"><p class="hint-line">Explanatory score ${esc(b.score)} vs city-typical ${esc(b.baseline)}, split exactly (Shapley) across the evidence. Stated weights; it explains TasteFit's model, not cause and effect.</p>
    <ul class="wf-bars">${rows}</ul></div></details>`;
}
function cardHTML(c, i) {
  const rs = c.afford ? { over: 'bad', stretch: 'mid', ok: 'ok' }[c.afford] : rentState(c.rent_note), cur = state.shortlist.currency;
  const rentCls = { bad: 'status-bad', mid: 'status-mid', ok: 'status-ok', none: 'status-none' }[rs];
  const calls = callsFor(c);
  return `<li class="card${i === 0 ? ' is-top' : ''}">
    <div class="card-head"><span class="rank" aria-label="Rank ${i + 1}">${i + 1}</span>
      <div><div class="card-name">${esc(c.concept)}</div><div class="card-why">${esc(c.why)}</div></div>
      <div class="score"><span class="score-n">${esc(c.score)}<small>/100</small></span>${badge(c.call, 'badge-xs')}</div></div>
    ${stackHTML(c)}
    <div class="chips">
      <span class="chip"><span class="k">${state.demo && state.demo.saved ? 'Example rank' : 'Qloo rank'}</span><b>${esc(c.position)} of ${esc(c.of)}</b></span>
      ${supplyChip(c)}
      ${c.tgi_label ? `<span class="chip" title="Taste Gap: over-index (area vs city, Qloo) against OSM supply. Stated priors, uncalibrated."><span class="k">Taste Gap</span><b>${esc(c.tgi_label)}</b>${c.overindex != null ? `<span class="k">over-index ${c.overindex > 0 ? '+' : ''}${esc(c.overindex)}</span>` : ''}</span>` : ''}
      ${c.overlap != null ? `<span class="chip"><span class="k">Overlap</span><b>${esc((c.overlap / 100).toFixed(2))}</b></span>` : ''}
      ${trendHTML(c.trend)}
      ${c.hotspot_km != null ? `<span class="chip"><span class="k">Hotspot</span><b>${esc(c.hotspot_km)} km</b></span>` : ''}
    </div>
    <div class="rent-line"><span class="k">Rent</span><span><span class="status ${rentCls}">${c.rent_max ? `max ${esc(money(c.rent_max, cur))}` : 'no ceiling'}</span> ${esc(c.rent_note)}</span></div>
    ${c.adjust_note ? `<p class="adjust-note">${esc(c.adjust_note)}</p>` : ''}
    ${c.call_rule === 'rent-first' ? '<p class="hint-line">Rent-first call: the rent is checked before taste, and taste cannot rescue an unpayable rent.</p>' : ''}
    ${c.breakdown ? breakdownHTML(c.breakdown) : ''}
    ${calls.length ? `<details class="card-more"><summary class="btn btn-quiet btn-sm">${icon('chev')}Evidence: ${calls.length} Qloo ${calls.length === 1 ? 'call' : 'calls'}</summary><div class="receipts">${calls.map((x, j) => receiptHTML(x, j, calls.length)).join('')}</div></details>` : ''}
  </li>`;
}
function cents(n, cur) {
  if (n == null) return '–';
  const opts = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
  return cur ? new Intl.NumberFormat('en', { style: 'currency', currency: cur, ...opts }).format(n)
    : new Intl.NumberFormat('en', opts).format(n);
}
function portfolioHTML() {
  const p = state.portfolio;
  if (!p && state.streetShort) return `<section class="compare" aria-labelledby="port-title"><h3 id="port-title">Stable Street</h3><p id="port-proof">A street certificate needs 3 to 5 rents in total. This run had fewer, so no certificate was computed. No extra Qloo call.</p></section>`;
  if (!p) return '';
  const cur = state.shortlist && state.shortlist.currency;
  const rows = (p.matches || []).map(m => `<li>${esc(m.unit)} at ${esc(money(m.rent, cur))}/month: ${m.concept ? `${esc(m.concept)} (${esc(m.verdict)})` : 'unmatched'}</li>`).join('');
  const split = p.rent_split;
  const splitRows = split && split.rows ? split.rows.map(r => {
    const sign = r.contribution > 0 ? '+' : '';
    return `<li>${esc(r.unit)}${r.concept ? `, ${esc(r.concept)}` : ''}: neighbour contribution ${sign}${Number(r.contribution).toFixed(1)} (${esc(r.role)}). Asking ${esc(cents(r.asking, cur))}. Suggested ${esc(cents(r.suggested, cur))}.</li>`;
  }).join('') : '';
  const splitHTML = splitRows ? `<h4 id="rent-split-title">Suggested rent split</h4><p id="rent-split-label">${esc(split.label)}</p><p>${esc(split.note)}</p><ul>${splitRows}</ul><p>Landlord total ${esc(cents(split.total_asking, cur))} a month. Suggested total ${esc(cents(split.total_suggested, cur))}.</p>` : '';
  return `<section class="compare" aria-labelledby="port-title"><h3 id="port-title">Stable Street</h3><p id="port-proof">${esc(p.proof)}</p>${rows ? `<ul>${rows}</ul>` : ''}${splitHTML}${whatIfHTML(p)}</section>`;
}
function whatIfHTML(p) {
  if (!p || !(p.matches || []).length) return '';
  const options = (p.inputs || []).map(c => `<option value="${esc(c.id)}">${esc(c.id)}</option>`).join('');
  return `<form id="what-if"><h4 id="what-title">What if</h4>
    <p class="hint-line" id="what-help">Add or remove one tenant and re-run this street. A new name has no taste data in this run. No extra Qloo call.</p>
    <div class="field"><label for="what-remove">Remove a tenant</label>
      <select class="input" id="what-remove"><option value="">Keep the current tenants</option>${options}</select></div>
    <div class="field"><label for="what-add">Or add a tenant</label>
      <input class="input" id="what-add" maxlength="80" autocomplete="off" placeholder="supermarket at the corner"></div>
    <button type="submit" class="btn btn-ink btn-sm">Re-run this street</button>
    <div id="what-result">${state.savedWhatIf ? whatIfResultHTML(state.savedWhatIf) : ''}</div></form>`;
}
function whatIfResultHTML(data) {
  const lines = (data.changes || []).map(c => {
    if (c.before == null && c.after == null) return `<li>${esc(c.concept)}: not assigned.</li>`;
    if (c.before == null) return `<li>${esc(c.concept)}: added, value ${Number(c.after).toFixed(1)}.</li>`;
    if (c.after == null) return `<li>${esc(c.concept)}: removed. Previous value ${Number(c.before).toFixed(1)}.</li>`;
    const word = c.delta > 0 ? 'gains' : c.delta < 0 ? 'loses' : 'unchanged';
    return `<li>${esc(c.concept)}: ${Number(c.before).toFixed(1)} to ${Number(c.after).toFixed(1)} (${word} ${Math.abs(c.delta).toFixed(1)}).</li>`;
  }).join('');
  const stable = (data.after && data.after.proof) || '';
  return `${data.added_note ? `<p>${esc(data.added_note)}</p>` : ''}<ul>${lines}</ul><p id="what-stable">${esc(stable)}</p>`;
}
async function runWhatIf() {
  const p = state.portfolio;
  const add = ($('#what-add') && $('#what-add').value || '').trim();
  const remove = ($('#what-remove') && $('#what-remove').value) || '';
  const box = $('#what-result');
  if (!p || !box) return;
  if (Boolean(add) === Boolean(remove)) { toast('Add or remove one tenant'); return; }
  const payload = { units: (p.matches || []).map(m => ({ id: m.unit, rent: m.rent })), concepts: p.inputs || [], remove: remove || null, add: add || null };
  box.textContent = 'Re-running this street.';
  try {
    const res = await fetch('/street?' + new URLSearchParams({ payload: JSON.stringify(payload) }));
    const text = await res.text();
    if (!res.ok) { box.textContent = text; return; }
    box.innerHTML = whatIfResultHTML(JSON.parse(text));
  } catch (err) {
    box.textContent = 'The street could not be re-run.';
  }
}
function shortlistHTML() {
  const sl = state.shortlist, scored = sl.concepts.filter(c => c.call), rest = sl.concepts.filter(c => !c.call);
  if (!scored.length) return `<div class="state state-warn"><h3>No concept could be scored here</h3><p>Qloo did not return enough benchmarks for this area. Try a better-known neighbourhood.</p></div>${portfolioHTML()}`;
  return `<ol class="cards" aria-label="Ranked tenant shortlist">${scored.map(cardHTML).join('')}</ol>
    ${rest.length ? `<p class="unscored">Also tested: ${rest.map(c => `${esc(c.concept)}${c.position ? ` (rank ${esc(c.position)} of ${esc(c.of)})` : ''}`).join(', ')}. Only the top concepts by demand get the supply and audience checks.</p>` : ''}
    <p class="hint-line">${esc(sl.method)}</p>${portfolioHTML()}`;
}
const LEDGER_STATUS = { confirmed: ['status-ok', 'Confirmed'], contradicted: ['status-bad', 'Contradicted'], partly: ['status-mid', 'Partly'], 'not tested': ['status-none', 'Not tested'], excluded: ['status-none', 'Excluded'] };
const VENUE_STATUS = { exists: ['status-ok', 'Found in OSM'], ambiguous: ['status-mid', 'Ambiguous'], 'not found': ['status-none', 'Not found in OSM'] };
function compareHTML() {
  const b = state.baseline, sl = state.shortlist;
  if (!b || b.unavailable) return '<div class="state state-warn"><h3>No model-only answer this run</h3><p>The language model did not answer in time, so there is nothing to compare against.</p></div>';
  const n = s => b.candidates.filter(c => c.status === s).length;
  const venues = b.venues && b.venues.length ? `<div class="cmp-col"><h4>Venues the model named, checked against OpenStreetMap within ${esc(b.ledger.llm_venues.radius_m)} m</h4>
      ${b.venues.map(v => `<div class="cmp-item"><div class="top"><span>${esc(v.name)}</span><span class="status ${VENUE_STATUS[v.status][0]}">${VENUE_STATUS[v.status][1]}</span></div>${v.match ? `<small>Closest match: ${esc(v.match)}</small>` : ''}</div>`).join('')}
      <small>Not found means not in the open map near this point, which can be a mapping gap rather than an invented venue.</small></div>` : '';
  return `<section class="compare" aria-labelledby="cmp-title"><h3 id="cmp-title" class="sr">Without Qloo versus with Qloo</h3>
    <p class="compare-sum">The model alone named ${b.candidates.length} concepts: <b>${n('confirmed')} confirmed</b>, <b>${n('contradicted')} contradicted</b>, <b>${n('partly')} partly</b>, <b>${n('not tested')} not tested</b>${n('excluded') ? `, ${n('excluded')} excluded as alcohol-led` : ''}. Qloo surfaced <b>${(b.qloo_only || []).length}</b> it missed. This is agreement with TasteFit's evidence, not accuracy.</p>
    <div class="cmp-grid">
      <div class="cmp-col cmp-without"><h4>Without Qloo</h4>${b.candidates.map(c => { const s = LEDGER_STATUS[c.status] || LEDGER_STATUS['not tested']; return `<div class="cmp-item"><div class="top"><span>${esc(c.concept)}</span><span class="status ${s[0]}">${s[1]}</span></div><small>${esc(c.evidence)}</small></div>`; }).join('')}</div>
      <div class="cmp-col"><h4>With Qloo</h4>${sl.concepts.filter(c => c.call).map(c => `<div class="cmp-item"><div class="top"><span>${esc(c.concept)}</span>${badge(c.call, 'badge-xs')}</div>
        <small>Rank ${esc(c.position)} of ${esc(c.of)}${c.local_supply != null ? `, ${esc(c.local_supply)} nearby` : ''}, score ${esc(c.score)}.</small>${(b.qloo_only || []).includes(c.concept) ? '<span class="cmp-new">Missed by the model alone</span>' : ''}</div>`).join('')}</div>
      ${venues}
    </div></section>`;
}
function trialHTML() {
  const t = state.shortlist && state.shortlist.trial;
  if (!t || !t.sentence) return '';
  return `<p class="hint-line">${esc(t.sentence)}</p>`;
}
function footfallHTML() {
  const f = state.shortlist && state.shortlist.footfall;
  if (!f || !f.sentence) return '';
  return `<p class="hint-line">${esc(f.sentence)}</p>`;
}
function leadHTML() {
  const top = state.shortlist.concepts.find(c => c.call);
  if (!top) return '';
  const saved = state.demo && state.demo.label
    ? `<p class="hint-line" id="example-label"><b>${esc(state.demo.label)}</b>. ${esc(state.demo.notice || '')}</p>` : '';
  return `${saved}<section class="lead" aria-labelledby="lead-title"><div class="lead-row">${badge(top.call, 'badge-lg')}<span class="num" style="font-weight:700">${esc(top.score)}/100</span></div>
    <h3 id="lead-title">${esc(top.concept)} leads the shortlist for this unit.</h3>
    <p>Rank ${esc(top.position)} of ${esc(top.of)} for the ${esc(state.shortlist.area)} audience${top.local_supply != null ? `, ${esc(top.local_supply)} similar places nearby` : ''}${top.trend ? `, interest ${top.trend.change >= 3 ? 'rising' : top.trend.change <= -3 ? 'fading' : 'steady'}` : ''}. ${esc(top.rent_note)} ${esc(top.adjust_note || '')}</p>
    ${voiHTML(state.shortlist.voi)}${trialHTML()}${footfallHTML()}</section>`;
}
function voiHTML(v) {
  const top = v && v.checks.filter(c => c.above_noise);
  if (!top || !top.length) return '';
  const at = `${money(v.rent, v.currency)}/month ${v.rent_source === 'asking' ? 'asking' : 'at the ceiling'}`;
  return `<div class="voi"><p><b>Check first, before signing:</b> ${esc(top[0].label)}. For ${esc(v.concept)} at ${esc(at)}, knowing it is worth up to about <b>${esc(money(top[0].evppi, v.currency))}/month</b> of decision value${top[1] ? `; then ${esc(top[1].label)} (${esc(money(top[1].evppi, v.currency))})` : ''}.</p>
    <p class="hint-line">Value of information (EVPPI) from TasteFit's stated cost assumptions, not your figures: P(monthly profit &gt; 0) ${esc(Math.round(100 * v.p_profit))}%, all inputs known ${esc(money(v.evpi, v.currency))}/month. Monthly figures, no discounting.</p></div>`;
}
function founderResultHTML() {
  const sc = state.scorecard, g = state.geo;
  if (!sc || sc.fit == null) {
    return `<div class="state state-warn"><h3>No fit score this run</h3><p>The agent did not gather enough evidence to score the concept. The memo explains what it found.</p></div>`;
  }
  const color = sc.call === 'GO' ? 'var(--go)' : sc.call === 'ADJUST' ? 'var(--adjust)' : 'var(--nogo)';
  const where = state.demo ? state.demo.location : (state.location || 'this area');
  return `<section class="verdict-card" aria-labelledby="fv-title"><div class="dial" style="--v:${+sc.fit || 0};--c:${color}"><div><div><b>${esc(sc.fit)}</b><span>fit score</span></div></div></div>
    <div class="lead"><div class="lead-row">${badge(sc.call)}</div><h3 id="fv-title" style="font-size:var(--t-22)">${esc(state.concept.charAt(0).toUpperCase() + state.concept.slice(1))} in ${esc(where)}</h3></div></section>
    <ol class="cards">${(sc.bars || []).map(b => `<li class="card"><div class="card-head" style="grid-template-columns:1fr auto"><div class="card-name" style="font-size:var(--t-16)">${esc(b.name)}</div><span class="score-n" style="font-size:var(--t-22)">${esc(b.score ?? '–')}</span></div>
      <div class="stack-bar"><i class="seg-demand" style="width:${+b.score || 0}%"></i></div><p class="card-why">${esc(b.reason)}</p></li>`).join('')}</ol>
    ${state.competitors.length ? `<div><h3 style="font-size:var(--t-16);margin-bottom:8px">Closest competitors (${state.demo ? 'sample' : 'Qloo'})</h3><div class="chips">${state.competitors.map(c => `<span class="chip">${esc(c)}</span>`).join('')}</div></div>` : ''}
    ${g && g.recommended ? `<p class="hint-line">Suggested spot: opportunity index ${esc(g.recommended.opportunity_index)}, ${esc(g.recommended.nearest_competitor_km)} km from the nearest competitor, catchment ${esc(g.catchment_km)} km. Barycenters of ${esc(g.demand_points)} demand and ${esc(g.competitor_points)} competitor points; a geometric heuristic, not a site survey. The suggested spot is a block to scout, not a vetted address.</p>` : ''}
    ${sc.method ? `<p class="hint-line">${esc(sc.method)}</p>` : ''}`;
}

function paintRun() {
  const founder = state.mode === 'founder';
  exampleFlag();
  let body = '';
  if (state.phase === 'running') {
    body = traceHTML() + `<div aria-hidden="true" style="display:grid;gap:10px"><div class="sk" style="width:40%"></div><div class="sk" style="width:90%"></div><div class="sk" style="width:75%"></div></div>`;
  } else if (state.phase === 'stopped') {
    body = `<div class="state state-warn" role="status"><h3>Stopped after ${state.steps.length} steps</h3><p>Nothing was scored yet. The steps that finished are below.</p>
      <div class="btns"><button type="button" class="btn btn-ink btn-sm" data-action="run-again">Run it again</button></div></div>` + traceHTML();
  } else if (founder) {
    const tabs = [['verdict', 'Verdict'], ['trace', 'Agent trace', state.steps.length]];
    if (!tabs.some(t => t[0] === state.tab)) state.tab = 'verdict';
    body = tabsHTML(tabs) + `<div role="tabpanel" id="tp" aria-labelledby="tab-${state.tab}">${state.tab === 'trace' ? traceHTML() : founderResultHTML()}</div>` + actionsHTML();
  } else {
    const calls = state.budget ? state.budget.qloo_calls : state.steps.reduce((a, s) => a + s.calls.length, 0);
    const tabs = [['shortlist', 'Shortlist', state.shortlist.concepts.filter(c => c.call).length], ['compare', 'Without vs with Qloo'], ['trace', 'Agent trace', calls]];
    if (!tabs.some(t => t[0] === state.tab)) state.tab = 'shortlist';
    const pane = state.tab === 'compare' ? compareHTML() : state.tab === 'trace' ? traceHTML() : shortlistHTML();
    body = leadHTML() + tabsHTML(tabs) + `<div role="tabpanel" id="tp" aria-labelledby="tab-${state.tab}" tabindex="0">${pane}</div>` + actionsHTML();
  }
  const open = $$('details', panel).map((d, i) => d.open && i).filter(i => i !== false);
  panel.innerHTML = unitCardHTML(false) + body;
  const details = $$('details', panel);
  open.forEach(i => { if (details[i]) details[i].open = true; });
  state.steps.forEach(s => { s.isNew = false; });
  if (state.phase === 'running' && !open.length) {
    const live = $('.step.live', panel), box = panel;
    if (live && box.scrollHeight > box.clientHeight) {
      const below = live.getBoundingClientRect().bottom - box.getBoundingClientRect().bottom + 32;
      if (below > 0) box.scrollTop += below;
    }
  }
}
function tabsHTML(tabs) {
  return `<div class="tabs" role="tablist" aria-label="Results">${tabs.map(([id, label, n]) => `<button type="button" role="tab" class="tab" id="tab-${id}" data-tab="${id}" aria-selected="${state.tab === id}" aria-controls="tp" tabindex="${state.tab === id ? 0 : -1}">${label}${n != null ? `<span class="count">${n}</span>` : ''}</button>`).join('')}</div>`;
}
function actionsHTML() {
  return `<div class="actions">${state.memo ? `<button type="button" class="btn btn-ink" data-action="memo">${icon('print')}Open the memo</button>` : ''}
    <button type="button" class="btn btn-ghost" data-action="share">${icon('link')}Copy link</button></div>`;
}

/* ---------------- streaming ---------------- */
async function* sse(url, signal) {
  const res = await fetch(url, { signal, headers: { Accept: 'text/event-stream' } });
  if (!res.ok) { const e = new Error((await res.text()).trim()); e.status = res.status; throw e; }
  const reader = res.body.getReader(), dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = 'message', data = '';
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trimStart();
      }
      yield [event, data ? JSON.parse(data) : null];
    }
  }
}

function fail(code, text) {
  const [title, def, sample] = ERRORS[code] || ERRORS.internal;
  state.error = { code, title, text: text || def, sample };
  pushTraceRow('error', { title: `Stopped: ${title.toLowerCase()}`, out: text || def, kind: 'local', fail: true });
  announce(`Stopped. ${title}`);
}

const HANDLERS = {
  demo: d => { state.demo = d; state.concept = d.concept; state.location = d.location; },
  unit: d => { state.unitInfo = d; pushTraceRow('locate', { kind: 'local', out: [d.area, d.cell != null ? `City Scan cell ${d.cell}` : '', d.rent_note || ''].filter(Boolean).join('. ') }); },
  step: s => {
    if (state.mode === 'founder' || s.tool) {
      const names = s.names || [];
      if (s.tool === 'competitors') state.competitors = names.slice(0, 6);
      state.steps.push({ key: s.tool, title: (FOUNDER_TITLE[s.tool] || (() => s.tool))(s.args || {}), out: s.error ? `Failed: ${s.error}` : names.length ? 'Found ' + listOf(names) : s.tool === 'site_math' ? 'Placed every venue on the map' : 'No results', kind: s.tool === 'site_math' ? 'local' : 'qloo', wf: s.tool === 'area_taste' || s.tool === 'competitors' || s.tool === 'hotspots' ? 'recommend' : s.tool, calls: [], isNew: true, warn: !!s.error });
      announce(state.steps[state.steps.length - 1].title);
      return;
    }
    const key = stepKey(s), names = s.names || [];
    if (s.workflow === 'osm') { pushTraceRow(key, { kind: 'osm', out: `${s.purpose}: ${names.join(', ')}` }); return; }
    const warn = !!(s.error || s.skipped);
    const out = warn ? (s.skipped ? `Skipped: ${s.skipped}` : `Failed: ${s.error}`)
      : s.choice ? `Chose ${s.choice.split(' (')[0]}` + (s.alternatives && s.alternatives.length ? ` over ${s.alternatives.join(', ')}` : '')
      : names.length ? listOf(names) : 'No matches returned';
    const row = pushTraceRow(key, { wf: s.workflow, call: s, out: key === 'find_tags' ? null : out, warn });
    if (key === 'find_tags') row.out = `${row.calls.length} concepts matched, alternatives shown in each receipt`;
    if (key === 'benchmarks') row.out = `${row.calls.length} benchmarks, ${row.calls.filter(c => c.cached).length} from cache`;
    if (key === 'rank' && row.calls.length > 1) row.title = 'Ranked the benchmarks for this audience and for the whole city';
  },
  candidates: d => pushTraceRow('candidates', { kind: d.source === 'catalog' ? 'local' : 'llm', out: (d.source === 'catalog' ? 'From the built-in catalog (no model available): ' : '') + d.concepts.map(c => c.concept).join(', ') + '. Alcohol-led concepts excluded.' }),
  shortlist: d => { state.shortlist = d; drawHeat(d.heat); pushTraceRow('score', { kind: 'local', out: `${d.concepts.filter(c => c.call).length} concepts scored${d.currency ? ', rent joined from the City Scan model' : ''}` }); },
  portfolio: d => { state.portfolio = d; pushTraceRow('score', { kind: 'local', out: d.proof }); },
  baseline: d => { state.baseline = d; pushTraceRow('baseline', { kind: 'llm', out: d.unavailable ? 'No answer in time' : `${d.candidates.length} picks, checked against this run's evidence` }); },
  geo: g => { state.geo = g; drawFounderGeo(g); },
  scorecard: d => { state.scorecard = d; },
  memo: m => { state.memo = typeof m === 'string' ? { markdown: m, author: state.demo ? 'sample' : 'agent' } : m; if (state.mode === 'broker') pushTraceRow('memo', { kind: m.author === 'template' ? 'local' : 'llm', out: m.author === 'template' ? 'Template memo (no model available), with limits' : 'One page, with limits' }); },
  budget: d => { state.budget = d; },
  error: msg => {
    const code = typeof msg === 'string' && ERRORS[msg] ? msg : 'area';
    fail(code, code === 'area' ? String(msg) : '');
  },
};

function drawFounderGeo(g) {
  if (!map || !window.L) return;
  layerHeat.clearLayers();
  const love = getComputedStyle(document.documentElement).getPropertyValue('--love').trim();
  const comp = getComputedStyle(document.documentElement).getPropertyValue('--comp').trim();
  const pts = [];
  (g.demand || []).forEach(p => { pts.push([p.lat, p.lon]); L.circleMarker([p.lat, p.lon], { radius: 5 + 5 * Math.min(1, p.w || 0), color: '#fff', weight: 2, fillColor: love, fillOpacity: 0.9 }).bindTooltip(esc(p.name || 'Demand point'), { className: 'tf-tip' }).addTo(layerHeat); });
  (g.competition || []).forEach(p => { pts.push([p.lat, p.lon]); L.circleMarker([p.lat, p.lon], { radius: 6, color: comp, weight: 3, fillColor: '#fff', fillOpacity: 1 }).bindTooltip(`${esc(p.name || 'Competitor')}: similar concept`, { className: 'tf-tip' }).addTo(layerHeat); });
  if (g.recommended) {
    pts.push([g.recommended.lat, g.recommended.lon]);
    L.marker([g.recommended.lat, g.recommended.lon], { icon: L.divIcon({ className: '', html: '<div class="pin-drop"></div>', iconSize: [22, 22], iconAnchor: [11, 22] }) })
      .bindTooltip('Suggested spot', { className: 'tf-tip', permanent: true, direction: 'top', offset: [0, -20] }).addTo(layerHeat);
  }
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.2));
}

/* ---------------- run lifecycle ---------------- */
let clockTimer;
function resetRun() {
  if (state.run) { state.run.stopped = true; state.run.ctrl.abort(); }
  Object.assign(state, { phase: state.unit ? 'selected' : 'empty', run: null, steps: [], unitInfo: null, shortlist: null, portfolio: null, savedWhatIf: null, streetShort: false, baseline: null, budget: null, memo: null, scorecard: null, geo: null, competitors: [], error: null, demo: null, tab: 'shortlist', endedAt: 0 });
  clearInterval(clockTimer);
  if (layerHeat) layerHeat.clearLayers();
}
function shareQuery() {
  const u = state.unit, q = new URLSearchParams({ mode: state.mode });
  if (u.id !== 'pin') q.set('unit', u.id); else if (u.lat != null) { q.set('lat', u.lat.toFixed(5)); q.set('lon', u.lon.toFixed(5)); }
  if (state.mode === 'founder' && state.concept) q.set('concept', state.concept);
  if (u.location) q.set('location', u.location);
  return q;
}
function selectUnit(u, { fromRoute } = {}) {
  state.unit = u;
  resetRun();
  state.phase = 'selected';
  highlightUnit(u.id); placeDrop(u);
  if (map && u.lat != null) map.flyTo([u.lat, u.lon], Math.max(map.getZoom(), 15), { duration: reduced() ? 0 : 0.6 });
  renderPanel();
  if (!fromRoute) history.replaceState(null, '', '#/app?' + shareQuery());
  const first = $('#asking') || $('#concept');
  if (first && !fromRoute) first.focus({ preventScroll: true });
}

function invalid(id) {
  const el = $('#' + id);
  el.setAttribute('aria-invalid', 'true'); el.setAttribute('aria-describedby', `${id}-err`); $(`#${id}-err`).hidden = false; el.focus();
}

async function startRun({ demo } = {}) {
  const broker = state.mode === 'broker';
  let url;
  if (demo) {
    url = '/demo';
  } else if (broker) {
    if (state.unit.lat == null) { toast('Drop a pin on the map first'); return; }
    const raw = $('#asking').value.replace(/[\s,€$]/g, '');
    if (raw && !(+raw > 0)) return invalid('asking');
    state.asking = raw ? +raw : null;
    const streetRaw = $('#street').value.trim();
    const extras = streetRaw ? streetRaw.split(',').map(s => s.replace(/[\s€$]/g, '')).filter(Boolean) : [];
    if (extras.some(s => !(+s > 0))) return invalid('street');
    state.streetRents = streetRaw;
    const q = { lat: state.unit.lat.toFixed(6), lon: state.unit.lon.toFixed(6) };
    if (state.asking) q.rent = String(state.asking);
    const rents = [...(state.asking ? [state.asking] : []), ...extras.map(Number)];
    state.streetShort = Boolean(streetRaw) && rents.length < 3;
    if (rents.length >= 3) q.portfolio = extras.join(',');
    url = '/tenant?' + new URLSearchParams(q);
  } else {
    const c = $('#concept').value.trim(), loc = $('#location').value.trim();
    if (c.length < 3) return invalid('concept');
    if (loc.length < 3) return invalid('location');
    state.concept = c; state.location = loc; state.unit.location = loc;
    url = '/run?' + new URLSearchParams({ concept: c, location: loc });
  }
  const run = { stopped: false, ctrl: new AbortController() };
  Object.assign(state, { run, phase: 'running', steps: [], portfolio: null, startedAt: Date.now(), endedAt: 0, error: null });
  clearInterval(clockTimer);
  clockTimer = setInterval(() => { const el = $('#clock'); if (el) { const t = Math.round((Date.now() - state.startedAt) / 1000); el.textContent = `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`; } }, 1000);
  paintRun();
  let ended = false;
  try {
    for await (const [event, data] of sse(url, run.ctrl.signal)) {
      if (run.stopped) return;
      if (HANDLERS[event]) HANDLERS[event](data);
      if (event === 'error') { ended = true; break; }
      if (event === 'memo') ended = true;
      paintRun();
    }
  } catch (e) {
    if (run.stopped || e.name === 'AbortError') return;
    if (e.status === 429) fail('busy', e.message);
    else if (e.status) fail('rejected', e.message || `HTTP ${e.status}`);
    else fail('dropped');
    ended = true;
  }
  if (run.stopped) return;
  if (!ended) fail('dropped');
  clearInterval(clockTimer);
  state.endedAt = Date.now(); state.run = null;
  if (state.error) { state.phase = 'error'; renderPanel(); return; }
  state.phase = 'done'; state.tab = broker ? 'shortlist' : 'verdict';
  paintRun();
  toast(broker ? 'Shortlist ready' : 'Verdict ready');
  const h = $('#lead-title') || $('#fv-title');
  if (h) { h.setAttribute('tabindex', '-1'); h.focus({ preventScroll: false }); }
}

/* ---------------- memo ---------------- */
function memoHTML() {
  const today = new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
  const sl = state.shortlist, broker = !!sl, cur = sl && sl.currency;
  const title = broker ? `${state.unitInfo && state.unitInfo.area || state.unit.name}` : `${state.concept} in ${state.demo ? state.demo.location : state.location}`;
  const table = broker ? `<table class="memo-table"><thead><tr><th>Concept</th><th>Call</th><th>Score</th><th>Evidence</th><th>Rent</th></tr></thead><tbody>
      ${sl.concepts.filter(c => c.call).map(c => `<tr><td>${esc(c.concept)}</td><td>${badge(c.call, 'badge-xs')}</td><td>${esc(c.score)}</td>
        <td>Rank ${esc(c.position)} of ${esc(c.of)} (Qloo)${c.local_supply != null ? `; ${esc(c.local_supply)} nearby (${esc(c.supply_source)})` : ''}${c.overlap != null ? `; overlap ${esc((c.overlap / 100).toFixed(2))}` : ''}${c.coverage === 'low' ? '; data coverage low' : ''}</td>
        <td>${c.rent_max ? `max ${esc(money(c.rent_max, cur))}` : '–'}</td></tr>`).join('')}
    </tbody></table>` : '';
  const b = state.budget;
  return `<header class="memo-top"><span class="brand"><svg class="brand-mark" viewBox="0 0 28 28" aria-hidden="true"><rect class="f-ink" x="1" y="1" width="26" height="26" rx="6"/><rect class="s-hl" x="6" y="6" width="16" height="16" rx="2.5" fill="url(#hatch)" stroke-width="1.4"/></svg>TasteFit ${broker ? 'tenant' : 'fit'} memo</span>
      <div class="meta">${esc(today)}${state.demo ? '<br><span class="memo-stamp">SAMPLE DATA</span>' : ''}</div></header>
    <h1 id="memo-title">${esc(title)}</h1>
    ${broker ? `<p>${state.asking ? `Asking ${esc(money(state.asking, cur))} a month.` : 'No asking rent given.'} The unit is a point on a map, not a vetted address.</p>` : ''}
    ${state.demo ? `<p><b>${esc(state.demo.notice || 'Sample report: synthetic data.')}</b></p>` : ''}
    ${table}
    <div class="memo-md">${md(state.memo.markdown)}</div>
    <p class="hint-line">Written by ${esc(state.memo.author === 'template' ? 'the TasteFit template (no model available)' : state.memo.author || 'the agent')}.${b ? ` Sources: Qloo taste graph (${esc(b.qloo_calls)} calls, ${esc(b.workflows.length)} workflows)${broker ? ', © OpenStreetMap contributors, official business registers' : ''}.` : ''}</p>`;
}
function openMemo() {
  $('#memo-page').innerHTML = memoHTML();
  $('#memo').showModal();
}
let wickerLoad = 0;
async function loadWicker() {
  const token = ++wickerLoad;
  setMode('broker', { silent: true });
  const base = EXAMPLES.find(x => x.id === 'chicago-wicker');
  state.unit = Object.assign({}, base, {
    street: 'saved example from 2026-10-15. Example point near Milwaukee Avenue. Not a vetted address.',
  });
  resetRun();
  state.phase = 'running';
  state.startedAt = Date.now();
  state.steps = [{ key: 'example', title: 'Opening the saved Wicker Park example', out: 'Aggregates only. No Qloo call.', kind: 'local', calls: [], isNew: true }];
  paintRun();
  initMap();
  highlightUnit(base.id);
  placeDrop(base);
  if (map) map.setView([base.lat, base.lon], 15);
  try {
    const res = await fetch('/wicker-example.json', { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('missing');
    const doc = await res.json();
    if (token !== wickerLoad || !location.hash.includes('example=wicker')) return;
    state.demo = { saved: true, label: doc.label, notice: doc.notice, location: doc.place };
    state.asking = doc.asking_rent;
    state.unitInfo = doc.unit;
    state.shortlist = doc.shortlist;
    state.portfolio = doc.portfolio;
    state.savedWhatIf = doc.what_if;
    state.baseline = { unavailable: true };
    state.budget = { qloo_calls: 0, workflows: [] };
    state.memo = doc.memo;
    state.endedAt = Date.now();
    state.run = null;
    state.phase = 'done';
    state.tab = 'shortlist';
    paintRun();
    const h = $('#lead-title');
    if (h) { h.setAttribute('tabindex', '-1'); h.focus(); }
  } catch (err) {
    if (token !== wickerLoad) return;
    state.error = { title: 'The saved example did not load', text: 'The Wicker Park snapshot is not available.', sample: true };
    state.phase = 'error';
    renderPanel();
  }
}
function openWicker() {
  if (state.phase === 'running') return;
  const want = '#/app?unit=chicago-wicker&example=wicker';
  if (location.hash !== want) { location.hash = '/app?unit=chicago-wicker&example=wicker'; return; }
  loadWicker();
}
async function openSample() {
  if (state.phase === 'running') return;
  if (location.hash.split('?')[0] !== '#/app') { location.hash = '/app'; await new Promise(r => setTimeout(r, 60)); }
  setMode('founder', { silent: true });
  state.unit = { id: 'demo', name: 'Sample report', street: 'Synthetic data, not live Qloo output', location: '', lat: 0, lon: 0 };
  resetRun();
  toast('Loading the sample report');
  await startRun({ demo: true });
  if (state.memo) openMemo();
}

/* ---------------- events ---------------- */
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 2600);
}
document.addEventListener('click', e => {
  const goApp = e.target.closest('[data-go-app]');
  if (goApp) {
    e.preventDefault();
    if (location.hash !== '#/app') location.hash = '/app'; else route();
    setTimeout(() => (panel.querySelector('.unit-btn') || panel).focus(), 120);
    return;
  }
  const ub = e.target.closest('[data-unit]');
  if (ub && ub.tagName === 'BUTTON') { const u = EXAMPLES.find(x => x.id === ub.dataset.unit); if (u) selectUnit(u); return; }
  const tab = e.target.closest('[role=tab]');
  if (tab) { state.tab = tab.dataset.tab; paintRun(); $(`#tab-${state.tab}`).focus(); return; }
  const a = e.target.closest('[data-action]');
  if (!a) return;
  const act = a.dataset.action;
  if (act === 'back') { state.unit = null; resetRun(); state.phase = 'empty'; placeDrop(null); highlightUnit(null); history.replaceState(null, '', '#/app'); renderPanel(); }
  else if (act === 'stop') { state.run.stopped = true; state.run.ctrl.abort(); state.run = null; state.phase = 'stopped'; state.endedAt = Date.now(); clearInterval(clockTimer); paintRun(); }
  else if (act === 'run-again') { if (state.unit && state.unit.id === 'demo') { openSample(); return; } const keep = { concept: state.concept, asking: state.asking }; resetRun(); Object.assign(state, keep, { phase: 'selected' }); renderPanel(); }
  else if (act === 'memo') openMemo();
  else if (act === 'sample-memo') openSample();
  else if (act === 'wicker-example') openWicker();
  else if (act === 'print') window.print();
  else if (act === 'close-memo') $('#memo').close();
  else if (act === 'share') {
    const url = state.unit && state.unit.id !== 'demo' ? location.href.split('#')[0] + '#/app?' + shareQuery() : location.href;
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(() => toast('Link copied'), () => toast('Copy this link: ' + url));
  }
});
document.addEventListener('submit', e => {
  if (e.target.id === 'what-if') { e.preventDefault(); runWhatIf(); return; }
  if (e.target.id !== 'run-form') return;
  e.preventDefault();
  startRun();
});
document.addEventListener('input', e => {
  if (e.target.matches('.input[aria-invalid="true"]')) {
    e.target.removeAttribute('aria-invalid');
    const err = $(`#${e.target.id}-err`); if (err) err.hidden = true;
    const help = $(`#${e.target.id}-help`); help ? e.target.setAttribute('aria-describedby', help.id) : e.target.removeAttribute('aria-describedby');
  }
});
document.addEventListener('keydown', e => {
  const tab = e.target.closest && e.target.closest('[role=tab]');
  if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
  e.preventDefault();
  const tabs = $$('[role=tab]'), i = tabs.indexOf(tab);
  const next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  state.tab = tabs[next].dataset.tab; paintRun(); $(`#tab-${state.tab}`).focus();
});
$('#memo').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.close(); });

/* ---------------- boot ---------------- */
{
  const q = new URLSearchParams(location.search);
  if (q.get('mode') === 'broker' || q.get('mode') === 'founder') setMode(q.get('mode'), { silent: true });
  else setMode('broker', { silent: true });
  if (q.get('c') && q.get('l')) {
    setMode('founder', { silent: true });
    history.replaceState(null, '', location.pathname + '#/app?' + new URLSearchParams({ mode: 'founder', concept: q.get('c').slice(0, 300), location: q.get('l').slice(0, 120) }));
  } else if (q.get('mode')) {
    history.replaceState(null, '', location.pathname + '#/app?mode=' + state.mode);
  }
}
route();
