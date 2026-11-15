// Automatic observation policy and provenance: isolated continuation controls: no physical camera, pump or serial connection.
'use strict';
const http = require('http'),
  fs = require('fs'),
  path = require('path'),
  assert = require('assert');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = path.resolve(__dirname, '../..'),
  calls = [];
const inventory = {
  known: true,
  revision: 3,
  total_mass_g: 1,
  peg_mass_g: 0.1,
  salt_mass_g: 0.05,
  water_mass_g: 0.85,
  salt_port: 13,
  sample_id: 'parent-1',
  calibration_valid: true,
};
const project = {
  id: 'project-1',
  name: '同瓶研究',
  salt_port: 13,
  total_mass_g: 1,
  safe_mass_limit_g: 2,
  temperature_c: 25,
  stocks: [],
  photo_offsets_s: [60, 180],
  min_settle_s: 60,
  max_observe_s: 600,
};
const state = {
  projects: [project],
  stocks: [
    {
      port: 10,
      name: '二水柠檬酸三钠',
      solute: 'sodium_citrate',
      mass_fraction: (0.3 * 258.06) / 294.1,
      reagent_mass_fraction: 0.3,
      reagent_form: 'dihydrate',
      density_g_ml: null,
      density_basis: 'unknown',
      temperature_c: 25,
    },
  ],
  density_audit: [
    {
      port: 10,
      name: '二水柠檬酸三钠',
      configured_density_g_ml: null,
      status_label: '未测',
      issues: ['合成测试证据'],
      reference: {
        density_g_ml: 1.17,
        mass_fraction: (0.3 * 258.06) / 294.1,
        temperature_c: 25,
        note: 'synthetic reference only',
      },
    },
  ],
  calibrations: [],
  rounds: [],
  samples: [],
  bottles: [
    { bottle: 1, state: 'occupied', sample_id: 'parent-1', inventory },
    {
      bottle: 2,
      state: 'free',
      sample_id: null,
      inventory: {
        ...inventory,
        total_mass_g: 0,
        peg_mass_g: 0,
        salt_mass_g: 0,
        water_mass_g: 0,
        sample_id: null,
      },
    },
  ],
  photo_jobs: [],
  camera: { mapping_version: 0 },
  start_previews: {},
  continuation_runs: [],
  running: false,
};
const hardware = {
  connected: false,
  busy: false,
  state: 'DISCONNECTED',
  config: JSON.parse(fs.readFileSync(path.join(base, 'config.default.json'))),
  logs: [],
  samples: {},
  sources: {},
  imaging: {},
};
let server, browser;
const reply = (res, value) => {
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(value));
};
async function until(fn) {
  for (let n = 0; n < 100; n++) {
    if (await fn()) return;
    await new Promise((r) => setTimeout(r, 50));
  }
  throw Error('Timed out');
}
(async () => {
  server = http.createServer(async (req, res) => {
    if (req.url.startsWith('/api/')) {
      let raw = '';
      for await (const part of req) raw += part;
      const body = raw ? JSON.parse(raw) : {};
      if (req.method === 'POST') calls.push({ url: req.url, body });
      if (req.url === '/api/token') return reply(res, { token: 'test' });
      if (req.url === '/api/state') return reply(res, hardware);
      if (req.url === '/api/lab/state') return reply(res, state);
      if (req.url === '/api/serial-ports') return reply(res, { ports: [] });
      const op = req.url.split('/').pop();
      let value = { ok: true };
      if (op === 'continuation_preview')
        value.preview = {
          kind: 'continuation',
          executable: true,
          issues: [],
          warnings: [],
          initial_inventory: inventory,
          target: { peg_fraction: 0.12, salt_fraction: 0.06 },
          theoretical_additions_g: { 14: 0.11, 13: 0.055, 15: 0.035 },
          estimated: { total_mass_g: 1.2, peg_fraction: 0.12, salt_fraction: 0.06 },
          added_mass_g: 0.2,
          estimated_duration_s: 80,
          plan: { items: [] },
        };
      if (op === 'continuation_run') {
        value.run = {
          id: 'run-' + (state.continuation_runs.length + 1),
          project_id: body.project_id,
          bottles: body.bottles,
          status: 'draft',
          observation_policy: body.observation_policy,
          min_initial_mass_g: body.min_initial_mass_g,
          reserve_mass_g: body.reserve_mass_g,
          budgets: {
            source_mass_budget_g: body.source_mass_budget_g,
            round_budget: body.round_budget,
            added_mass_budget_g: body.added_mass_budget_g,
            time_budget_s: body.time_budget_s,
          },
          used: { rounds: 0, added_mass_g: 0 },
          remaining: {
            rounds: body.round_budget,
            added_mass_g: body.added_mass_budget_g,
            time_s: body.time_budget_s,
          },
          rounds: [],
          sample_ids: [],
          reason: '等待明确启动',
        };
        state.continuation_runs.push(value.run);
      }
      if (['continuation_start', 'continuation_pause', 'continuation_resume'].includes(op)) {
        const run = state.continuation_runs.find((r) => r.id === body.run_id);
        run.status = op === 'continuation_pause' ? 'paused' : 'waiting_review';
        run.reason =
          op === 'continuation_pause' ? '人工暂停' : '1号瓶等待有效人工复核；2号瓶可继续';
      }
      return reply(res, value);
    }
    const file = path.join(base, 'static', req.url === '/' ? 'index.html' : req.url.slice(1));
    fs.readFile(file, (e, data) => {
      res.writeHead(e ? 404 : 200, {
        'Content-Type': file.endsWith('.js')
          ? 'text/javascript'
          : file.endsWith('.css')
            ? 'text/css'
            : 'text/html; charset=utf-8',
      });
      res.end(data);
    });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('http://127.0.0.1:' + server.address().port);
  await page.waitForFunction(
    () => document.querySelector('#stock-form [name=reagent_form]').value === 'dihydrate',
  );
  await page.getByRole('button', { name: '实验设计', exact: true }).click();
  const field = (name) => page.locator('#continuation-run-form [name=' + name + ']');
  assert.equal(await field('observation_mode').inputValue(), 'human');
  await page.locator('#continuation-run-form button[type=submit]').click();
  await until(() => state.continuation_runs.length === 1);
  const human = calls.filter(c => c.url.endsWith('continuation_run')).at(-1).body;
  assert.deepEqual(human.observation_policy, {
    mode: 'human', max_auto_retries: 2, retry_interval_s: 60, stable_frames: 2,
    min_frame_spacing_s: 30, max_interface_shift_fraction: .03, min_phase_fraction: .1,
    single_phase_validated: false, single_phase_evidence: '',
  });
  assert.equal(human.min_initial_mass_g, project.total_mass_g);
  assert.equal(human.reserve_mass_g, .2);
  assert(!('source_mass_budget_g' in human));
  await field('observation_mode').selectOption('automatic');
  await field('source_budget_peg_g').fill('3');
  await field('source_budget_salt_g').fill('2');
  await field('source_budget_water_g').fill('4');
  await field('min_initial_mass_g').fill('1.25');
  await field('max_auto_retries').fill('3');
  await field('single_phase_validated').selectOption('true');
  await field('single_phase_evidence').fill('validated criterion TEST ONLY');
  await page.locator('#continuation-run-form button[type=submit]').click();
  await until(() => state.continuation_runs.length === 2);
  const automatic = calls.filter(c => c.url.endsWith('continuation_run')).at(-1).body;
  assert.equal(automatic.observation_policy.mode, 'automatic');
  assert.equal(automatic.observation_policy.max_auto_retries, 3);
  assert.equal(automatic.observation_policy.single_phase_validated, true);
  assert.equal(automatic.observation_policy.single_phase_evidence, 'validated criterion TEST ONLY');
  assert.equal(automatic.min_initial_mass_g, 1.25);
  assert.deepEqual(automatic.source_mass_budget_g, {14: 3, 13: 2, 15: 4});
  assert(!calls.some(c => c.url.endsWith('continuation_start')));
  const run = state.continuation_runs.at(-1);
  run.status = 'waiting_observation';
  state.samples.push({
    id: 'sample-auto', project_id: project.id, bottle: 1, generation: 1,
    execution_state: 'observation_complete', review_state: 'two_phase',
    label: {source: 'automatic_optical'}, frames: [],
    auto_observation: {status: 'unknown', reason_codes: ['single_phase_criterion_unvalidated', 'future_unknown_reason'],
      evidence_frame_ids: ['frame-test-1', 'frame-test-2'], retries_used: 3},
  });
  state.bottles[0].sample_id = 'sample-auto';
  await page.getByRole('button', {name: '运行监控', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('#continuation-monitor').textContent.includes('等待自动观察'));
  const monitor = await page.locator('#continuation-monitor').innerText();
  assert(monitor.includes('自动光学候选'));
  assert(monitor.includes('0.03'));
  assert(monitor.includes('1.25'));
  assert(monitor.includes('母液预算'));
  await page.waitForFunction(() => document.querySelector('#page-monitor').textContent.includes('单相判据未经独立验证'));
  assert((await page.locator('#page-monitor').innerText()).includes('future_unknown_reason'));
  assert((await page.locator('#page-monitor').innerText()).includes('3'));
  await page.getByRole('button', {name: '图像判相与复核', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('#auto-observation-summary').textContent.includes('frame-test-2'));
  const summary = await page.locator('#auto-observation-summary').innerText();
  assert(summary.includes('未知'));
  assert(summary.includes('automatic_optical'));
  assert(summary.includes('单相判据未经独立验证'));
  assert(summary.includes('future_unknown_reason'));
  assert(!summary.includes('single_phase_criterion_unvalidated'));
  assert(summary.includes('3'));
  // Historical human labels have no source field; preserve their provenance.
  delete state.samples[0].label.source;
  state.samples[0].auto_observation.status = 'human_reviewed';
  await page.waitForFunction(() => document.querySelector('#auto-observation-summary').textContent.includes('人工复核'));
  assert((await page.locator('#auto-observation-summary').innerText()).includes('(human)'));
  for (const [status, text] of [['pending', '等待证据'], ['candidate', '光学候选']]) {
    state.samples[0].auto_observation.status = status;
    await page.waitForFunction(text => document.querySelector('#auto-observation-summary').textContent.includes('自动观察：' + text), text);
  }
  for (const [status, text] of [['observation_complete', '观察完成'], ['exhausted', '研究已结束']]) {
    run.status = status;
    await page.waitForFunction(text => document.querySelector('#continuation-monitor').textContent.includes(text), text);
  }
  await page.locator('#review-form [name=classification]').selectOption('uncertain');
  await page.locator('#review-form button[type=submit]').click();
  await until(() => calls.some(c => c.url.endsWith('/review')));
  assert.equal(calls.find(c => c.url.endsWith('/review')).body.classification, 'uncertain');
  state.samples[0].mixed_at = new Date().toISOString();
  state.samples[0].status = 'observation_complete';
  await page.getByRole('button', {name: '数据与报告', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('#tie-line-sample').options.length > 0);
  await page.locator('#tie-line-sample').selectOption('sample-auto');
  for (const [name, value] of Object.entries({upper_peg: '20', upper_salt: '3', lower_peg: '4', lower_salt: '12', method: '独立分析测试', evidence: 'TEST-ONLY-ASSAY'}))
    await page.locator('#tie-line-form [name=' + name + ']').fill(value);
  await page.locator('#tie-line-form button[type=submit]').click();
  await until(() => calls.some(c => c.url.endsWith('/tie_line')));
  const assay = calls.find(c => c.url.endsWith('/tie_line')).body;
  assert.equal(assay.sample_id, 'sample-auto');
  assert.deepEqual(assay.upper, {peg_fraction: .2, salt_fraction: .03});
  assert.deepEqual(assay.lower, {peg_fraction: .04, salt_fraction: .12});
  assert.equal(assay.method, '独立分析测试');
  assert.equal(assay.evidence, 'TEST-ONLY-ASSAY');
  assert(assay.request_id);
  assert(!calls.some(c => c.url.endsWith('continuation_start')));
  assert.deepEqual(errors, []);
  console.log('PASS human defaults, automatic policy/budgets, frozen policy, bounded retry/unknown evidence and label provenance; no devices');

})()
  .catch((e) => {
    console.error(e);
    process.exitCode = 1;
  })
  .finally(async () => {
    if (browser) await browser.close();
    if (server) {
      server.closeAllConnections();
      await new Promise((r) => server.close(r));
    }
  });
