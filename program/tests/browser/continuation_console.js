// Isolated continuation controls: no physical camera, pump or serial connection.
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
          budgets: {
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
  assert.equal(await page.locator('#stock-form [name=percent]').inputValue(), '30');
  assert.equal(await page.locator('#stock-form [name=density_g_ml]').inputValue(), '');
  assert((await page.locator('#stock-composition-note').innerText()).includes('26.324%'));
  await page.locator('#stock-form button[type=submit]').click();
  await until(() => calls.some((c) => c.url === '/api/lab/stock'));
  const stock = calls.find((c) => c.url === '/api/lab/stock').body;
  assert(Math.abs(stock.mass_fraction - (0.3 * 258.06) / 294.1) < 1e-12);
  assert.equal(stock.reagent_mass_fraction, 0.3);
  assert.equal(stock.density_g_ml, null);
  assert.equal(stock.density_temperature_c, null);
  await page.getByRole('button', { name: '实验设计', exact: true }).click();
  await page.locator('#composition-form [name=mode]').selectOption('continuation');
  await page.locator('#composition-form [name=bottle]').selectOption('1');
  await page.locator('#composition-form [name=peg]').fill('12');
  await page.locator('#composition-form [name=salt]').fill('6');
  await page.locator('#composition-form button[type=submit]').click();
  await until(() => calls.some((c) => c.url === '/api/lab/continuation_preview'));
  assert.deepEqual(calls.find((c) => c.url.endsWith('continuation_preview')).body, {
    project_id: 'project-1',
    bottle: 1,
    peg_fraction: 0.12,
    salt_fraction: 0.06,
  });
  await page.waitForFunction(() =>
    document.querySelector('#composition-preview').textContent.includes('现有瓶内'),
  );
  assert((await page.locator('#composition-preview').innerText()).includes('1.2'));
  assert(await page.locator('#enqueue-preview').isHidden());
  await page.locator('#continuation-single-create').click();
  await until(() => state.continuation_runs.length === 1);
  const single = calls.find((c) => c.url.endsWith('continuation_run')).body;
  assert.deepEqual(single.bottles, [1]);
  assert.equal(single.round_budget, 1);
  assert.equal(single.peg_min, 0.12);
  assert.equal(single.peg_max, 0.12);
  assert(!calls.some((c) => c.url.endsWith('continuation_start')));
  await page.locator('#continuation-run-form [name=bottles]').fill('1,2');
  await page.locator('#continuation-run-form [name=time_budget_min]').fill('30');
  await page.locator('#continuation-run-form button[type=submit]').click();
  await until(() => state.continuation_runs.length === 2);
  assert.equal(
    calls.filter((c) => c.url.endsWith('continuation_run')).at(-1).body.time_budget_s,
    1800,
  );
  assert(!calls.some((c) => c.url.endsWith('continuation_start')));
  await page.locator('#continuation-start').click();
  await until(() => calls.some((c) => c.url.endsWith('continuation_start')));
  await page.getByRole('button', { name: '运行监控', exact: true }).click();
  await page.waitForFunction(() =>
    document.querySelector('#continuation-monitor').textContent.includes('等待有效人工复核'),
  );
  assert((await page.locator('#inventory-table').innerText()).includes('估计累计'));
  assert((await page.locator('#continuation-monitor').innerText()).includes('含等待人工复核'));
  await page.locator('#continuation-pause').click();
  await until(() => state.continuation_runs[1].status === 'paused');
  assert(calls.find((c) => c.url.endsWith('continuation_pause')).body.request_id);
  await page.locator('#continuation-resume').click();
  await until(() => calls.some((c) => c.url.endsWith('continuation_resume')));
  assert.deepEqual(errors, []);
  console.log(
    'PASS continuation preview, separate run/start, mass and wall-clock budget, inventory/wait reasons and pause/resume; isolated devices only',
  );
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
