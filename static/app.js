/* TasteFit Chicago: pin → Census + licences + taste exhibit.
 * Affine lease R = b + αG holds the landlord's harvest and buys survival.
 * Happy path is GET /spot, /ask, /taste. /run is never called from this page. */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (n, cur) => n == null ? '–' : new Intl.NumberFormat('en', {
  style: 'currency', currency: cur || 'USD', maximumFractionDigits: 0,
}).format(n);
const pct1 = p => p == null ? '–' : `${(100 * p).toFixed(1)}%`;
const in100 = p => Math.max(0, Math.min(100, Math.round(100 * p)));

const CITY = 'chicago';
const GROUPS = ['restaurant', 'fast_food', 'bar_cafe'];
const GROUP_NAME = { restaurant: 'Restaurant', fast_food: 'Fast food', bar_cafe: 'Bar & café' };
const ASSET_RHO = 0.12;
const WICKER = [41.9088, -87.6796];
const CONTACT = 'youssefbenzidiya@gmail.com';

const STALL_AFTER_MS = 8000;
function stallOut(quietMs) {
  if (quietMs <= STALL_AFTER_MS) return 'Working';
  return `Still working… ${Math.floor(quietMs / 1000)}s on this step`;
}
function quietMs() {
  const since = state.lastEventAt || state.startedAt || Date.now();
  return Date.now() - since;
}
function nextTitle() { return 'Qloo agent'; }
let clockTimer = null;
function tickRun() {
  const out = $('#live-out');
  if (out) out.textContent = stallOut(quietMs());
}
function pushTraceRow(key, row) {
  const ol = $('#agent .agent-log');
  if (!ol) return;
  const li = document.createElement('li');
  li.textContent = (row && (row.title || row.out)) || key;
  ol.appendChild(li);
}
const HANDLERS = {
  started: () => pushTraceRow('open', { kind: 'local', title: 'Opening the stream', out: 'The unit scan comes next. No taste result yet.' }),
  step: d => pushTraceRow('step', { title: (d && (d.workflow || d.source)) || 'Qloo' }),
  shortlist: d => pushTraceRow('shortlist', { title: 'Shortlist' }),
  memo: () => pushTraceRow('memo', { title: 'Memo on this pin' }),
  error: () => pushTraceRow('error', { title: 'Agent stopped' }),
};
function consumeSSE(text) {
  let event = 'message', payload = null;
  for (const line of String(text).split('\n')) {
    if (line.startsWith(':')) continue;
    const named = line.startsWith('event:') || line.startsWith('data:');
    if (!named) continue;
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) payload = line.slice(5).trim();
  }
  const parseData = data => data ? JSON.parse(data) : null;
  const data = parseData(payload);
  if (HANDLERS[event]) HANDLERS[event](data);
}
const state = {
  asking: 4200,
  capex: 450000,
  capexTouched: false,
  usedDefaultCapex: false,
  termYears: 5,
  belief: null,
  share: 0,
  group: 'restaurant',
  concept: 'Korean fried chicken',
  lat: WICKER[0],
  lon: WICKER[1],
  spot: null,
  spotStatus: 'idle',
  taste: null,
  cache: {},
  status: 'loading',
  error: null,
  order: {},
  view: null,
  frontierKey: '',
  frontier: null,
};
let map, marker, cellRect, hoodLayer;

function pathLosses(card, rent, capex) {
  const caps = card.caps, fxs = card.fx, b = card.buffer_months || 6, frac = b / 36;
  return caps.map((a, i) => {
    if (a >= rent) return 0;
    const fx = fxs[i];
    const pi = a - rent - fx * frac;
    if (pi >= 0) return 0;
    const tau = Math.min(36, Math.max(0, -b * fx / pi));
    return capex + rent * tau;
  });
}

function runwayF(card) {
  return 1 - (card.buffer_months || 6) / 36;
}

function affineLosses(card, base, share, capex) {
  const f = runwayF(card), frac = (card.buffer_months || 6) / 36;
  const a = Math.min(Math.max(share, 0), 0.99);
  const buf = card.buffer_months || 6;
  return card.caps.map((cap, i) => {
    const fx = card.fx[i];
    const g = cap + fx * f;
    const R = base + a * g;
    const S = (1 - a) * cap - a * fx * f;
    if (S >= base) return { loss: 0, harvest: 36 * R, S };
    const pi = S - base - fx * frac;
    const tau = pi >= 0 ? 0 : Math.min(36, Math.max(0, -buf * fx / pi));
    return { loss: capex + R * tau, harvest: R * tau, S };
  });
}

function matchBase(card, share, targetH, capex) {
  let lo = -40000, hi = 20000;
  const n = card.caps.length || 1;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    const H = affineLosses(card, mid, share, capex).reduce((s, x) => s + x.harvest, 0) / n;
    if (H < targetH) lo = mid; else hi = mid;
  }
  return (lo + hi) / 2;
}

function expectedShortfall(xs, level) {
  const n = xs.length;
  if (!n) return 0;
  const k = Math.max(1, Math.ceil((1 - Math.min(Math.max(level, 0), 1 - 1 / n)) * n));
  return xs.slice().sort((a, b) => b - a).slice(0, k).reduce((s, x) => s + x, 0) / k;
}

function kellyBankroll(p, value, capex) {
  if (!(capex > 0) || !(value > capex) || !(p > 0)) return null;
  const f = p - (1 - p) / (value / capex - 1);
  return f <= 1e-9 ? null : capex / f;
}

function cdfNorm(x) {
  const ax = Math.abs(x);
  const t = 1 / (1 + 0.2316419 * ax);
  const d = 0.3989422804014327 * Math.exp(-0.5 * x * x);
  const p = d * t * (0.31938153 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  return x >= 0 ? 1 - p : p;
}

function invNorm(p) {
  const a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
    1.383577509590705e+02, -3.066479806614716e+01, 2.506628277459239e+00];
  const b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
    6.680131188771972e+01, -1.328068071512795e+01];
  const c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
    -2.821206333886606e+00, -1.491421237491649e+00, -3.543738060432609e-01];
  const d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00];
  const plow = 0.02425;
  if (p < plow) {
    const q = Math.sqrt(-2 * Math.log(Math.max(p, 1e-12)));
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  if (p > 1 - plow) {
    const q = Math.sqrt(-2 * Math.log(Math.max(1 - p, 1e-12)));
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  const q = p - 0.5, r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
    (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

function vasicekK(pd, rho, q) {
  const p = Math.min(Math.max(pd, 1e-9), 1 - 1e-9);
  const r = Math.min(Math.max(rho, 0), 0.999);
  const z = Math.min(Math.max(q, 1e-9), 1 - 1e-9);
  return cdfNorm((invNorm(p) + Math.sqrt(r) * invNorm(z)) / Math.sqrt(1 - r));
}

function pctile(xs, q) {
  if (!xs.length) return 0;
  const v = xs.slice().sort((a, b) => a - b);
  const k = (v.length - 1) * q;
  const lo = Math.floor(k), hi = Math.ceil(k);
  return v[lo] + (v[hi] - v[lo]) * (k - lo);
}

function mix(xs) {
  const n = xs.length || 1;
  const dead = xs.filter(x => x > 0);
  const el = xs.reduce((s, x) => s + x, 0) / n;
  const ul = Math.sqrt(xs.reduce((s, x) => s + (x - el) ** 2, 0) / n);
  const mu = xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : 0;
  return {
    n: xs.length, nDead: dead.length, pd: dead.length / n, el, ul,
    p50: pctile(xs, 0.5), p90: pctile(xs, 0.9), p99: pctile(xs, 0.99),
    ruined: dead.length ? dead.reduce((s, x) => s + x, 0) / dead.length : 0,
    mu,
  };
}

function book(m, capex, rent, termMonths) {
  /* Same identities as cityscan_breakeven.credit_book (METHOD.md §12). */
  const p = Math.max(0, 1 - m.pd);
  const corpse = rent > 1e-12 ? Math.max(0, (m.el - capex * m.pd) / rent) : 0;
  const committed = capex + rent * termMonths;
  return {
    pd: m.pd, drawn: m.el, committed, guarantee: m.pd * committed,
    ttk: m.pd > 1e-12 ? corpse / m.pd : null,
    hurdle: p > 1e-12 ? m.el / p : null, corpse,
  };
}

function npvLine(belief, hurdle, cur) {
  if (hurdle == null) return 'No hurdle until the curve loads.';
  if (belief == null) {
    return `State what the living shop is worth at year 3. Expected NPV is negative until that beats the hurdle (${money(hurdle, cur)}).`;
  }
  if (belief >= hurdle) {
    return `Stated ${money(belief, cur)} covers the hurdle ${money(hurdle, cur)}. 80% open is not for sale as a rent. The block adds 0.`;
  }
  return `Stated ${money(belief, cur)} is below the hurdle ${money(hurdle, cur)}. Expected NPV is negative. The street will not close the gap.`;
}

async function getJSON(url) {
  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  if (res.ok) return res.json();
  const text = (await res.text()).trim();
  throw new Error(res.status === 429
    ? 'Too many scans from this connection. Try again in a few minutes.'
    : res.status < 500 && text ? text : 'The curve failed on our side. Try again.');
}

async function loadGroup(group) {
  if (state.cache[group]) return state.cache[group];
  const d = await getJSON('/ask?' + new URLSearchParams({ city: CITY, group }));
  state.cache[group] = d;
  state.order[group] = d.caps.map((_, i) => i).sort((i, j) => d.caps[i] - d.caps[j]);
  return d;
}

async function load() {
  state.status = 'loading';
  state.error = null;
  render();
  try {
    await loadGroup(state.group);
    GROUPS.filter(g => g !== state.group).forEach(g => loadGroup(g).then(() => render()).catch(() => {}));
    state.status = 'ready';
  } catch (e) {
    state.error = e instanceof TypeError ? 'Could not reach TasteFit. Check your connection and try again.' : e.message;
    state.status = 'error';
  }
  render();
}

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
  if (state.view) paint();
});
syncThemeLabel();

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function contractOf(card) {
  const rows0 = affineLosses(card, state.asking, 0, state.capex);
  const H0 = rows0.reduce((s, x) => s + x.harvest, 0) / rows0.length;
  const share = state.share;
  const base = share <= 0 ? state.asking : matchBase(card, share, H0, state.capex);
  const rows = share <= 0 ? rows0 : affineLosses(card, base, share, state.capex);
  return { rows0, H0, share, base, rows };
}

function paint() {
  const card = state.cache[state.group];
  const canvas = $('#paths');
  const v = state.view;
  if (!card || !canvas || !v) return;
  const xs = v.rows.map(r => r.loss);
  const m = v.m;
  const order = v.rows.map((_, i) => i).sort((i, j) => v.rows[i].S - v.rows[j].S);
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 640;
  const cssH = 320;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const L = 52, R = 12, T = 18, B = 28;
  const w = cssW - L - R, h = cssH - T - B;
  const yMax = Math.max(m.ruined * 1.08, m.p90 * 1.05, state.capex * 1.05, 1);
  const xAt = i => L + (i / (order.length - 1 || 1)) * w;
  const yAt = val => T + (1 - val / yMax) * h;
  const ink = cssVar('--ink', '#15203B');
  const nogo = cssVar('--nogo', '#B42318');
  const hl = cssVar('--hl', '#FFD84D');
  ctx.clearRect(0, 0, cssW, cssH);
  const barW = Math.max(1, w / order.length);
  const yFloor = T + h;
  order.forEach((idx, rank) => {
    const loss = xs[idx];
    if (loss <= 0) return;
    ctx.fillStyle = nogo;
    ctx.globalAlpha = 0.55;
    ctx.fillRect(xAt(rank), yAt(loss), barW, yFloor - yAt(loss));
  });
  ctx.globalAlpha = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(L, yAt(m.el));
  ctx.lineTo(L + w, yAt(m.el));
  ctx.strokeStyle = hl;
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(L, yAt(m.ruined));
  ctx.lineTo(L + w, yAt(m.ruined));
  ctx.strokeStyle = nogo;
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = ink;
  ctx.font = '600 11px ' + cssVar('--font', 'sans-serif');
  ctx.fillText('weak', L, cssH - 8);
  ctx.textAlign = 'right';
  ctx.fillText('strong', L + w, cssH - 8);
  ctx.fillStyle = nogo;
  ctx.fillText('dead mean ' + money(m.ruined), L + w, Math.max(T + 12, yAt(m.ruined) - 4));
  ctx.textAlign = 'left';
  ctx.fillStyle = ink;
  ctx.fillText('expected ' + money(m.el), L + 4, Math.max(T + 12, yAt(m.el) - 4));
}

function paintFrontier() {
  const canvas = $('#frontier');
  const v = state.view;
  if (!canvas || !v || !v.frontier) return;
  const pts = v.frontier;
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 640;
  const cssH = 160;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const L = 44, R = 12, T = 14, B = 24;
  const w = cssW - L - R, h = cssH - T - B;
  const ink = cssVar('--ink', '#15203B');
  const nogo = cssVar('--nogo', '#B42318');
  const hl = cssVar('--hl', '#FFD84D');
  ctx.clearRect(0, 0, cssW, cssH);
  const xAt = a => L + (a / 0.5) * w;
  const yAt = p => T + (1 - (p - 0.55) / 0.40) * h;
  ctx.strokeStyle = cssVar('--rule', '#DDE2E8');
  ctx.beginPath();
  ctx.moveTo(L, yAt(0.80));
  ctx.lineTo(L + w, yAt(0.80));
  ctx.stroke();
  ctx.beginPath();
  pts.forEach((pt, i) => {
    const x = xAt(pt.share), y = yAt(pt.open);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = ink;
  ctx.lineWidth = 1.8;
  ctx.stroke();
  const here = pts.reduce((best, pt) => Math.abs(pt.share - state.share) < Math.abs(best.share - state.share) ? pt : best, pts[0]);
  ctx.fillStyle = nogo;
  ctx.beginPath();
  ctx.arc(xAt(here.share), yAt(here.open), 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = ink;
  ctx.font = '600 11px ' + cssVar('--font', 'sans-serif');
  ctx.fillText('α = 0', L, cssH - 6);
  ctx.textAlign = 'right';
  ctx.fillText('α = 50%', L + w, cssH - 6);
  ctx.fillStyle = hl === '#FFD84D' ? ink : ink;
  ctx.textAlign = 'left';
  ctx.fillText('80% open', L + 4, Math.max(T + 10, yAt(0.80) - 4));
}

function frontierOf(card, H0) {
  const key = [state.group, state.asking, state.capex].join('|');
  if (state.frontierKey === key && state.frontier) return state.frontier;
  const out = [];
  for (let i = 0; i <= 10; i++) {
    const share = i / 20;
    const base = share <= 0 ? state.asking : matchBase(card, share, H0, state.capex);
    const rows = affineLosses(card, base, share, state.capex);
    const pd = rows.filter(r => r.loss > 0).length / rows.length;
    out.push({ share, base, open: 1 - pd });
  }
  state.frontier = out;
  state.frontierKey = key;
  return out;
}

function rankHTML(cur) {
  const rows = GROUPS.map(g => {
    const card = state.cache[g];
    if (!card || !card.caps) return { g, ready: false };
    const m = mix(pathLosses(card, state.asking, state.capex));
    return { g, ready: true, m, rStar: card.ceilings.city.rent_max, eighty: card.eighty_for_sale };
  }).filter(r => r.ready);
  rows.sort((a, b) => a.m.el - b.m.el);
  if (!rows.length) return '';
  return `<ol class="who" aria-label="Tenant types ranked by expected loss on this rent">
    ${rows.map((r, i) => `<li>
      <button type="button" data-group="${esc(r.g)}" aria-checked="${r.g === state.group}" role="radio">
        <span class="rank-i">${i + 1}</span>
        <span class="rank-who"><b>${esc(GROUP_NAME[r.g])}</b>
          <small>${i === 0 ? 'Least expected loss on this rent' : 'Higher expected loss than the row above'}</small></span>
        <span class="rank-n">${esc(money(r.m.el, cur))} EL
          <small>${esc(in100(r.m.pd))} of 100 closed by year 3</small></span>
      </button>
    </li>`).join('')}
  </ol>`;
}

function sheetHTML(card, v) {
  const cur = card.currency;
  const m = v.m, b = v.b;
  const ttk = b.ttk == null ? '–' : `${b.ttk.toFixed(1)} mo`;
  const live = in100(1 - m.pd);
  const live0 = in100(1 - v.m0.pd);
  const saved = Math.max(0, live - live0);
  const partner = v.base < 0;
  const baseLine = partner
    ? `Landlord co-invests ${money(-v.base, cur)}/mo and takes ${(100 * v.share).toFixed(0)}% of contribution.`
    : v.share <= 0
      ? `This is a rent. Drag the share to hold the landlord’s take and buy survival.`
      : `Base rent ${money(v.base, cur)}/mo plus ${(100 * v.share).toFixed(0)}% of contribution. Same 36-month landlord take as the listing.`;
  const eightyShare = v.alpha80 == null
    ? '80% open is not for sale at this harvest, even as a share.'
    : `80% open is not for sale as a rent. It is for sale as a share at α ≥ ${(100 * v.alpha80).toFixed(0)}%.`;
  const kellyLine = v.kelly == null
    ? (state.belief == null ? 'State a year-3 value to size the Kelly bankroll.' : 'Kelly stake is 0: no growth-optimal bankroll funds this build-out.')
    : `Kelly bankroll ${money(v.kelly, cur)} — the book on which this build-out is the growth-optimal stake.`;
  return `<p class="glance" id="glance" role="status"><b class="glance-n">${esc(live)}</b><span>of 100 still open by year 3${v.share > 0 ? ` · ${esc(saved)} more than as a rent` : ''}.</span></p>
    <p class="coin">${esc(in100(m.pd))} of 100 closed by year 3 have already spent about ${esc(money(m.ruined, cur))}. Expected loss ${esc(money(m.el, cur))} is the mean of those two, not a bill that arrives. Median is ${esc(money(m.p50, cur))}.</p>
    <p class="contract">${esc(baseLine)} ${esc(eightyShare)}</p>
    <div class="actions">
      <button type="button" class="btn btn-ghost" data-action="memo"><svg class="i" aria-hidden="true"><use href="#i-print"/></svg>Print memo</button>
      <button type="button" class="btn btn-ghost" data-action="share"><svg class="i" aria-hidden="true"><use href="#i-link"/></svg>Copy link</button>
    </div>
    <details class="more"><summary>Who, ranked</summary>${rankHTML(cur)}</details>
    <details class="more" id="lab"><summary>The 2,000 paths</summary>
    <canvas id="paths" class="paths" role="img" aria-label="${esc(m.n)} paths ranked weak to strong. Floor is still open. Height is dollars already spent by year 3. Yellow line is the mean, which sits in empty space."></canvas>
    <p class="paths-cap">Each bar is one draw. Ranked by remaining capacity under this contract. Extra closures this rent buys versus free: ${esc(v.extra)} of 100. Versus city-rate rent (${esc(money(v.rStar, cur))}/mo): ${esc(v.extraStar)} of 100. 80% open ${card.eighty_for_sale ? 'is on the market as a rent' : 'is not for sale as a rent'}.</p>
    <canvas id="frontier" class="frontier" role="img" aria-label="P(open) against share α, holding the landlord 36-month harvest fixed. Horizontal line is 80% open."></canvas>
    <p class="paths-cap">Harvest-matched frontier. The street is not an axis. α raises P(open) because the floor b falls. The 80% line is the joint-venture region.</p>
    <p class="npv">${esc(npvLine(state.belief, b.hurdle, cur))} ${esc(kellyLine)}</p>
    </details>
    <details class="more"><summary>The book</summary>
    <table class="bda">
      <tr><th>Expected loss (the mean)</th><td class="num">${esc(money(m.el, cur))}</td></tr>
      <tr><th>Surety capital ES 95%</th><td class="num">${esc(money(v.es95, cur))}</td></tr>
      <tr><th>Unexpected loss (SD of the 2,000)</th><td class="num">${esc(money(m.ul, cur))}</td></tr>
      <tr><th>Median path</th><td class="num">${esc(money(m.p50, cur))}</td></tr>
      <tr><th>P90 path</th><td class="num">${esc(money(m.p90, cur))}</td></tr>
      <tr><th>Mean among the closed</th><td class="num">${esc(money(m.ruined, cur))}</td></tr>
      <tr><th>Closed even at $0 rent</th><td class="num">${esc(v.cityDead)} of 100</td></tr>
      <tr><th>Distance to default</th><td class="num">${v.dd == null ? '–' : esc(v.dd.toFixed(2))}</td></tr>
      <tr><th>Guarantee EL (${esc(state.termYears)} y)</th><td class="num">${esc(money(b.guarantee, cur))}</td></tr>
      <tr><th>Time to know</th><td class="num">${esc(ttk)}</td></tr>
      <tr><th>Hurdle equity at 36 mo</th><td class="num">${b.hurdle == null ? '–' : esc(money(b.hurdle, cur))}</td></tr>
      <tr><th>Kelly bankroll</th><td class="num">${v.kelly == null ? '–' : esc(money(v.kelly, cur))}</td></tr>
      <tr><th>Book of 10, Vasicek 99.9%</th><td class="num">${esc(money(v.book10, cur))}</td></tr>
      <tr><th>City-rate rent R*</th><td class="num">${esc(money(v.rStar, cur))}/mo</td></tr>
      <tr><th>Matched base b*(α)</th><td class="num">${esc(money(v.base, cur))}/mo</td></tr>
      <tr><th>Landlord 36-month take</th><td class="num">${esc(money(v.H0, cur))}</td></tr>
    </table></details>`;
}

function placeHTML(spot) {
  if (!spot || spot.data_thin) return '';
  const pl = spot.place;
  const hood = (pl && pl.neighborhood) || spot.city || 'Chicago';
  const comps = ((pl && pl.competitors) || []).map(c =>
    `<li><b>${esc(c.name)}</b><span>${esc(c.m)} m</span></li>`).join('');
  const reasons = ((pl && pl.reasons) || []).map(r => `<li>${esc(r)}</li>`).join('');
  const suit = pl && pl.suitability != null ? `${Math.round(100 * pl.suitability)}` : null;
  const m = spot.market_share;
  return `<p class="hood"><b>${esc(hood)}</b>${suit ? `<small>OSM fit ${esc(suit)} · map demand, not survival</small>` : ''}</p>
    ${comps ? `<ul class="chips">${comps}</ul>` : ''}
    ${reasons ? `<ul class="why">${reasons}</ul>` : ''}
    ${pl && pl.huff ? `<p class="acs-src">${esc(pl.huff)}</p>` : ''}
    ${m && m.note ? `<p class="acs-src">${esc(m.note)}</p>` : ''}`;
}

function censusHTML(c, status) {
  if (status === 'loading') return `<p class="census-miss">Reading the tract…</p>`;
  if (!c) return `<p class="census-miss">Census tract did not load. Licences still rule.</p>`;
  const pov = c.poverty_rate == null ? '–' : `${(100 * c.poverty_rate).toFixed(0)}%`;
  const hh = (c.owner_hh != null && c.renter_hh != null)
    ? `${Number(c.renter_hh).toLocaleString('en')} renter / ${Number(c.owner_hh).toLocaleString('en')} owner`
    : null;
  const rentNote = c.median_gross_rent != null
    ? `Households here pay ${money(c.median_gross_rent)}/mo in gross rent. The listing above is commercial rent — a different market.`
    : '';
  return `<dl class="acs">
    <div><dt>Median household income</dt><dd>${esc(money(c.median_income))}</dd></div>
    <div><dt>Median gross rent</dt><dd>${c.median_gross_rent == null ? '–' : `${esc(money(c.median_gross_rent))}/mo`}</dd></div>
    <div><dt>Population</dt><dd>${c.population == null ? '–' : esc(Number(c.population).toLocaleString('en'))}</dd></div>
    <div><dt>Median age</dt><dd>${c.median_age == null ? '–' : esc(String(c.median_age))}</dd></div>
    <div><dt>Poverty rate</dt><dd>${esc(pov)}</dd></div>
    <div><dt>Households</dt><dd>${hh ? esc(hh) : '–'}</dd></div>
  </dl><p class="acs-src">${esc(c.name || '')}. ${esc(c.vintage || '')}. Tract aggregates, not a person. ${esc(rentNote)}</p>`;
}

function aff(p) {
  const n = Number(p && p.affinity);
  return Number.isFinite(n) ? `${Math.round(100 * n)}` : '—';
}

function isQlooPlace(p) {
  const n = String((p && p.name) || '').trim();
  return !!(n && !n.startsWith('urn:') && !/^qloo(\s+native\s+resolution)?$/i.test(n));
}

function tasteHTML(t) {
  if (!t || t.status !== 'ok') {
    return `<p class="census-miss">Qloo is waiting on a key. Licences still answer survival.</p>`;
  }
  const loves = (t.loves || []).filter(isQlooPlace).map(p => `<li><b>${esc(p.name)}</b><span>${esc(aff(p))}</span></li>`).join('');
  const places = (t.places || []).filter(isQlooPlace).map(p => `<li><b>${esc(p.name)}</b><span>${esc(aff(p))}</span></li>`).join('');
  if (!loves && !places) {
    return `<p class="census-miss">Qloo returned no places for ${esc(t.location || 'this pin')}.</p>`;
  }
  return `<p class="hood"><b>What this street loves</b><small>Qloo taste graph · not survival</small></p>
    ${loves ? `<ul class="exhibit">${loves}</ul>` : ''}
    ${places ? `<p class="hood"><b>Places like ${esc(t.concept)}</b></p><ul class="exhibit">${places}</ul>` : ''}
    <p class="acs-src">Affinity in ${esc(t.location)}. Survival is the licence coin above. Qloo answers who belongs here.</p>
    <button type="button" class="btn btn-ink" data-action="agent">Run the Qloo agent on this pin</button>`;
}

function renderExhibits() {
  const placeEl = $('#place');
  if (placeEl) placeEl.innerHTML = placeHTML(state.spot);
  const censusEl = $('#census');
  if (censusEl) censusEl.innerHTML = censusHTML(state.spot && state.spot.census, state.spotStatus);
  const tasteEl = $('#taste');
  if (tasteEl) tasteEl.innerHTML = tasteHTML(state.taste);
  const conceptEl = $('#concept');
  if (conceptEl && document.activeElement !== conceptEl) conceptEl.value = state.concept;
}

async function dropPin(lat, lon) {
  state.lat = lat;
  state.lon = lon;
  if (marker) marker.setLatLng([lat, lon]);
  state.spotStatus = 'loading';
  renderExhibits();
  try {
    state.spot = await getJSON('/spot?' + new URLSearchParams({ city: CITY, lat: String(lat), lon: String(lon) }));
    state.spotStatus = 'ready';
    paintCell();
  } catch (e) {
    toast(e.message);
    state.spotStatus = 'error';
  }
  renderExhibits();
  const loc = (state.spot && state.spot.place && state.spot.place.neighborhood)
    ? `${state.spot.place.neighborhood}, Chicago` : 'Chicago';
  try {
    state.taste = await getJSON('/taste?' + new URLSearchParams({ concept: state.concept, location: loc }));
  } catch (e) {
    state.taste = { status: 'offline', places: [] };
  }
  renderExhibits();
}

function paintCell() {
  if (!map || typeof L === 'undefined' || !state.spot || !state.spot.bounds) return;
  const b = state.spot.bounds;
  if (cellRect) map.removeLayer(cellRect);
  cellRect = L.rectangle([[b[0], b[1]], [b[2], b[3]]], {
    color: '#B08D4F', weight: 1.5, fillColor: '#B08D4F', fillOpacity: 0.1,
  }).addTo(map);
  if (hoodLayer) map.removeLayer(hoodLayer);
  hoodLayer = L.layerGroup();
  (state.spot.nearby || []).forEach(n => {
    const c = n.centroid;
    if (!c) return;
    L.circleMarker([c.lat, c.lon], {
      radius: 5, color: '#15203B', weight: 1, fillColor: '#FFD84D', fillOpacity: 0.85,
    }).bindTooltip(n.neighborhood || n.concept || '', { direction: 'top', opacity: 0.92 }).addTo(hoodLayer);
  });
  hoodLayer.addTo(map);
}

function pinIcon() {
  return L.divIcon({ className: 'pin', iconSize: [18, 18], iconAnchor: [9, 9] });
}

function bootMap() {
  const el = $('#map');
  if (el && typeof L !== 'undefined') {
    map = L.map(el, { zoomControl: false, scrollWheelZoom: true, attributionControl: true })
      .setView([state.lat, state.lon], 14);
    L.control.zoom({ position: 'bottomleft' }).addTo(map);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    marker = L.marker([state.lat, state.lon], { title: 'Unit', icon: pinIcon(), keyboard: true }).addTo(map);
    map.on('click', e => {
      dropPin(e.latlng.lat, e.latlng.lng);
      history.replaceState(null, '', '#' + shareQuery());
    });
    setTimeout(() => map.invalidateSize(), 80);
  }
  load();
  dropPin(state.lat, state.lon);
}

function render() {
  renderExhibits();
  const card = state.cache[state.group];
  if (card && !state.capexTouched && !state.usedDefaultCapex && card.capex_default) {
    state.capex = card.capex_default;
    state.usedDefaultCapex = true;
  }
  const askingEl = $('#asking');
  const capexEl = $('#capex');
  const termEl = $('#term');
  if (askingEl && document.activeElement !== askingEl) askingEl.value = String(state.asking);
  if (capexEl && document.activeElement !== capexEl) capexEl.value = String(state.capex);
  if (termEl && document.activeElement !== termEl) termEl.value = String(state.termYears);
  const beliefEl = $('#belief');
  if (beliefEl && document.activeElement !== beliefEl) beliefEl.value = state.belief == null ? '' : String(state.belief);
  const range = $('#rent-range');
  if (range && document.activeElement !== range) range.value = String(Math.min(+range.max || 8000, state.asking));
  const shareEl = $('#share');
  if (shareEl && document.activeElement !== shareEl) shareEl.value = String(Math.round(100 * state.share));
  const shareVal = $('#share-val');
  if (shareVal) {
    shareVal.textContent = state.share <= 0 ? '0% · a rent' : `${Math.round(100 * state.share)}% of contribution`;
  }
  $$('.seg [data-group]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.group === state.group)));

  let stamp, tail = '';
  state.view = null;
  if (state.status === 'loading') {
    stamp = `<div class="stamp-skel" aria-hidden="true"><div class="sk" style="width:40%"></div><div class="sk" style="width:70%"></div></div>`;
  } else if (state.status === 'error') {
    stamp = `<div class="state state-error" role="alert"><h3>The curve did not load</h3><p>${esc(state.error)}</p></div>`;
  } else {
    const c = contractOf(card);
    const xs = c.rows.map(r => r.loss);
    const m = mix(xs);
    const m0 = mix(c.rows0.map(r => r.loss));
    const b = book(m, state.capex, state.asking, state.termYears * 12);
    const free = mix(pathLosses(card, 0, state.capex));
    const rStar = card.ceilings.city.rent_max;
    const atStar = mix(pathLosses(card, Math.max(0, rStar), state.capex));
    const extra = Math.max(0, Math.round(100 * (m0.nDead - free.nDead) / m0.n));
    const extraStar = Math.max(0, Math.round(100 * (m0.nDead - atStar.nDead) / m0.n));
    const cityDead = Math.round(100 * free.nDead / free.n);
    const es95 = expectedShortfall(xs, 0.95);
    const mu = card.caps.reduce((s, x) => s + x, 0) / card.caps.length;
    const sd = Math.sqrt(card.caps.reduce((s, x) => s + (x - mu) ** 2, 0) / (card.caps.length - 1 || 1));
    const dd = sd > 0 ? (mu - state.asking) / sd : null;
    const kelly = state.belief == null ? null : kellyBankroll(1 - m.pd, state.belief, state.capex);
    const k999 = vasicekK(m.pd, ASSET_RHO, 0.999);
    const book10 = 10 * (m.ruined || state.capex) * k999;
    const frontier = frontierOf(card, c.H0);
    const hit = frontier.find(pt => pt.open >= 0.80);
    const v = {
      rows: c.rows, m, m0, b, extra, extraStar, cityDead, rStar,
      share: c.share, base: c.base, H0: c.H0, es95, dd, kelly, book10,
      frontier, alpha80: hit ? hit.share : null,
    };
    state.view = v;
    stamp = sheetHTML(card, v);
    tail = `
      <section class="limits-note" aria-labelledby="lim-title">
        <h2 id="lim-title">Limits</h2>
        <p>${esc(card.source)}, n = ${esc(card.n)}. ${esc(card.label_en)}. ${esc(card.note)} Affine lease R = b + αG, G = contribution. α = 0 is the rent. b*(α) holds the landlord’s 36-month harvest. ES 95% is the mean of the worst 5% of path losses (coherent; P90 is not). Vasicek 99.9% on 10 identical units uses stated asset correlation ρ = 0.12, not estimated. Kelly uses the stated year-3 value. LGD = 1. A model, not a forecast.</p>
      </section>`;
  }
  $('#stamp').innerHTML = stamp;
  const diagram = $('#diagram');
  if (diagram) diagram.innerHTML = '';
  $('#tail').innerHTML = tail;
  paint();
  paintFrontier();
}

function readMoney(id, errId, apply) {
  const el = $('#' + id);
  if (!el) return;
  const raw = el.value.replace(/[\s,$]/g, '');
  const n = +raw;
  const err = $('#' + errId);
  if (raw && !(n > 0 && n < 1e8)) {
    el.setAttribute('aria-invalid', 'true');
    if (err) err.hidden = false;
    return;
  }
  el.removeAttribute('aria-invalid');
  if (err) err.hidden = true;
  if (n > 0) apply(n);
  render();
  history.replaceState(null, '', '#' + shareQuery());
}

function readAsking() {
  readMoney('asking', 'asking-err', n => { state.asking = n; });
}
function readCapex() {
  readMoney('capex', 'capex-err', n => { state.capex = n; state.capexTouched = true; });
}
function readTerm() {
  const el = $('#term');
  if (!el) return;
  const n = +el.value.replace(/[\s,]/g, '');
  const err = $('#term-err');
  if (!(n >= 1 && n <= 20 && n === Math.round(n))) {
    el.setAttribute('aria-invalid', 'true');
    if (err) err.hidden = false;
    return;
  }
  el.removeAttribute('aria-invalid');
  if (err) err.hidden = true;
  state.termYears = n;
  render();
  history.replaceState(null, '', '#' + shareQuery());
}
function readBelief() {
  const el = $('#belief');
  if (!el) return;
  const raw = el.value.replace(/[\s,$]/g, '');
  const err = $('#belief-err');
  if (!raw) {
    el.removeAttribute('aria-invalid');
    if (err) err.hidden = true;
    state.belief = null;
    render();
    history.replaceState(null, '', '#' + shareQuery());
    return;
  }
  const n = +raw;
  if (!(n >= 0 && n < 1e8)) {
    el.setAttribute('aria-invalid', 'true');
    if (err) err.hidden = false;
    return;
  }
  el.removeAttribute('aria-invalid');
  if (err) err.hidden = true;
  state.belief = n;
  render();
  history.replaceState(null, '', '#' + shareQuery());
}

function memoCall(live, v) {
  if (!v) return { tag: 'HOLD', kicker: 'The coin is still loading for this pin.', tone: 'hold' };
  const a = v.alpha80 == null ? null : Math.round(100 * v.alpha80);
  if (v.share > 0 && live >= 80) {
    return { tag: 'JOINT VENTURE', tone: 'go',
      kicker: `${live} of 100 stay open because you sold a share, not because the street is lucky. The landlord’s 36-month take is unchanged.` };
  }
  if (v.share > 0 && a != null && live < 80) {
    return { tag: 'SHARE FURTHER', tone: 'adjust',
      kicker: `This share still leaves ${live} of 100 open. 80% open is for sale at α ≥ ${a}%, holding the same harvest.` };
  }
  if (live >= 70) {
    return { tag: 'OPEN TILT', tone: 'go',
      kicker: `${live} of 100 still have a shop by year 3 at this rent. That is rare. The street did not buy it. The rent did.` };
  }
  if (live >= 50) {
    return { tag: 'A COIN YOU CAN SEE', tone: 'adjust',
      kicker: a == null
        ? `${live} of 100 stay open. Price it as a coin, not as a vibe.`
        : `${live} of 100 stay open as a rent. 80% open is not for sale as a cheaper pin. It is for sale as a share at α ≥ ${a}%.` };
  }
  return { tag: 'HARD COIN', tone: 'nogo',
    kicker: `${live} of 100 stay open. Most paths have already spent the build-out. Cut the rent, sell a share, or walk.` };
}

function censusRead(c, asking, cur) {
  if (!c) return 'Census tract did not load. Licences still answer survival.';
  const bits = [];
  if (c.median_income != null) {
    bits.push(c.median_income >= 120000
      ? `Households here report ${money(c.median_income)} median income — high for a Chicago tract. A full-service cheque has room, if the licences agree.`
      : c.median_income < 50000
        ? `Households here report ${money(c.median_income)} median income — thin for a full-service cheque. Price the plate, not the postcode.`
        : `Households here report ${money(c.median_income)} median income — a middle Chicago tract, not a story.`);
  }
  if (c.median_gross_rent != null) {
    const ratio = asking && c.median_gross_rent > 0 ? (asking / c.median_gross_rent) : null;
    bits.push(ratio != null
      ? `They pay ${money(c.median_gross_rent)}/mo in household rent. Your listing is ${money(asking, cur)}/mo commercial — ${ratio.toFixed(1)}× their home rent, and a different market.`
      : `They pay ${money(c.median_gross_rent)}/mo in household rent. Your listing is ${money(asking, cur)}/mo commercial — a different market.`);
  }
  if (c.poverty_rate != null) {
    const p = 100 * c.poverty_rate;
    bits.push(p >= 25
      ? `Poverty rate ${p.toFixed(0)}% — high. Do not read this as a licence forecast.`
      : `Poverty rate ${p.toFixed(0)}%.`);
  }
  if (c.median_age != null) {
    bits.push(c.median_age < 32
      ? `Median age ${c.median_age} — a younger tract.`
      : c.median_age > 42
        ? `Median age ${c.median_age} — older than the city centre.`
        : `Median age ${c.median_age}.`);
  }
  if (c.renter_hh != null && c.owner_hh != null) {
    const tot = c.renter_hh + c.owner_hh;
    const rp = tot > 0 ? Math.round(100 * c.renter_hh / tot) : null;
    bits.push(`${Number(c.renter_hh).toLocaleString('en')} renter households, ${Number(c.owner_hh).toLocaleString('en')} owner${rp != null ? ` (${rp}% renter)` : ''}.`);
  }
  if (c.population != null) bits.push(`${Number(c.population).toLocaleString('en')} people in the tract.`);
  bits.push('These are ACS tract aggregates, not a person, and not a survival forecast.');
  return bits.join(' ');
}

function qlooRows(list) {
  return (list || []).filter(isQlooPlace).slice(0, 8).map(p =>
    `<tr><td>${esc(p.name)}</td><td>${esc(aff(p))}</td></tr>`).join('');
}

function csvNames(list, n) {
  return (list || []).filter(isQlooPlace).slice(0, n || 5).map(p => p && p.name).filter(Boolean);
}

function qlooRead(t, concept, hood) {
  if (!t || t.status !== 'ok') {
    return {
      head: 'Qloo did not return a taste exhibit for this pin. Survival still comes from Chicago licences.',
      fit: 'When a key is set, only the concept and the place name go to the graph. Nothing personal.',
    };
  }
  const loc = t.location || hood;
  const loves = csvNames(t.loves, 5);
  const places = csvNames(t.places, 5);
  const tag = t.tag ? String(t.tag) : '';
  let head = `Only the concept “${concept}” and the place “${loc}” went to Qloo. Nothing personal. Affinity is who belongs here, not who survives.`;
  if (tag) head += ` find_tags matched the concept to ${tag}.`;
  let fit;
  if (loves.length && places.length) {
    fit = `This block already over-indexes on ${loves.join(', ')}. Places Qloo ranks as kin to “${concept}”: ${places.join(', ')}. Read the names. If they feel like your room, the street already has the taste. If they do not, you are asking the block to learn you.`;
  } else if (loves.length) {
    fit = `This block already over-indexes on ${loves.join(', ')}. That is the taste of the street. Qloo did not return kin places for “${concept}” on this pin.`;
  } else if (places.length) {
    fit = `Places Qloo ranks as kin to “${concept}”: ${places.join(', ')}. The area-taste call did not return named loves for ${loc}.`;
  } else {
    fit = `Qloo returned no named places for ${loc}. The licences still answer survival.`;
  }
  return { head, fit };
}

function voices(call, v, live, who, cur, concept, hood) {
  const a = v && v.alpha80 != null ? Math.round(100 * v.alpha80) : null;
  const landlord = !v ? 'The lease has not loaded.'
    : call.tag === 'JOINT VENTURE'
      ? `Your 36-month take stays ${money(v.H0, cur)}. You co-invest ${money(-v.base, cur)}/mo and take ${(100 * v.share).toFixed(0)}% of contribution. Print this.`
      : call.tag === 'SHARE FURTHER'
        ? `Same harvest ${money(v.H0, cur)}. This share still leaves ${live} of 100 open. 80% open is for sale at α ≥ ${a}%.`
        : call.tag === 'OPEN TILT'
          ? `${live} of 100 ${who} still open at ${money(state.asking, cur)}/mo. Rare. Do not mark ${hood} as a vibe.`
          : call.tag === 'HARD COIN'
            ? `Most paths have already spent the build-out. Cut the rent, sell a share, or walk. Harvest today is ${money(v.H0, cur)}.`
            : a == null
              ? `${live} of 100 stay open. Price ${hood} as a coin. 80% open is not for sale at this harvest.`
              : `${live} of 100 stay open as a rent. 80% open is a share at α ≥ ${a}%, not a cheaper pin.`;
  const founder = !v ? 'Wait for the coin.'
    : `${live} of 100 ${who} still have a door by year 3 if “${concept}” opens at ${money(state.asking, cur)}/mo. ${v.cityDead} of 100 close even at $0 rent — the street cannot buy those back. This rent buys ${v.extra} extra closures versus free.`;
  return { landlord, founder };
}

function nextMove(call, v) {
  const a = v && v.alpha80 != null ? Math.round(100 * v.alpha80) : null;
  if (!v) return 'Wait until the 2,000 paths load, then print again.';
  if (call.tag === 'JOINT VENTURE') return 'Take this sheet to the landlord. The harvest is the same. The coin is not.';
  if (call.tag === 'SHARE FURTHER') {
    return a == null
      ? 'Drag the share and reprint. 80% open is not for sale at this harvest, even as a share.'
      : `Drag the share to ${a}% or more, hold the harvest, and reprint.`;
  }
  if (call.tag === 'OPEN TILT') return 'Do not spend this coin on a story. Walk the block. The pin is a scout, not a vetted address.';
  if (call.tag === 'HARD COIN') return 'Cut the rent, sell a share, or walk. Reprint after each change — this sheet is this pin, this rent, this concept.';
  return a == null
    ? 'Price it. 80% open is not for sale as a cheaper pin at this harvest.'
    : `Price it, or buy 80% open as a share at α ≥ ${a}%.`;
}

function bookRows(v, live, cur) {
  if (!v) return [];
  const ddEn = v.dd == null ? 'Capacity versus rent did not resolve.'
    : v.dd >= 2 ? `Mean licence capacity sits ${v.dd.toFixed(2)} SDs above this rent. Room.`
      : v.dd >= 0.5 ? `Mean capacity is only ${v.dd.toFixed(2)} SDs above this rent. Tight.`
        : v.dd >= 0 ? `Mean capacity is sitting on this rent (DD ${v.dd.toFixed(2)}).`
          : `Mean capacity is already below this rent (DD ${v.dd.toFixed(2)}).`;
  const kellyEn = state.belief == null
    ? 'State a year-3 value to size the bankroll. Kelly is silent until then.'
    : v.kelly == null || v.kelly <= 0
      ? 'The coin does not pay enough versus the build-out to size a bankroll on the stated value.'
      : `At most ${money(v.kelly, cur)} of bankroll on this coin, using the stated year-3 value.`;
  return [
    ['Still open (of 100)', `${live} shops in 100 still have a door at year 3.`, String(live)],
    ['Expected loss (mean of 2,000 paths)', `The average of the rolls, not a bill that arrives. Most rolls lose $0.`, money(v.m.el, cur)],
    ['Surety capital ES 95%', `If you land in the worst 5% of rolls, their average is this. Credit desks hold ES 95%, not P90 — P90 is not coherent.`, money(v.es95, cur)],
    ['Unexpected loss (SD)', `How spread the 2,000 rolls are around the mean.`, money(v.m.ul, cur)],
    ['Median path', `Half the rolls lose this or less. Often $0 because most shops live.`, money(v.m.p50, cur)],
    ['P90 path', `Nine in ten rolls lose this or less. Not a capital number.`, money(v.m.p90, cur)],
    ['Mean among the closed', `When a shop dies, this is the average spend of the corpse.`, money(v.m.ruined, cur)],
    ['Closed even at $0 rent', `${v.cityDead} of 100 close even if the unit is free. The street cannot buy those back.`, `${v.cityDead} of 100`],
    ['Extra closures this rent buys vs free', `This listing, versus $0 rent, buys ${v.extra} extra closures in 100.`, `${v.extra} of 100`],
    ['Vs city-rate rent R*', `Versus the city-rate ceiling R* ${money(v.rStar, cur)}/mo, extra closures: ${v.extraStar} of 100.`, `${v.extraStar} of 100 · R* ${money(v.rStar, cur)}/mo`],
    ['Distance to default', ddEn, v.dd != null ? v.dd.toFixed(2) : '—'],
    [`Guarantee EL (${state.termYears} y)`, `Probability of death × committed capital (build-out + rent for the term).`, money(v.b.guarantee, cur)],
    ['Time to know', v.b.ttk != null ? `About ${v.b.ttk.toFixed(1)} months of trading until the path has spoken.` : 'Not resolved on this pin.', v.b.ttk != null ? `${v.b.ttk.toFixed(1)} mo` : '—'],
    ['Hurdle equity at 36 mo', `The living shop has to be worth this at year 3 before expected NPV turns positive.`, v.b.hurdle != null ? money(v.b.hurdle, cur) : '—'],
    ['Kelly bankroll', kellyEn, v.kelly != null ? money(v.kelly, cur) : (state.belief == null ? 'State a year-3 value' : '0')],
    ['Book of 10, Vasicek 99.9%', `Ten identical units, stated asset correlation ρ = ${ASSET_RHO}, not estimated. LGD = 1.`, money(v.book10, cur)],
  ];
}

function memoHTML() {
  const card = state.cache[state.group];
  const v = state.view;
  const spot = state.spot;
  const c = spot && spot.census;
  const pl = spot && spot.place;
  const share = spot && spot.market_share;
  const hood = (pl && pl.neighborhood) || 'Chicago';
  const live = v ? in100(1 - v.m.pd) : '–';
  const liveN = typeof live === 'number' ? live : 0;
  const cur = (card && card.currency) || 'USD';
  const who = GROUP_NAME[state.group] || state.group;
  const call = memoCall(liveN, v);
  const say = voices(call, v, liveN, who, cur, state.concept, hood);
  const t = state.taste;
  const q = qlooRead(t, state.concept, hood);
  const loves = t && t.status === 'ok' ? (t.loves || []) : [];
  const places = t && t.status === 'ok' ? (t.places || []) : [];
  const comps = ((pl && pl.competitors) || []).map(x => `${x.name} (${x.m} m)`).join('; ') || '—';
  const reasons = ((pl && pl.reasons) || []).join('; ');
  const eighty = !v ? '' : v.alpha80 == null
    ? '80% open is not for sale at this harvest, even as a share.'
    : `80% open is not for sale as a rent. It is for sale as a share at α ≥ ${(100 * v.alpha80).toFixed(0)}%.`;
  const partner = v && v.base < 0;
  const contractLine = !v ? 'The lease has not loaded.'
    : partner
      ? `This is a joint venture. The landlord co-invests ${money(-v.base, cur)}/mo and takes ${(100 * v.share).toFixed(0)}% of contribution. Formula: R = b + αG, with G = A + fx·f.`
      : v.share <= 0
        ? `This is a flat rent of ${money(state.asking, cur)}/mo (α = 0). Drag the share to hold the landlord’s 36-month take and buy survival.`
        : `Affine lease R = b + αG. Base b*(α) = ${money(v.base, cur)}/mo plus ${(100 * v.share).toFixed(0)}% of contribution. Same 36-month landlord take as the listing (${money(v.H0, cur)}).`;
  const ranks = GROUPS.map(g => {
    const cd = state.cache[g];
    if (!cd || !cd.caps) return null;
    const m = mix(pathLosses(cd, state.asking, state.capex));
    return { g, el: m.el, pd: m.pd };
  }).filter(Boolean).sort((a, b) => a.el - b.el);
  const when = new Date().toISOString().slice(0, 10);
  const lines = bookRows(v, live, cur);
  const nLic = card && card.n != null ? card.n : '5,692';
  const alphaPct = Math.round(100 * state.share);
  const plugged = v
    ? `On this pin: R = ${money(state.asking, cur)}/mo, α = ${alphaPct}%, b*(α) = ${money(v.base, cur)}/mo, H₀ = ${money(v.H0, cur)}. G<sub>k</sub> = A<sub>k</sub> + fx·f. ES<sub>0.95</sub> = ${money(v.es95, cur)}. Merton DD = (μ<sub>capacity</sub> − R) / σ = ${v.dd != null ? esc(v.dd.toFixed(2)) : '—'}. Vasicek book of 10 at ρ = ${ASSET_RHO}: ${money(v.book10, cur)}.`
    : 'The curve has not loaded, so the identities have nothing to plug.';
  return `<header class="memo-top">
      <div class="brand"><b>TasteFit</b><div class="memo-stamp">A model, not a forecast</div></div>
      <p class="meta">${esc(when)}<br>${esc(hood)} · ${esc(state.lat.toFixed(4))}, ${esc(state.lon.toFixed(4))}<br>Not a vetted address<br><a href="mailto:${esc(CONTACT)}">${esc(CONTACT)}</a></p>
    </header>
    <p class="memo-kicker memo-kicker-${esc(call.tone)}">${esc(call.tag)}</p>
    <h1 id="memo-title">Who should rent this unit?</h1>
    <p class="memo-lead">${esc(call.kicker)}</p>
    <p class="memo-hero"><b>${esc(live)}</b><span> of 100 ${esc(who)} still open by year 3 at ${esc(money(state.asking, cur))}/mo · ${esc(state.concept)}</span></p>
    <p>${esc(eighty)}</p>

    <div class="memo-call">
      <p><b>To the landlord.</b> ${esc(say.landlord)}</p>
      <p><b>To the founder.</b> ${esc(say.founder)}</p>
      <p><b>Next.</b> ${esc(nextMove(call, v))}</p>
    </div>

    <section class="memo-section">
    <h2>How to read this (plain)</h2>
    <ul class="memo-plain">
      <li>We roll 2,000 versions of this lease on Chicago licences. The big number is how many still have a shop.</li>
      <li>Expected loss is the average of those rolls — not a bill that arrives. Median is often $0 because most shops live.</li>
      <li>Qloo says what this street loves. Licences say who dies. Census says who lives here. None of the three is a vetted address.</li>
    </ul>
    </section>

    <section class="memo-section">
    <h2>This sheet’s inputs</h2>
    <p class="memo-plain">Every number below is this pin, this rent, this concept. Change any input and reprint. The sheet is not a city average.</p>
    <table class="memo-table">
      <tr><th>Concept</th><td>${esc(state.concept)} · ${esc(who)}</td></tr>
      <tr><th>Pin</th><td>${esc(hood)} · ${esc(state.lat.toFixed(4))}, ${esc(state.lon.toFixed(4))} · not a vetted address</td></tr>
      <tr><th>Asking / share / build-out / term</th><td>${esc(money(state.asking, cur))}/mo · ${esc(String(alphaPct))}% · ${esc(money(state.capex, cur))} · ${esc(String(state.termYears))} y</td></tr>
      <tr><th>Stated year-3 value</th><td>${state.belief == null ? 'Not stated — Kelly and expected NPV stay silent' : esc(money(state.belief, cur))}</td></tr>
      <tr><th>Census tract / GEOID</th><td>${c ? esc((c.name || '—') + (c.geoid ? ' · ' + c.geoid : '')) : 'Tract did not load'}</td></tr>
      <tr><th>Qloo location / tag</th><td>${t && t.status === 'ok' ? esc((t.location || hood) + (t.tag ? ' · ' + t.tag : ' · find_tags unmatched')) : 'No Qloo exhibit on this request'}</td></tr>
      <tr><th>Licences / seed</th><td>n = ${esc(nLic)} · seed 20261011 · κ = 0 · HR_loc = 1</td></tr>
    </table>
    </section>

    <section class="memo-section">
    <h2>Who lives here</h2>
    <p class="memo-plain">${esc(censusRead(c, state.asking, cur))}</p>
    <table class="memo-table">
      <tr><th>Census tract</th><td>${c ? esc(c.name || c.geoid || '—') : '—'}</td></tr>
      <tr><th>GEOID</th><td>${c && c.geoid ? esc(c.geoid) : '—'}</td></tr>
      <tr><th>Median household income</th><td>${c ? esc(money(c.median_income)) : '—'}</td></tr>
      <tr><th>Median gross rent (household)</th><td>${c && c.median_gross_rent != null ? `${esc(money(c.median_gross_rent))}/mo` : '—'}</td></tr>
      <tr><th>Population</th><td>${c && c.population != null ? esc(Number(c.population).toLocaleString('en')) : '—'}</td></tr>
      <tr><th>Median age</th><td>${c && c.median_age != null ? esc(String(c.median_age)) : '—'}</td></tr>
      <tr><th>Poverty rate</th><td>${c && c.poverty_rate != null ? esc(`${(100 * c.poverty_rate).toFixed(0)}%`) : '—'}</td></tr>
      <tr><th>Households</th><td>${c && c.renter_hh != null && c.owner_hh != null ? esc(`${Number(c.renter_hh).toLocaleString('en')} renter / ${Number(c.owner_hh).toLocaleString('en')} owner`) : '—'}</td></tr>
      <tr><th>Vintage</th><td>${c ? esc(c.vintage || 'ACS 5-year via Census Reporter') : '—'}</td></tr>
    </table>
    <p class="memo-expert">U.S. Census Bureau ACS 5-year via Census Reporter (B19013, B25064, B01003, B01002, B17001, B25003). Tract aggregates, never a person. Income and household rent are not a licence forecast.</p>
    </section>

    <section class="memo-section">
    <h2>Who this street loves</h2>
    <p class="memo-plain">${esc(q.head)}</p>
    <p class="memo-plain">${esc(q.fit)}</p>
    ${loves.length ? `<table class="memo-table"><thead><tr><th>Qloo · what the area over-indexes on</th><th>Affinity</th></tr></thead><tbody>${qlooRows(loves)}</tbody></table>` : ''}
    ${places.length ? `<table class="memo-table"><thead><tr><th>Qloo · places like ${esc(state.concept)}</th><th>Affinity</th></tr></thead><tbody>${qlooRows(places)}</tbody></table>` : ''}
    <p class="memo-expert">Qloo Taste AI graph. find_tags → recommend (signal_tags) / area_taste. Affinity scores are not comparable across separate rank calls, so this sheet never mixes them. Qloo is taste, not survival.</p>
    </section>

    <section class="memo-section">
    <h2>Who is already here</h2>
    <p>${esc(comps)}. OSM demand, not survival.${reasons ? ` ${esc(reasons)}.` : ''} ${pl && pl.huff ? esc(pl.huff) : ''} ${pl && pl.suitability != null ? `Map fit ${Math.round(100 * pl.suitability)}.` : ''} ${share && share.note ? esc(share.note) : ''}</p>
    <p class="memo-expert">OpenStreetMap contributors. A nearby name is a neighbour on the map, not a vetted comparable and not a Qloo entity unless it also appears in the taste tables above.</p>
    </section>

    <section class="memo-section">
    <h2>The lease</h2>
    <p class="memo-plain">${esc(contractLine)}</p>
    <table class="memo-table">
      <tr><th>Asking rent</th><td>${esc(money(state.asking, cur))}/mo</td></tr>
      <tr><th>Share α</th><td>${esc(`${alphaPct}%`)}</td></tr>
      <tr><th>Matched base b*(α)</th><td>${v ? `${esc(money(v.base, cur))}/mo` : '—'}</td></tr>
      <tr><th>Build-out</th><td>${esc(money(state.capex, cur))}</td></tr>
      <tr><th>Term</th><td>${esc(String(state.termYears))} years</td></tr>
      <tr><th>Landlord 36-month take H₀</th><td>${v ? esc(money(v.H0, cur)) : '—'}</td></tr>
    </table>
    </section>

    <section class="memo-section">
    <h2>The book</h2>
    <p class="memo-plain">In English: how much this rent costs in closed shops. In the language credit desks use: EL, ES 95%, Merton distance to default, Kelly, Vasicek. Each line is this pin’s number, then the English.</p>
    <table class="memo-table memo-book">
      <thead><tr><th>Line</th><th>In English</th><th>This pin</th></tr></thead>
      <tbody>${lines.map(r => `<tr><td>${esc(r[0])}</td><td class="memo-en">${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join('')}</tbody>
    </table>
    <p class="memo-plain">${esc(npvLine(state.belief, v && v.b.hurdle, cur))}</p>
    </section>

    <section class="memo-section">
    <h2>Who should rent, ranked</h2>
    <p class="memo-plain">Same pin, same rent, same build-out. Only the licence family changes. Least expected loss first.</p>
    <table class="memo-table">
      <thead><tr><th>Concept family</th><th>Expected loss</th><th>Closed of 100</th></tr></thead>
      <tbody>${ranks.map((r, i) => `<tr><td>${i === 0 ? '1 · ' : ''}${esc(GROUP_NAME[r.g])}${r.g === state.group ? ' · this memo' : ''}</td><td>${esc(money(r.el, cur))}</td><td>${esc(in100(r.pd))}</td></tr>`).join('')}</tbody>
    </table>
    </section>

    <section class="memo-section">
    <h2>The maths (for the desk)</h2>
    <p class="memo-expert">Chicago business licences, n = ${esc(nLic)}. Mixture coin, κ = 0, HR_loc = 1: location did not predict 3-year survival in held-out licences. Affine lease R<sub>k</sub> = b + α G<sub>k</sub>, G<sub>k</sub> = A<sub>k</sub> + fx·f, harvest-matched b*(α) so E[H] stays H₀. ES<sub>0.95</sub> is the mean of the worst 5% of path losses (coherent; P90 is not). Merton DD = (mean capacity − rent) / SD. Kelly f* = p − (1−p)/(b−1) on the stated year-3 value. Vasicek on 10 identical units, stated ρ = ${ASSET_RHO}, not estimated. LGD = 1. Seed 20261011. ${card ? esc(card.note) : ''}</p>
    <p class="memo-formula">${plugged}</p>
    </section>

    <section class="memo-limits">
      <h2>Limits</h2>
      <p>${card ? `${esc(card.source)}, n = ${esc(card.n)}. ${esc(card.note)}` : ''} Qloo is taste, not survival. A cell is a block to scout, not a vetted address. Write ${esc(CONTACT)}.</p>
    </section>
    <p class="memo-foot">TasteFit · ${esc(hood)} · ${esc(when)} · Questions on this pin: <a href="mailto:${esc(CONTACT)}">${esc(CONTACT)}</a></p>`;
}

function runAgent() {
  const box = $('#agent');
  if (!box) return;
  box.hidden = false;
  state.startedAt = Date.now();
  state.lastEventAt = Date.now();
  box.innerHTML = `<p class="hood" id="live-title">${esc(nextTitle())}<small>find_tags → recommend → rank. One pin, one request.</small></p>
    <p id="live-out">${esc(stallOut(quietMs()))}</p>
    <span id="clock"></span>
    <ol class="agent-log"></ol>`;
  const ol = box.querySelector('ol');
  clearInterval(clockTimer);
  clockTimer = setInterval(tickRun, 1000);
  HANDLERS.started();
  const loc = (state.spot && state.spot.place && state.spot.place.neighborhood)
    ? `${state.spot.place.neighborhood}, Chicago` : 'Chicago';
  const es = new EventSource('/tenant?' + new URLSearchParams({
    city: 'Chicago', location: loc, lat: String(state.lat), lon: String(state.lon), rent: String(state.asking),
  }));
  const add = t => { const li = document.createElement('li'); li.textContent = t; ol.appendChild(li); };
  const bump = () => { state.lastEventAt = Date.now(); tickRun(); };
  es.addEventListener('step', e => {
    bump();
    const d = JSON.parse(e.data);
    HANDLERS.step(d);
    const names = (d.names || []).slice(0, 3).join(', ');
    add(`${d.workflow || d.source || 'Qloo'}${names ? ' · ' + names : ''}`);
  });
  es.addEventListener('shortlist', e => {
    bump();
    const d = JSON.parse(e.data);
    HANDLERS.shortlist(d);
    const rows = d.concepts || d.rows || [];
    add('Shortlist: ' + (rows.slice(0, 5).map(r => r.concept || r.name || r.label).filter(Boolean).join(', ') || 'ready'));
  });
  es.addEventListener('memo', () => {
    bump();
    HANDLERS.memo();
    add('Memo on this pin. Taste from Qloo. Survival from licences.');
    clearInterval(clockTimer);
    es.close();
  });
  es.addEventListener('error', e => {
    bump();
    HANDLERS.error();
    if (e.data && e.data !== 'offline') add('Agent stopped.');
    else add('Qloo agent offline.');
    clearInterval(clockTimer);
    es.close();
  });
}

async function openWicker() {
  state.lat = WICKER[0];
  state.lon = WICKER[1];
  if (map) map.setView(WICKER, 15);
  history.replaceState(null, '', '#' + shareQuery());
  openMemo();
  toast('Wicker Park pin · Print memo');
  await dropPin(state.lat, state.lon);
  const d = $('#memo');
  if (d && d.open) openMemo();
}

function openMemo() {
  const page = $('#memo-page');
  if (page) page.innerHTML = memoHTML();
  const d = $('#memo');
  if (d && d.showModal) d.showModal();
}

const memoEl = $('#memo');
if (memoEl) {
  memoEl.addEventListener('click', e => {
    if (e.target === memoEl && memoEl.close) memoEl.close();
  });
}

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 2600);
}

function shareQuery() {
  return '?' + new URLSearchParams({
    rent: String(state.asking), capex: String(state.capex), term: String(state.termYears),
    group: state.group, share: String(Math.round(100 * state.share)),
    lat: String(state.lat), lon: String(state.lon), concept: state.concept,
    ...(state.belief != null ? { belief: String(state.belief) } : {}),
  }).toString();
}

document.addEventListener('click', e => {
  const g = e.target.closest('[data-group]');
  if (g) {
    state.group = g.dataset.group;
    history.replaceState(null, '', '#' + shareQuery());
    if (state.cache[state.group]) { state.status = 'ready'; render(); }
    else load();
    return;
  }
  if (e.target.closest('[data-action=share]')) {
    const url = location.href.split('#')[0] + '#' + shareQuery();
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject())
      .then(() => toast('Link copied'), () => toast('Copy this link: ' + url));
    return;
  }
  if (e.target.closest('[data-action=memo]')) {
    openMemo();
    return;
  }
  if (e.target.closest('[data-action=print]')) {
    window.print();
    return;
  }
  if (e.target.closest('[data-action=agent]')) {
    runAgent();
    return;
  }
  if (e.target.closest('[data-action=wicker-example]')) {
    openWicker();
  }
});
document.addEventListener('input', e => {
  if (e.target.id === 'asking') {
    clearTimeout(readAsking.t);
    readAsking.t = setTimeout(readAsking, 60);
  }
  if (e.target.id === 'capex') {
    clearTimeout(readCapex.t);
    readCapex.t = setTimeout(readCapex, 60);
  }
  if (e.target.id === 'term') {
    clearTimeout(readTerm.t);
    readTerm.t = setTimeout(readTerm, 60);
  }
  if (e.target.id === 'belief') {
    clearTimeout(readBelief.t);
    readBelief.t = setTimeout(readBelief, 60);
  }
  if (e.target.id === 'rent-range') {
    state.asking = +e.target.value;
    render();
    history.replaceState(null, '', '#' + shareQuery());
  }
  if (e.target.id === 'share') {
    state.share = Math.min(0.5, Math.max(0, (+e.target.value || 0) / 100));
    render();
    history.replaceState(null, '', '#' + shareQuery());
  }
  if (e.target.id === 'concept') {
    clearTimeout(dropPin.conceptT);
    dropPin.conceptT = setTimeout(() => {
      state.concept = e.target.value.trim().slice(0, 300) || 'Korean fried chicken';
      dropPin(state.lat, state.lon);
      history.replaceState(null, '', '#' + shareQuery());
    }, 400);
  }
});
document.addEventListener('change', e => {
  if (e.target.id === 'asking') readAsking();
  if (e.target.id === 'capex') readCapex();
  if (e.target.id === 'term') readTerm();
  if (e.target.id === 'belief') readBelief();
});
document.addEventListener('submit', e => { if (e.target.id === 'lease-form') e.preventDefault(); });
document.addEventListener('toggle', e => {
  if (e.target.id === 'lab') { paint(); paintFrontier(); }
}, true);

{
  const q = new URLSearchParams((location.hash.replace(/^#/, '').split('?')[1] || location.search.replace(/^\?/, '')));
  if (q.get('rent') && +q.get('rent') > 0) state.asking = +q.get('rent');
  if (q.get('capex') && +q.get('capex') > 0) { state.capex = +q.get('capex'); state.capexTouched = true; }
  if (q.get('term') && +q.get('term') >= 1 && +q.get('term') <= 20) state.termYears = Math.round(+q.get('term'));
  if (q.get('belief') && +q.get('belief') >= 0) state.belief = +q.get('belief');
  if (q.get('share') && +q.get('share') >= 0) state.share = Math.min(0.5, Math.max(0, +q.get('share') / 100));
  if (GROUPS.includes(q.get('group'))) state.group = q.get('group');
  if (q.get('concept')) state.concept = q.get('concept').slice(0, 300);
  if (q.get('lat') && q.get('lon') && Math.abs(+q.get('lat')) <= 90 && Math.abs(+q.get('lon')) <= 180) {
    state.lat = +q.get('lat');
    state.lon = +q.get('lon');
  }
}
bootMap();
window.addEventListener('resize', () => { paint(); paintFrontier(); if (map) map.invalidateSize(); });
