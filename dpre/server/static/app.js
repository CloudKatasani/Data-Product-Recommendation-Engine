/* Data Product Recommendation Engine - browser application.
   Vanilla JS, no build step: the engine ships without dependencies and so does
   its interface. */

/* ------------------------------------------------------------------ utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

const fmt = {
  num: (v, digits = 0) => (v === null || v === undefined || Number.isNaN(v))
    ? '-' : Number(v).toLocaleString(undefined, { maximumFractionDigits: digits }),
  pct: (v, digits = 0) => (v === null || v === undefined) ? '-' : `${(Number(v) * 100).toFixed(digits)}%`,
  date: (v) => v ? String(v).slice(0, 10) : '-',
};

/* The CSRF header a state-changing request must carry. A cross-origin form
   cannot set a custom header, so sending one is the whole control. */
const CSRF_HEADER = 'X-DPRE-Request';
/* Loopback development identity. On a real deployment the SSO proxy or a
   bearer token decides who you are and this header is ignored. */
const IDENTITY_HEADER = 'X-DPRE-Identity';

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') headers.set(CSRF_HEADER, '1');
  if (state.identity) headers.set(IDENTITY_HEADER, state.identity);
  const response = await fetch(path, { ...options, headers });
  let payload = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) {
    if (response.status === 401) { state.principal = null; paintIdentity(); }
    const error = new Error(problemText(payload, response));
    error.status = response.status;
    error.code = payload && payload.code ? payload.code : '';
    throw error;
  }
  return payload;
}

/* RFC 9457 problem documents carry title + detail + a stable code. The code is
   what a client's integration team writes a ticket against, so it is shown. */
function problemText(payload, response) {
  if (!payload) return response.statusText || `HTTP ${response.status}`;
  const head = payload.title || payload.error || payload.message || response.statusText;
  const detail = payload.detail ? ` ${payload.detail}` : '';
  const code = payload.code ? ` (${payload.code})` : '';
  return `${head}${detail}${code}`;
}

function flash(message, kind = 'info', timeout = 6000) {
  const host = $('#flash');
  host.innerHTML = '';
  if (!message) return;
  const banner = el('div', { class: `banner ${kind}`, style: 'margin-bottom:14px' }, message);
  host.append(banner);
  if (timeout) setTimeout(() => { if (banner.isConnected) banner.remove(); }, timeout);
}

function severityClass(severity) {
  return { blocker: 'critical', major: 'serious', minor: 'warning', info: '' }[severity] || '';
}
function statusClass(status) {
  return { Accepted: 'good', Proposed: '', Exploratory: 'warning', Blocked: 'critical',
           Rejected: 'critical', Deferred: 'warning', Merged: '' }[status] || '';
}
function chip(text, kind = '', title = '') {
  return el('span', { class: `chip ${kind}`.trim(), title: title || text },
    kind ? el('span', { class: 'dot' }) : null, text);
}
function table(columns, rows, options = {}) {
  const head = el('thead', {}, el('tr', {}, columns.map(c =>
    el('th', { class: c.num ? 'num' : '' }, c.label))));
  const body = el('tbody', {}, rows.map(row => {
    const tr = el('tr', { class: options.onRow ? 'clickable' : '' },
      columns.map(c => el('td', { class: c.num ? 'num' : '' }, c.render(row))));
    if (options.onRow) tr.addEventListener('click', () => options.onRow(row));
    return tr;
  }));
  return [head, body];
}
function fillTable(node, columns, rows, options = {}) {
  node.innerHTML = '';
  if (!rows.length) {
    node.append(el('tbody', {}, el('tr', {}, el('td', { colspan: columns.length },
      el('div', { class: 'empty' }, options.empty || 'Nothing to show yet.')))));
    return;
  }
  node.append(...table(columns, rows, options));
}

/* ------------------------------------------------------------------ state */
const state = {
  runId: null,
  run: null,
  candidates: [],
  portfolio: null,
  industries: [],
  selectedIndustry: 'generic',
  uploads: [],
  schemas: [],
  /* Who the server says you are. A decision is attributed to the principal it
     authenticated, never to a name typed into the form (R-01). */
  identity: readIdentity(),
  principal: null,
  view: 'start',
};

/* ---------------------------------------------------------------- identity */
/* The server decides who you are: a trusted proxy header, a bearer token, or -
   on a loopback development instance - the name you sign in with here. The
   browser never asserts a reviewer name in a request body, because a decision
   that anyone can attribute to anyone is not a control. */

function readIdentity() {
  try { return sessionStorage.getItem('dpre.identity') || ''; } catch { return ''; }
}

function writeIdentity(name) {
  state.identity = name;
  try {
    if (name) sessionStorage.setItem('dpre.identity', name);
    else sessionStorage.removeItem('dpre.identity');
  } catch { /* private mode: the session still works, it just will not persist */ }
}

async function refreshPrincipal() {
  try {
    const payload = await api('/api/v1/whoami');
    state.principal = payload.principal;
  } catch (error) {
    state.principal = null;
    if (error.status !== 401) throw error;
  }
  paintIdentity();
  return state.principal;
}

function paintIdentity() {
  const host = $('#identity');
  if (!host) return;
  host.innerHTML = '';
  const who = state.principal;
  if (who && who.identity) {
    host.append(
      el('span', { class: 'who', title: `${who.roles.join(', ')} · ${who.auth_method}` },
        who.identity),
      el('button', { class: 'ghost sm', onclick: signOut }, 'Sign out'));
  } else {
    host.append(el('button', { class: 'ghost sm', onclick: signIn }, 'Sign in'));
  }
}

async function signIn() {
  const name = window.prompt(
    'Your name, as it should appear on every decision you record.\n\n' +
    'This instance accepts a name only because it is listening on localhost. ' +
    'A deployed instance takes your identity from single sign-on.',
    state.identity || '');
  if (name === null) return;
  writeIdentity(name.trim());
  const who = await refreshPrincipal();
  if (who && who.identity) flash(`Signed in as ${who.identity}.`, 'ok');
  else flash('The server did not accept that identity.', 'error');
}

function signOut() {
  writeIdentity('');
  state.principal = null;
  paintIdentity();
  flash('Signed out. Reading is still open; deciding is not.', 'info');
}

function requireIdentity(what) {
  if (state.principal && state.principal.identity) return true;
  flash(`Sign in before you ${what}: every decision is attributed to a principal.`, 'error');
  return false;
}

function mayDo(action) {
  return Boolean(state.principal && (state.principal.actions || []).includes(action));
}

/* ------------------------------------------------------------- navigation */
function showView(name) {
  state.view = name;
  $$('#tabs button').forEach(b => b.toggleAttribute('aria-current', b.dataset.view === name));
  $$('#tabs button').forEach(b => { if (b.dataset.view === name) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
  $$('section.view').forEach(section => { section.hidden = section.id !== `view-${name}`; });
  const loaders = {
    backlog: renderBacklog, portfolio: renderPortfolio,
    gaps: renderGaps, runs: renderRuns, ask: renderAsk,
  };
  if (loaders[name]) loaders[name]();
}

$('#tabs').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-view]');
  if (button) showView(button.dataset.view);
});

$('#theme-toggle').addEventListener('click', () => {
  const root = document.documentElement;
  const current = root.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : current === 'light' ? '' : 'dark';
  root.setAttribute('data-theme', next);
  try { localStorage.setItem('dpre.theme', next); } catch { /* private mode */ }
  if (state.portfolio) renderCoverageChart(state.portfolio.coverage_curve || []);
});
try {
  const saved = localStorage.getItem('dpre.theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
} catch { /* private mode */ }

$$('[data-goto]').forEach(button => button.addEventListener('click', () => {
  const panel = button.dataset.goto;
  $(`#panel-${panel}`).hidden = false;
  $(`#panel-${panel === 'manual' ? 'automated' : 'manual'}`).hidden = true;
  $(`#panel-${panel}`).scrollIntoView({ behavior: 'smooth', block: 'start' });
}));
$$('[data-close-panel]').forEach(button => button.addEventListener('click', () => {
  $(`#panel-${button.dataset.closePanel}`).hidden = true;
}));

/* --------------------------------------------------------- automated mode */
async function loadIndustries() {
  const data = await api('/api/industries');
  state.industries = data.industries;
  const grid = $('#industry-grid');
  grid.innerHTML = '';
  data.industries.forEach(industry => {
    const button = el('button', {
      class: 'industry', 'aria-pressed': industry.key === state.selectedIndustry,
      onclick: () => {
        state.selectedIndustry = industry.key;
        $$('#industry-grid .industry').forEach(b =>
          b.setAttribute('aria-pressed', b.dataset.key === industry.key));
      },
    },
      el('strong', {}, industry.label),
      el('span', {}, `${industry.domains.length} domains: ${industry.domains.slice(0, 3).join(', ')}`),
      el('span', {}, `backbone: ${industry.backbone.join(' → ')}`),
      el('span', {}, `systems: ${industry.systems.slice(0, 3).join(', ')}`));
    button.dataset.key = industry.key;
    grid.append(button);
  });
}

$('#btn-run-automated').addEventListener('click', async () => {
  const button = $('#btn-run-automated');
  const status = $('#automated-status');
  button.disabled = true;
  status.innerHTML = '';
  status.append(el('span', { class: 'spinner' }), ' generating the pack and running the seven agents...');
  try {
    const seed = $('#auto-seed').value;
    const payload = {
      industry: state.selectedIndustry,
      catalog: $('#auto-catalog').value,
      as_of: $('#auto-asof').value || null,
      seed: seed ? Number(seed) : null,
      save_workbook: true,
    };
    const result = await api('/api/run/automated', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    status.textContent = '';
    await adoptRun(result);
  } catch (error) {
    status.textContent = '';
    flash(error.message, 'error', 12000);
  } finally {
    button.disabled = false;
  }
});

$('#btn-download-pack').addEventListener('click', async () => {
  try {
    const data = await api(`/api/synthetic/${state.selectedIndustry}/workbook`);
    window.location.href = data.download;
    flash(`Workbook written with ${data.planted_defects.length} planted defect classes recorded in the Planted_Defects tab.`, 'ok');
  } catch (error) { flash(error.message, 'error'); }
});

/* ------------------------------------------------------------ manual mode */
const dropzone = $('#dropzone');
['dragenter', 'dragover'].forEach(name => dropzone.addEventListener(name, (event) => {
  event.preventDefault(); dropzone.classList.add('drag');
}));
['dragleave', 'drop'].forEach(name => dropzone.addEventListener(name, (event) => {
  event.preventDefault(); dropzone.classList.remove('drag');
}));
dropzone.addEventListener('drop', (event) => uploadFiles(event.dataTransfer.files));
$('#btn-browse').addEventListener('click', () => $('#file-input').click());
$('#file-input').addEventListener('change', (event) => uploadFiles(event.target.files));

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const form = new FormData();
  files.forEach(file => form.append('files', file, file.name));
  const status = $('#manual-status');
  status.innerHTML = '';
  status.append(el('span', { class: 'spinner' }), ` reading ${files.length} file(s)...`);
  try {
    const data = await api('/api/upload', { method: 'POST', body: form });
    status.textContent = '';
    data.files.forEach(file => state.uploads.push(file));
    renderUploads();
  } catch (error) {
    status.textContent = '';
    flash(error.message, 'error', 12000);
  }
}

function renderUploads() {
  const host = $('#manual-files');
  host.innerHTML = '';
  if (!state.uploads.length) { $('#btn-run-manual').disabled = true; return; }
  state.uploads.forEach((file, fileIndex) => {
    const card = el('div', { class: 'file-card stack' });
    card.append(el('div', { class: 'row between' },
      el('div', {}, el('strong', {}, file.file),
        el('span', { class: 'muted small' }, ` ${fmt.num((file.size_bytes || 0) / 1024)} KB`)),
      el('button', { class: 'ghost sm', onclick: () => { state.uploads.splice(fileIndex, 1); renderUploads(); } }, 'Remove')));
    if (file.error) card.append(el('div', { class: 'banner error' }, file.error));
    (file.tables || []).forEach((tableInfo, tableIndex) => {
      tableInfo.binding = tableInfo.binding || (tableInfo.confidence >= 0.4 ? tableInfo.suggested_schema : '');
      tableInfo.mapping = tableInfo.mapping || {};
      card.append(renderTableBinding(file, tableInfo, fileIndex, tableIndex));
    });
    host.append(card);
  });
  updateManualReadiness();
}

function renderTableBinding(file, tableInfo, fileIndex, tableIndex) {
  const wrap = el('div', { class: 'stack', style: 'border-top:1px solid var(--border);padding-top:10px' });
  const select = el('select', {
    onchange: (event) => {
      tableInfo.binding = event.target.value;
      tableInfo.mapping = {};
      renderUploads();
    },
  }, el('option', { value: '' }, 'ignore this table'),
     state.schemas.map(schema => el('option', {
       value: schema.key, selected: schema.key === tableInfo.binding,
     }, schema.label)));

  wrap.append(el('div', { class: 'row' },
    el('div', {}, el('strong', {}, tableInfo.sheet || 'table'),
      el('span', { class: 'muted small' }, ` ${fmt.num(tableInfo.rows)} rows, ${tableInfo.columns.length} columns`)),
    el('div', { class: 'spacer' }),
    tableInfo.suggested_schema
      ? chip(`detected ${tableInfo.suggested_label || tableInfo.suggested_schema} (${fmt.pct(tableInfo.confidence)})`,
             tableInfo.confidence >= 0.7 ? 'good' : 'warning')
      : chip('not recognised', 'warning'),
    select));

  const schema = state.schemas.find(s => s.key === tableInfo.binding);
  if (schema) {
    const grid = el('div', { class: 'mapping-grid' });
    schema.fields.forEach(field => {
      const current = tableInfo.mapping[field.name] || '';
      grid.append(el('label', {},
        el('span', { class: field.required ? 'req' : '' }, field.name),
        el('select', {
          title: field.used_for,
          onchange: (event) => {
            if (event.target.value) tableInfo.mapping[field.name] = event.target.value;
            else delete tableInfo.mapping[field.name];
            updateManualReadiness();
          },
        }, el('option', { value: '' }, current ? '— not mapped —' : '— not mapped —'),
           tableInfo.columns.map(column => el('option', { value: column, selected: column === current }, column)))));
    });
    const missing = schema.fields.filter(f => f.required && !tableInfo.mapping[f.name]).map(f => f.name);
    if (missing.length) {
      wrap.append(el('div', { class: 'banner error small' },
        `Required fields not mapped: ${missing.join(', ')}`));
    }
    wrap.append(grid);
  }
  return wrap;
}

function selectedSources() {
  const sources = [];
  state.uploads.forEach(file => (file.tables || []).forEach(tableInfo => {
    if (!tableInfo.binding) return;
    sources.push({
      path: file.path, schema_key: tableInfo.binding, sheet: tableInfo.sheet,
      mapping: tableInfo.mapping, label: file.file,
    });
  }));
  return sources;
}

function updateManualReadiness() {
  const sources = selectedSources();
  const hasLineage = sources.some(s => s.schema_key === 'cognos_kpi_lineage' || s.schema_key === 'powerbi_measure_lineage');
  $('#btn-run-manual').disabled = !hasLineage;
  const status = $('#manual-status');
  if (!sources.length) { status.textContent = 'Bind at least one table to an input contract.'; return; }
  status.textContent = hasLineage
    ? `${sources.length} table(s) bound. The engine will resolve them into one graph.`
    : 'A KPI lineage extract (Cognos or Power BI) is required before the engine can run.';
}

$('#btn-run-manual').addEventListener('click', async () => {
  const button = $('#btn-run-manual');
  const status = $('#manual-status');
  button.disabled = true;
  status.innerHTML = '';
  status.append(el('span', { class: 'spinner' }), ' resolving, canonicalizing, clustering, scoring...');
  try {
    const result = await api('/api/run/manual', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sources: selectedSources(),
        catalog: $('#manual-catalog').value || null,
        as_of: $('#manual-asof').value || null,
      }),
    });
    status.textContent = '';
    if (result.ok === false) {
      renderIngestIssues(result.ingest);
      flash('Ingestion failed validation. Fix the errors listed below, or bind the missing input.', 'error', 15000);
      return;
    }
    await adoptRun(result);
  } catch (error) {
    status.textContent = '';
    flash(error.message, 'error', 15000);
  } finally {
    button.disabled = false;
    updateManualReadiness();
  }
});

function renderIngestIssues(ingest) {
  const host = $('#run-summary');
  host.hidden = false;
  host.innerHTML = '';
  host.append(el('h2', {}, 'Ingestion report'));
  const issues = (ingest.validation && ingest.validation.issues) || [];
  host.append(el('div', { class: 'stack' }, issues.map(issue =>
    el('div', { class: `banner ${issue.severity === 'error' ? 'error' : 'info'}` },
      el('strong', {}, `${issue.code}: `), issue.message,
      issue.detail ? el('div', { class: 'small muted' }, issue.detail) : null))));
}

/* ------------------------------------------------------------ run summary */
async function adoptRun(result) {
  state.runId = result.run_id;
  state.run = result.summary;
  state.candidates = result.candidates || [];
  state.portfolio = result.portfolio || null;
  $('#run-pill').hidden = false;
  $('#run-pill-text').textContent = `${result.summary.industry || result.summary.mode} · ${result.run_id}`;
  renderRunSummary(result);
  flash(`Run ${result.run_id} finished: ${state.candidates.length} candidates from ` +
        `${result.summary.stats.canonicalization.canonical_metrics} canonical metrics.`,
        result.summary.published ? 'ok' : 'info', 9000);
  showView('backlog');
}

function renderRunSummary(result) {
  const host = $('#run-summary');
  host.hidden = false;
  host.innerHTML = '';
  const summary = result.summary;
  const stats = summary.stats || {};
  host.append(el('div', { class: 'row between' },
    el('h2', {}, `Run ${summary.run_id}`),
    el('div', { class: 'row' },
      chip(summary.mode, ''),
      chip(summary.industry || 'manual', ''),
      chip(summary.catalog, ''),
      chip(summary.published ? 'published' : 'held back', summary.published ? 'good' : 'warning'),
      chip(`weights ${summary.weight_version}`, ''),
      chip(summary.parser_version, ''))));

  host.append(el('div', { class: 'tiles', style: 'margin:12px 0' },
    tile(fmt.num(stats.reports), 'reports ingested'),
    tile(fmt.num(stats.kpi_rows), 'KPI lineage rows'),
    tile(fmt.num(stats.canonicalization?.canonical_metrics), 'canonical metrics',
         `${fmt.num(stats.canonicalization?.rows_collapsed)} rows collapsed`),
    tile(fmt.num(stats.canonicalization?.conflicts), 'conflicts surfaced'),
    tile(fmt.num(state.candidates.length), 'candidates'),
    tile(fmt.pct(stats.usage_coverage_top_n), 'usage covered by top 20')));

  host.append(el('h3', {}, 'Quality gates'));
  const gates = el('div', { class: 'table-wrap' }, el('table'));
  fillTable($('table', gates), [
    { label: 'Gate', render: g => g.gate },
    { label: '', render: g => chip(g.passed ? 'pass' : 'fail', g.passed ? 'good' : 'critical') },
    { label: 'Value', num: true, render: g => typeof g.value === 'number' ? g.value.toFixed(3) : g.value },
    { label: 'Threshold', num: true, render: g => String(g.threshold) },
    { label: 'What it protects', render: g => el('span', { class: 'small secondary' }, g.detail) },
  ], summary.quality_gates || []);
  host.append(gates);

  host.append(el('h3', { style: 'margin-top:14px' }, 'Agent run'));
  const agents = el('div', { class: 'table-wrap' }, el('table'));
  fillTable($('table', agents), [
    { label: 'Agent', render: a => el('strong', {}, a.agent) },
    { label: 'Seconds', num: true, render: a => a.seconds.toFixed(2) },
    { label: 'What it wrote', render: a => el('span', { class: 'small secondary' }, a.note) },
  ], summary.agents || []);
  host.append(agents);

  if ((summary.warnings || []).length) {
    host.append(el('h3', { style: 'margin-top:14px' }, 'Warnings'));
    host.append(el('ul', { class: 'findings' }, summary.warnings.map(w => el('li', {}, w))));
  }
}

function tile(value, label, sub) {
  return el('div', { class: 'tile' },
    el('div', { class: 'value' }, value),
    el('div', { class: 'label' }, label),
    sub ? el('div', { class: 'sub' }, sub) : null);
}

/* ---------------------------------------------------------------- backlog */
async function ensureRun() {
  if (state.runId) return true;
  const data = await api('/api/runs');
  if (!data.runs.length) return false;
  state.runId = data.runs[0].run_id;
  state.run = data.runs[0];
  $('#run-pill').hidden = false;
  $('#run-pill-text').textContent = `${state.run.industry || state.run.mode} · ${state.runId}`;
  return true;
}

async function renderBacklog() {
  if (!(await ensureRun())) { emptyView('#view-backlog'); return; }
  if (!state.candidates.length) {
    const data = await api(`/api/runs/${state.runId}/candidates`);
    state.candidates = data.candidates;
  }
  const options = (key) => ['', ...new Set(state.candidates.map(c => c[key]).filter(Boolean))];
  [['#filter-status', 'status'], ['#filter-archetype', 'archetype'],
   ['#filter-tier', 'tier'], ['#filter-domain', 'domain']].forEach(([sel, key]) => {
    const select = $(sel);
    const current = select.value;
    select.innerHTML = '';
    options(key).forEach(value => select.append(el('option', { value }, value || 'all')));
    select.value = current;
    select.onchange = paintBacklog;
  });
  $('#filter-text').oninput = paintBacklog;
  paintBacklog();
}

function filteredCandidates() {
  const text = ($('#filter-text').value || '').toLowerCase();
  return state.candidates.filter(c =>
    (!$('#filter-status').value || c.status === $('#filter-status').value) &&
    (!$('#filter-archetype').value || c.archetype === $('#filter-archetype').value) &&
    (!$('#filter-tier').value || c.tier === $('#filter-tier').value) &&
    (!$('#filter-domain').value || c.domain === $('#filter-domain').value) &&
    (!text || c.proposed_name.toLowerCase().includes(text) || c.candidate_id.toLowerCase().includes(text)));
}

function paintBacklog() {
  const rows = filteredCandidates();
  const tiles = $('#backlog-tiles');
  tiles.innerHTML = '';
  const proposed = rows.filter(r => r.status === 'Proposed').length;
  const retirable = rows.reduce((total, r) => total + r.reports_retirable, 0);
  const conflicts = rows.reduce((total, r) => total + r.conflicts, 0);
  tiles.append(
    tile(fmt.num(rows.length), 'candidates shown'),
    tile(fmt.num(proposed), 'ready for review', 'status Proposed'),
    tile(fmt.num(retirable), 'reports fully covered'),
    tile(fmt.num(conflicts), 'conflicts to adjudicate'));

  fillTable($('#backlog-table'), [
    { label: 'Candidate', render: c => el('div', {},
        el('strong', {}, c.proposed_name),
        c.name_status === 'AI_DRAFT' ? chip('AI_DRAFT', 'draft') : null,
        el('div', { class: 'small muted' }, c.candidate_id + ' · ' + c.origin)) },
    { label: 'Status', render: c => chip(c.status, statusClass(c.status)) },
    { label: 'Archetype', render: c => el('div', {}, c.archetype,
        el('div', { class: 'small muted' }, `confidence ${c.archetype_confidence?.toFixed(2)}` +
          (c.archetype_runner_up ? ` or ${c.archetype_runner_up}` : ''))) },
    { label: 'Tier', render: c => c.tier },
    { label: 'Grain', render: c => c.grain },
    { label: 'Metrics', num: true, render: c => fmt.num(c.metrics) },
    { label: 'Retires', num: true, render: c => fmt.num(c.reports_retirable) },
    { label: 'Users', num: true, render: c => fmt.num(c.users) },
    { label: 'Conflicts', num: true, render: c => fmt.num(c.conflicts) },
    { label: 'Composite', num: true, render: c => el('strong', {}, fmt.num(c.composite, 1)) },
  ], rows, { onRow: (row) => openDrawer(row.candidate_id), empty: 'No candidates match those filters.' });
}

function emptyView(selector) {
  flash('Run the engine first: choose Manual or Automated on the Start tab.', 'info');
  showView('start');
}

/* ----------------------------------------------------------------- drawer */
async function openDrawer(candidateId) {
  const root = $('#drawer-root');
  root.innerHTML = '';
  const backdrop = el('div', { class: 'drawer-backdrop', onclick: () => root.innerHTML = '' });
  const drawer = el('aside', { class: 'drawer' }, el('div', { class: 'empty' },
    el('span', { class: 'spinner' }), ' loading candidate...'));
  root.append(backdrop, drawer);
  document.addEventListener('keydown', function escape(event) {
    if (event.key === 'Escape') { root.innerHTML = ''; document.removeEventListener('keydown', escape); }
  });
  try {
    const data = await api(`/api/runs/${state.runId}/candidates/${candidateId}`);
    drawer.innerHTML = '';
    drawer.append(drawerHeader(data), drawerBody(data));
  } catch (error) {
    drawer.innerHTML = '';
    drawer.append(el('div', { class: 'banner error', style: 'margin:20px' }, error.message));
  }
}

function drawerHeader(data) {
  const candidate = data.candidate;
  const score = candidate.score || {};
  const header = el('header', {});
  header.append(el('div', { class: 'row between' },
    el('div', {},
      el('h2', {}, candidate.proposed_name,
        candidate.name_status === 'AI_DRAFT' ? chip('AI_DRAFT', 'draft') : null),
      el('div', { class: 'small muted' }, `${candidate.candidate_id} · ${candidate.domain} · grain ${candidate.grain}`)),
    el('button', { class: 'ghost sm', onclick: () => $('#drawer-root').innerHTML = '' }, 'Close')));
  header.append(el('p', { class: 'secondary small', style: 'margin-top:8px' }, candidate.purpose));
  header.append(el('div', { class: 'row' },
    chip(data.status, statusClass(data.status)),
    chip(candidate.archetype), chip(candidate.tier), chip(candidate.origin),
    ...(candidate.tools || []).map(t => chip(t))));

  const tabs = el('div', { class: 'drawer-tabs' });
  const panels = {};
  const names = ['Overview', 'Score', 'Metrics', 'Consumers', 'Reports',
                 'Attributes', 'Sources', 'Critique', 'Decisions', 'Seeds', 'Review'];
  names.forEach((name, index) => {
    const button = el('button', { 'aria-current': index === 0 ? 'true' : 'false',
      onclick: () => {
        $$('.drawer-tabs button').forEach(b => b.setAttribute('aria-current', String(b === button)));
        Object.entries(panels).forEach(([key, node]) => { node.hidden = key !== name; });
      } }, name);
    tabs.append(button);
  });
  header.append(tabs);
  header._panels = panels;
  header._names = names;
  return header;
}

function drawerBody(data) {
  const header = $('.drawer header') || null;
  const body = el('div', { class: 'body' });
  const candidate = data.candidate;
  const score = candidate.score || {};
  const panels = {};

  panels.Overview = el('div', { class: 'stack' },
    el('div', { class: 'tiles' },
      tile(fmt.num(score.composite, 1), 'composite score', `weights ${score.weight_version || ''}`),
      tile(fmt.num((candidate.reports || []).filter(r => r.coverage >= 1).length), 'reports fully covered'),
      tile(fmt.num((candidate.consumers || []).reduce((t, c) => t + c.users, 0)), 'users served'),
      tile(fmt.num((candidate.metric_ids || []).length), 'canonical metrics')),
    el('h3', {}, 'Value hypothesis'),
    el('p', { class: 'secondary' }, (candidate.narrative || {}).value_hypothesis || '-'),
    el('h3', {}, 'Hard gates'),
    el('div', { class: 'stack' }, (score.gates || []).map(gate =>
      el('div', { class: 'row' }, chip(`${gate.gate} ${gate.passed ? 'pass' : 'fail'}`,
        gate.passed ? 'good' : 'critical'),
        el('span', { class: 'small secondary' }, `${gate.name}: ${gate.detail}${gate.effect ? ' — ' + gate.effect : ''}`)))),
    (candidate.gaps || []).length ? el('h3', {}, 'Gaps') : null,
    (candidate.gaps || []).length ? el('ul', { class: 'findings' },
      candidate.gaps.map(gap => el('li', {}, gap))) : null);

  panels.Score = el('div', { class: 'stack' },
    el('div', { class: 'stack' }, ['demand', 'consolidation', 'feasibility', 'risk'].map(dim =>
      meter(dim, score[dim] || 0, dim === 'risk'))),
    el('h3', {}, 'Features and the evidence behind them'),
    tableCard([
      { label: 'Dimension', render: f => f.dimension },
      { label: 'Feature', render: f => f.feature },
      { label: 'Value', num: true, render: f => fmt.num(f.value, 3) },
      { label: 'Normalized', num: true, render: f => fmt.num(f.normalized, 3) },
      { label: 'Weight', num: true, render: f => f.weight },
      { label: 'Contribution', num: true, render: f => fmt.num(f.contribution, 1) },
      { label: 'Definition', render: f => el('span', { class: 'small secondary' }, f.detail) },
    ], score.features || []),
    el('h3', {}, `Evidence rows (${(data.evidence || []).length})`),
    tableCard([
      { label: 'Feature', render: e => e.feature },
      { label: 'Type', render: e => e.evidence_type },
      { label: 'Evidence id', render: e => el('code', {}, e.evidence_id) },
      { label: 'Detail', render: e => el('span', { class: 'small secondary' }, e.detail) },
    ], data.evidence || []));

  panels.Metrics = tableCard([
    { label: 'Canonical name', render: m => el('div', {}, el('strong', {}, m.canonical_name),
        m.name_status === 'AI_DRAFT' ? chip('AI_DRAFT', 'draft') : chip('accepted', 'good')) },
    { label: 'Definition', render: m => el('span', { class: 'small secondary' }, m.definition) },
    { label: 'Aggregation', render: m => m.aggregation },
    { label: 'Grain', render: m => m.grain },
    { label: 'Reports', num: true, render: m => fmt.num(m.report_count) },
    { label: 'Variants', num: true, render: m => fmt.num(m.variant_count) },
    { label: 'Steward', render: m => m.steward_id || chip('unassigned', 'warning') },
    { label: '', render: m => m.name_status === 'AI_DRAFT'
        ? el('button', { class: 'sm', onclick: () => acceptName(m.metric_id) }, 'Accept name') : '' },
  ], data.metrics || []);

  panels.Consumers = tableCard([
    { label: 'Business unit', render: c => el('strong', {}, c.business_unit) },
    { label: 'Users', num: true, render: c => fmt.num(c.users) },
    { label: 'Reports', num: true, render: c => fmt.num(c.report_count) },
    { label: 'Scheduled', num: true, render: c => fmt.pct(c.scheduled_share) },
    { label: 'Cadence', render: c => c.cadence },
    { label: 'Top reports', render: c => el('span', { class: 'small muted' }, (c.top_reports || []).join(', ')) },
  ], candidate.consumers || []);

  panels.Reports = tableCard([
    { label: 'Report', render: r => el('div', {}, el('strong', {}, r.report_name),
        el('div', { class: 'small muted' }, r.report_id)) },
    { label: 'Coverage', num: true, render: r => fmt.pct(r.coverage) },
    { label: 'Disposition', render: r => r.disposition },
    { label: 'Users', num: true, render: r => fmt.num(r.users) },
    { label: 'Last run', render: r => fmt.date(r.last_run) },
    { label: 'Owner', render: r => r.owner },
    { label: '', render: r => el('button', { class: 'sm ghost',
        onclick: () => markCritical(r.report_id) }, 'Decision-critical') },
  ], candidate.reports || []);

  panels.Attributes = tableCard([
    { label: 'Attribute', render: a => el('strong', {}, a.name) },
    { label: 'Role', render: a => a.role },
    { label: 'Business term', render: a => a.business_term || chip('missing', 'warning') },
    { label: 'Type', render: a => a.data_type },
    { label: 'Sensitivity', render: a => a.pii_flag ? chip(`${a.sensitivity} · PII`, 'critical') : chip(a.sensitivity) },
    { label: 'Steward', render: a => a.steward_id || chip('unassigned', 'warning') },
    { label: 'Lineage', num: true, render: a => a.confidence?.toFixed(2) },
  ], candidate.attributes || []);

  panels.Sources = tableCard([
    { label: 'Table', render: s => el('code', {}, s.table_fqn) },
    { label: 'System', render: s => s.system },
    { label: 'Share of metrics', num: true, render: s => fmt.pct(s.share_of_metrics) },
    { label: 'System of record', render: s => s.sor_flag ? chip('SoR', 'good') : chip('not SoR', 'warning') },
    { label: 'Lifecycle', render: s => s.lifecycle_status === 'sunset'
        ? chip(`sunset${s.successor_system ? ' → ' + s.successor_system : ', no successor'}`, 'critical')
        : chip('active', 'good') },
  ], candidate.sources || []);

  panels.Critique = el('div', { class: 'stack' },
    el('p', { class: 'secondary small' }, 'What a reviewer is likely to raise, checked against the hard gates and the Stage 1 and Stage 2 exit criteria.'),
    el('div', { class: 'stack' }, (candidate.critique || []).map(finding =>
      el('div', { class: 'row', style: 'align-items:flex-start' },
        chip(finding.severity, severityClass(finding.severity)),
        el('div', {}, el('strong', { class: 'small' }, finding.criterion),
          el('div', { class: 'small secondary' }, finding.finding))))));

  panels.Decisions = el('div', { class: 'stack' },
    el('p', { class: 'secondary small' }, 'Stage 1 decision-register entries drafted from usage. A human must confirm the blocked decision, the latency tolerance and the consequence of not deciding.'),
    ...(candidate.decisions_drafted || []).map(draft => el('div', { class: 'card' },
      el('div', { class: 'row between' }, el('h3', {}, draft.business_unit), chip(draft.status, 'draft')),
      el('dl', { class: 'kv' },
        el('dt', {}, 'Persona'), el('dd', {}, draft.persona),
        el('dt', {}, 'Cadence'), el('dd', {}, draft.cadence),
        el('dt', {}, 'Inferred decision'), el('dd', {}, draft.inferred_decision),
        el('dt', {}, 'Latency tolerance'), el('dd', { class: 'muted' }, draft.latency_tolerance),
        el('dt', {}, 'Consequence'), el('dd', { class: 'muted' }, draft.consequence)),
      el('div', { class: 'small muted', style: 'margin-top:6px' }, 'Questions asked today:'),
      el('ul', { class: 'findings' }, (draft.questions || []).map(q => el('li', {}, q))))));

  panels.Seeds = el('div', { class: 'stack' },
    el('p', { class: 'secondary small' }, 'Seed artifacts for the Data Product Factory. Nothing marked AI_DRAFT may reach the catalog until a steward accepts it.'),
    tableCard([
      { label: 'File', render: s => el('strong', {}, s.name) },
      { label: 'Size', num: true, render: s => `${fmt.num(s.size / 1024, 1)} KB` },
      { label: '', render: s => el('a', { href: s.download, class: 'chip' }, 'Download') },
    ], data.seeds || []));

  panels.Review = reviewPanel(data);

  Object.entries(panels).forEach(([name, node]) => {
    node.hidden = name !== 'Overview';
    body.append(node);
  });
  if (header) header._panelNodes = panels;
  // wire the header tabs to these panels
  setTimeout(() => {
    const buttons = $$('.drawer-tabs button');
    buttons.forEach(button => {
      button.onclick = () => {
        buttons.forEach(b => b.setAttribute('aria-current', String(b === button)));
        Object.entries(panels).forEach(([key, node]) => { node.hidden = key !== button.textContent; });
      };
    });
  }, 0);
  return body;
}

function meter(label, value, isRisk) {
  const pct = Math.max(0, Math.min(100, value));
  return el('div', { class: 'meter' },
    el('span', { class: 'secondary' }, label),
    el('span', { class: 'track' }, el('span', { class: `fill${isRisk ? ' risk' : ''}`, style: `width:${pct}%` })),
    el('span', { class: 'val' }, fmt.num(value, 0)));
}

function tableCard(columns, rows) {
  const node = el('table');
  fillTable(node, columns, rows);
  return el('div', { class: 'table-wrap' }, node);
}

function reviewPanel(data) {
  const candidate = data.candidate;
  const decisionSelect = el('select', {});
  const reason = el('select', {});
  const note = el('textarea', { rows: 2, placeholder: 'note for the feedback table' });
  const target = el('input', { placeholder: 'merge into candidate id' });
  const gate = el('input', { placeholder: 'gate waived, e.g. G3' });
  const waiverReason = el('input', { placeholder: 'why the gate may be waived' });
  const secondApprover = el('input', { placeholder: 'second approver' });
  const result = el('div', {});
  const exceptionRow = el('div', { class: 'row', hidden: true },
    el('label', { class: 'field' }, 'Gate waived', gate),
    el('label', { class: 'field' }, 'Waiver reason', waiverReason),
    el('label', { class: 'field' }, 'Second approver', secondApprover));
  const mergeField = el('label', { class: 'field', hidden: true }, 'Merge target', target);

  /* The decision and its reason code come from a closed vocabulary, so a
     feedback model trained on these rows is reading categories and not prose. */
  const vocabulary = state.reasonCodes || {};
  Object.keys(vocabulary).forEach(name => decisionSelect.append(el('option', { value: name }, name)));
  decisionSelect.value = 'Accept';

  function paintReasons() {
    const decision = decisionSelect.value;
    reason.innerHTML = '';
    reason.append(el('option', { value: '' }, '(no reason code)'));
    (vocabulary[decision] || []).forEach(code => reason.append(el('option', { value: code }, code)));
    exceptionRow.hidden = decision !== 'AcceptWithException';
    mergeField.hidden = decision !== 'Merge';
  }
  decisionSelect.onchange = paintReasons;
  paintReasons();

  async function send(extra = {}) {
    if (!requireIdentity('record a decision')) return;
    const decision = decisionSelect.value;
    try {
      const response = await api(`/api/v1/runs/${state.runId}/review`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          candidate_id: candidate.candidate_id, decision,
          reason_code: reason.value, note: note.value.trim(),
          target_candidate_id: target.value.trim(),
          gate_waived: gate.value.trim(), waiver_reason: waiverReason.value.trim(),
          second_approver: secondApprover.value.trim(), ...extra,
        }),
      });
      result.innerHTML = '';
      result.append(el('div', { class: 'banner ok' },
        `${decision} recorded by ${response.reviewer}. Status is now ` +
        `${response.outcome.status}. ${response.outcome.note || ''}`));
      state.candidates = [];
      renderBacklog();
    } catch (error) {
      result.innerHTML = '';
      result.append(el('div', { class: 'banner error' }, error.message));
    }
  }

  return el('div', { class: 'stack' },
    el('p', { class: 'secondary small' },
      'The engine proposes; humans decide. No engine path can move a candidate past ' +
      'Proposed, and the decision is attributed to the principal the server authenticated, ' +
      'not to a name typed into this form.'),
    el('div', { class: 'row' },
      el('label', { class: 'field' }, 'Decision', decisionSelect),
      el('label', { class: 'field' }, 'Reason code', reason),
      mergeField),
    exceptionRow,
    el('label', { class: 'field' }, 'Note', note),
    el('div', { class: 'row' },
      el('button', { class: 'primary', onclick: () => send() }, 'Record decision'),
      el('button', { onclick: () => send({ split_by: 'grain' }) }, 'Split by grain'),
      el('button', { onclick: () => send({ split_by: 'consumer' }) }, 'Split by consumer')),
    result,
    (data.decisions || []).length ? el('h3', {}, 'Decision history') : null,
    (data.decisions || []).length ? tableCard([
      { label: 'Decision', render: d => d.decision },
      { label: 'Reviewer', render: d => d.reviewer },
      { label: 'Reason', render: d => d.reason_code },
      { label: 'From', render: d => d.previous_status || '' },
      { label: 'To', render: d => d.new_status || d.status || '' },
      { label: 'When', render: d => d.decided_at },
      { label: 'Note', render: d => el('span', { class: 'small secondary' }, d.note) },
    ], data.decisions) : null);
}

async function acceptName(metricId) {
  if (!requireIdentity('accept a canonical name')) return;
  try {
    await api(`/api/v1/runs/${state.runId}/metrics/${metricId}/accept-name`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    flash('Name accepted. It may now reach the catalog payload.', 'ok');
  } catch (error) { flash(error.message, 'error'); }
}

async function markCritical(reportId) {
  if (!requireIdentity('mark a report decision-critical')) return;
  try {
    await api(`/api/runs/${state.runId}/reports/decision-critical`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ report_id: reportId }),
    });
    flash(`${reportId} marked decision-critical: its usage weight is floored on the next run.`, 'ok');
  } catch (error) { flash(error.message, 'error'); }
}

/* -------------------------------------------------------------- portfolio */
async function renderPortfolio() {
  if (!(await ensureRun())) { emptyView('#view-portfolio'); return; }
  const data = await api(`/api/runs/${state.runId}/portfolio`);
  state.portfolio = data;
  renderCoverageChart(data.coverage_curve || []);

  const mix = $('#portfolio-mix');
  mix.innerHTML = '';
  [['Status', data.status_mix], ['Archetype', data.archetype_mix],
   ['Tier', data.tier_mix], ['Domain', data.domain_mix]].forEach(([label, values]) => {
    const total = Object.values(values || {}).reduce((a, b) => a + b, 0) || 1;
    mix.append(el('div', {}, el('h3', {}, label),
      el('div', { class: 'stack' }, Object.entries(values || {})
        .sort((a, b) => b[1] - a[1])
        .map(([key, count]) => el('div', { class: 'meter' },
          el('span', { class: 'secondary', title: key }, key),
          el('span', { class: 'track' }, el('span', { class: 'fill', style: `width:${(count / total) * 100}%` })),
          el('span', { class: 'val' }, count))))));
  });

  fillTable($('#retirement-table'), [
    { label: 'Candidate', render: r => el('div', {}, el('strong', {}, r.candidate),
        el('div', { class: 'small muted' }, r.candidate_id)) },
    { label: 'Status', render: r => chip(r.status, statusClass(r.status)) },
    { label: 'Fully covered', num: true, render: r => fmt.num(r.fully_covered) },
    { label: 'Partially', num: true, render: r => fmt.num(r.partially_covered) },
    { label: 'Users affected', num: true, render: r => fmt.num(r.users_affected) },
    { label: 'By disposition', render: r => el('div', { class: 'row' },
        Object.entries(r.by_disposition || {}).map(([k, v]) => chip(`${k} ${v}`))) },
  ], data.retirement_map || []);

  fillTable($('#heat-table'), [
    { label: 'Metric label', render: r => el('strong', {}, r.label) },
    { label: 'Competing definitions', num: true, render: r => fmt.num(r.competing_definitions) },
    { label: 'Conflicts', num: true, render: r => fmt.num(r.conflicts) },
    { label: 'Usage at stake', num: true, render: r => fmt.num(r.usage_at_stake) },
    { label: 'Patterns', render: r => el('div', { class: 'row' }, (r.patterns || []).map(p => chip(p, 'warning'))) },
    { label: 'Open', num: true, render: r => fmt.num(r.open) },
  ], data.conflict_heat_map || []);
}

function renderCoverageChart(curve) {
  const host = $('#coverage-chart');
  host.innerHTML = '';
  if (!curve.length) { host.append(el('div', { class: 'empty' }, 'No coverage data yet.')); return; }

  const width = 640, height = 260;
  const margin = { top: 16, right: 18, bottom: 34, left: 46 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const maxN = curve.length;
  const x = (n) => margin.left + (maxN === 1 ? plotW / 2 : ((n - 1) / (maxN - 1)) * plotW);
  const y = (v) => margin.top + plotH - v * plotH;

  const svgNS = 'http://www.w3.org/2000/svg';
  const make = (tag, attrs = {}) => {
    const node = document.createElementNS(svgNS, tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    return node;
  };
  const svg = make('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img',
    'aria-label': 'Cumulative usage-weighted consumption covered by the top N candidates' });

  [0, 0.25, 0.5, 0.75, 1].forEach(value => {
    svg.append(make('line', { class: 'grid-line', x1: margin.left, x2: width - margin.right,
      y1: y(value), y2: y(value) }));
    const label = make('text', { class: 'tick', x: margin.left - 8, y: y(value) + 3.5,
      'text-anchor': 'end' });
    label.textContent = `${Math.round(value * 100)}%`;
    svg.append(label);
  });

  // The coverage sanity gate: the top 20 candidates must cover at least half.
  const gateY = y(0.5);
  svg.append(make('line', { class: 'threshold', x1: margin.left, x2: width - margin.right,
    y1: gateY, y2: gateY }));
  const gateLabel = make('text', { class: 'threshold-label', x: width - margin.right,
    y: gateY - 6, 'text-anchor': 'end' });
  gateLabel.textContent = 'coverage gate 50%';
  svg.append(gateLabel);

  const points = curve.map(row => [x(row.n), y(row.cumulative_coverage)]);
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area = `${line} L${points[points.length - 1][0].toFixed(1)},${y(0)} L${points[0][0].toFixed(1)},${y(0)} Z`;
  svg.append(make('path', { class: 'series-area', d: area }));
  svg.append(make('path', { class: 'series-line', d: line }));

  svg.append(make('line', { class: 'axis-line', x1: margin.left, x2: width - margin.right,
    y1: y(0), y2: y(0) }));
  const ticks = new Set([1, Math.ceil(maxN / 2), maxN, Math.min(20, maxN)]);
  ticks.forEach(n => {
    const label = make('text', { class: 'tick', x: x(n), y: height - 14, 'text-anchor': 'middle' });
    label.textContent = String(n);
    svg.append(label);
  });
  const axisTitle = make('text', { class: 'tick', x: margin.left + plotW / 2, y: height - 2,
    'text-anchor': 'middle' });
  axisTitle.textContent = 'top N candidates by composite score';
  svg.append(axisTitle);

  const crosshair = make('line', { class: 'crosshair', y1: margin.top, y2: y(0), opacity: 0 });
  const dot = make('circle', { class: 'hover-dot', r: 4.5, opacity: 0 });
  svg.append(crosshair, dot);
  host.append(svg);

  const tooltip = el('div', { class: 'chart-tooltip', hidden: true });
  host.append(tooltip);
  svg.addEventListener('mousemove', (event) => {
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const index = Math.max(0, Math.min(curve.length - 1,
      Math.round(((px - margin.left) / plotW) * (maxN - 1))));
    const row = curve[index];
    const cx = x(row.n), cy = y(row.cumulative_coverage);
    crosshair.setAttribute('x1', cx); crosshair.setAttribute('x2', cx);
    crosshair.setAttribute('opacity', 1);
    dot.setAttribute('cx', cx); dot.setAttribute('cy', cy); dot.setAttribute('opacity', 1);
    tooltip.hidden = false;
    tooltip.innerHTML = '';
    tooltip.append(
      el('div', {}, el('strong', {}, `Top ${row.n}`)),
      el('div', {}, `${fmt.pct(row.cumulative_coverage, 1)} of usage-weighted consumption`),
      el('div', { class: 'small muted' }, row.candidate));
    const left = (cx / width) * box.width;
    tooltip.style.left = `${Math.min(box.width - 220, Math.max(0, left + 12))}px`;
    tooltip.style.top = `${(cy / height) * box.height - 10}px`;
  });
  svg.addEventListener('mouseleave', () => {
    crosshair.setAttribute('opacity', 0);
    dot.setAttribute('opacity', 0);
    tooltip.hidden = true;
  });

  host.append(el('figcaption', { class: 'small muted' },
    `The top 20 candidates cover ${fmt.pct((curve[Math.min(19, curve.length - 1)] || {}).cumulative_coverage, 1)} of usage-weighted KPI consumption.`));
}

/* ------------------------------------------------------------------- gaps */
async function renderGaps() {
  if (!(await ensureRun())) { emptyView('#view-gaps'); return; }
  const data = await api(`/api/runs/${state.runId}/gaps`);
  const totalQuarantine = (data.quarantine || []).reduce((t, r) => t + r.rows_affected, 0);
  const tiles = $('#gap-tiles');
  tiles.innerHTML = '';
  tiles.append(
    tile(fmt.num(totalQuarantine), 'unresolved lineage rows'),
    tile(fmt.num((data.columns_without_definition || []).length), 'columns with no term', 'first 200 shown'),
    tile(fmt.num((data.metrics_without_steward || []).length), 'metrics with no steward'));
  fillTable($('#gap-quarantine'), [
    { label: 'Reason code', render: r => el('strong', {}, r.reason_code) },
    { label: 'Rows', num: true, render: r => fmt.num(r.rows_affected) },
  ], data.quarantine || []);
  fillTable($('#gap-stewards'), [
    { label: 'Metric', render: r => r.canonical_name },
    { label: 'Domain', render: r => r.domain },
  ], data.metrics_without_steward || []);
  fillTable($('#gap-columns'), [
    { label: 'Column', render: r => el('code', {}, r.column_fqn) },
    { label: 'Domain', render: r => r.domain },
  ], data.columns_without_definition || []);
}

/* -------------------------------------------------------------------- ask */
async function renderAsk() {
  if (!(await ensureRun())) { emptyView('#view-ask'); return; }
  if ($('#chat-suggestions').childElementCount) return;
  const data = await api('/api/semantic-view');
  const host = $('#chat-suggestions');
  data.suggested_questions.forEach(question =>
    host.append(el('button', { class: 'ghost', onclick: () => askQuestion(question) }, question)));
}

$('#chat-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const input = $('#chat-input');
  const question = input.value.trim();
  if (!question) return;
  input.value = '';
  askQuestion(question);
});

async function askQuestion(question) {
  const log = $('#chat-log');
  log.append(el('div', { class: 'bubble user' }, question));
  const pending = el('div', { class: 'bubble agent' }, el('span', { class: 'spinner' }), ' thinking...');
  log.append(pending);
  log.scrollTop = log.scrollHeight;
  try {
    const answer = await api('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, run_id: state.runId }),
    });
    pending.remove();
    const bubble = el('div', { class: 'bubble agent' }, el('div', {}, answer.answer));
    if ((answer.citations || []).length) {
      bubble.append(el('div', { class: 'citations' },
        answer.citations.slice(0, 10).map(citation =>
          el('span', { class: 'chip', title: citation.label || '' },
            `${citation.type}: ${citation.id}`))));
    }
    bubble.append(el('div', { class: 'small muted', style: 'margin-top:6px' },
      `query ${answer.query_used || answer.intent} · ${answer.bound_to}`));
    if ((answer.rows || []).length) {
      bubble.append(el('details', { class: 'raw' },
        el('summary', {}, `${answer.rows.length} rows`),
        el('pre', {}, JSON.stringify(answer.rows.slice(0, 25), null, 2))));
    }
    if ((answer.followups || []).length) {
      bubble.append(el('div', { class: 'suggestions', style: 'margin-top:8px' },
        answer.followups.map(f => el('button', { class: 'ghost sm', onclick: () => askQuestion(f) }, f))));
    }
    log.append(bubble);
    log.scrollTop = log.scrollHeight;
  } catch (error) {
    pending.remove();
    log.append(el('div', { class: 'bubble agent' }, el('span', { class: 'banner error' }, error.message)));
  }
}

/* ------------------------------------------------------------------- runs */
async function renderRuns() {
  const data = await api('/api/runs');
  fillTable($('#runs-table'), [
    { label: 'Run', render: r => el('div', {}, el('strong', {}, r.run_id),
        el('div', { class: 'small muted' }, `${r.mode} · ${r.industry || 'manual'} · ${r.catalog}`)) },
    { label: 'As-of', render: r => fmt.date(r.as_of_date) },
    { label: 'Started', render: r => r.started_at },
    { label: 'Weights', render: r => r.weight_version },
    { label: 'Parser', render: r => r.parser_version },
    { label: 'Published', render: r => chip(r.published ? 'yes' : 'held', r.published ? 'good' : 'warning') },
    { label: 'Gates', render: r => el('div', { class: 'row' }, (r.quality_gates || []).map(g =>
        chip(g.gate, g.passed ? 'good' : 'critical'))) },
  ], data.runs || [], { onRow: (row) => selectRun(row.run_id) });

  if (state.runId) await renderFeedback();
}

async function selectRun(runId) {
  state.runId = runId;
  state.candidates = [];
  state.portfolio = null;
  const data = await api(`/api/runs/${runId}`);
  state.run = data.run;
  $('#run-pill').hidden = false;
  $('#run-pill-text').textContent = `${data.run.industry || data.run.mode} · ${runId}`;
  const host = $('#run-detail');
  host.innerHTML = '';
  host.append(el('h2', {}, `Run ${runId}`));
  host.append(el('div', { class: 'row' },
    chip(data.run.mode), chip(data.run.industry || 'manual'), chip(data.run.catalog),
    chip(data.run.generation_id || 'no generation id'),
    chip(data.run.synthetic ? 'synthetic' : 'real extract', data.run.synthetic ? 'warning' : 'good')));
  host.append(el('details', { class: 'raw', style: 'margin-top:10px' },
    el('summary', {}, 'Run statistics, gates and agent log'),
    el('pre', {}, JSON.stringify({ stats: data.run.stats, quality_gates: data.run.quality_gates,
      agents: data.run.agent_log, warnings: data.run.warnings }, null, 2))));
  await renderFeedback();
  flash(`Run ${runId} selected.`, 'ok', 4000);
}

async function renderFeedback() {
  const host = $('#feedback-panel');
  host.innerHTML = '';
  try {
    const data = await api(`/api/runs/${state.runId}/feedback`);
    host.append(el('h2', {}, 'Feedback loop'));
    host.append(el('p', { class: 'secondary small' },
      'Reviewer decisions are the training signal. Weight changes never re-score accepted candidates; they apply to the next run.'));
    host.append(el('div', { class: 'banner info' }, data.weights.note || ''));
    if (data.weights.sample_size) {
      host.append(el('div', { class: 'tiles', style: 'margin:10px 0' },
        tile(fmt.num(data.weights.sample_size), 'decisions in sample'),
        tile(fmt.num(data.weights.accepted), 'accepted'),
        tile(fmt.num(data.weights.rejected), 'rejected'),
        tile(fmt.num(data.weights.in_sample_accuracy, 2), 'in-sample accuracy')));
    }
    host.append(el('h3', {}, 'Archetype rules'));
    host.append(tableCard([
      { label: 'Archetype', render: r => r.archetype },
      { label: 'Candidates', num: true, render: r => fmt.num(r.candidates) },
      { label: 'Overrides', num: true, render: r => fmt.num(r.overrides) },
      { label: 'Override rate', num: true, render: r => fmt.pct(r.override_rate, 1) },
      { label: 'Action', render: r => r.action },
    ], data.archetype_rules || []));
    host.append(el('h3', {}, 'Clustering resolution'));
    host.append(tableCard([
      { label: 'Domain', render: r => r.domain },
      { label: 'Merges', num: true, render: r => r.merges },
      { label: 'Splits', num: true, render: r => r.splits },
      { label: 'Proposed resolution', num: true, render: r => r.proposed_resolution },
      { label: 'Rationale', render: r => el('span', { class: 'small secondary' }, r.rationale) },
    ], data.clustering_resolution || []));
    const approver = el('input', { placeholder: 'council member name' });
    host.append(el('div', { class: 'row', style: 'margin-top:12px' },
      el('label', { class: 'field' }, 'Approve proposed weights as', approver),
      el('button', {
        onclick: async () => {
          try {
            const response = await api('/api/weights/approve', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ approver: approver.value.trim() }),
            });
            flash(`Weight version ${response.weights.weight_version} approved. It applies to the next run.`, 'ok');
            renderFeedback();
          } catch (error) { flash(error.message, 'error'); }
        },
      }, 'Approve')));
  } catch (error) {
    host.append(el('div', { class: 'banner info' }, 'Feedback becomes available once a run exists.'));
  }
}

/* ------------------------------------------------------------------- boot */
(async function boot() {
  try {
    paintIdentity();
    await refreshPrincipal();
    const [schemas, reasons] = await Promise.all([
      api('/api/v1/schemas'), api('/api/v1/reason-codes')]);
    state.reasonCodes = reasons.reason_codes;
    await loadIndustries();
    state.schemas = schemas.schemas;
    const today = new Date().toISOString().slice(0, 10);
    $('#auto-asof').value = today;
    $('#manual-asof').value = today;
    const runs = await api('/api/v1/runs');
    if (runs.runs.length) {
      state.runId = runs.runs[0].run_id;
      state.run = runs.runs[0];
      $('#run-pill').hidden = false;
      $('#run-pill-text').textContent = `${state.run.industry || state.run.mode} · ${state.runId}`;
    }
  } catch (error) {
    flash(`Could not reach the engine: ${error.message}`, 'error', 0);
  }
})();
