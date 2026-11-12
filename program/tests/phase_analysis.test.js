'use strict';

// Synthetic optical fixtures only: passing these tests is not real-image validation.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = path.join(__dirname, '../static/phase_analysis.js');
function analyze(image, options) {
  assert.ok(fs.existsSync(source), 'The local optical analysis module must exist');
  return require(source).analyzeFrame(image, options);
}

function frame(width = 600, height = 300, value = 210) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < data.length; i += 4) data.set([value, value, value, 255], i);
  return { width, height, data };
}
function rect(image, x, y, w, h, rgb) {
  for (let yy = y; yy < y + h; yy++) for (let xx = x; xx < x + w; xx++) {
    image.data.set([...rgb, 255], (yy * image.width + xx) * 4);
  }
}
function fixtures({ count = 5, layer = false, interfaceY = 160, lowContrast = false } = {}) {
  const image = frame();
  const rois = [];
  for (let i = 0; i < count; i++) {
    const x = 35 + 110 * i;
    const edge = lowContrast ? 205 : 65;
    rect(image, x, 30, 60, 240, [edge, edge, edge]);
    const upper = lowContrast ? [207, 207, 207] : [155, 175, 190];
    const lower = lowContrast ? [204, 204, 204] : [115, 145, 155];
    rect(image, x + 3, 34, 54, 232, upper);
    if (layer) rect(image, x + 3, interfaceY, 54, 266 - interfaceY, lower);
    rois.push({ sample: 5 - i, x, y: 30, width: 60, height: 240 });
  }
  return { image, rois };
}
function allUncertain(result) {
  assert.ok(result.bottles.length === 0 || result.bottles.every(b => b.classification === 'uncertain'));
}

test('empty and uniform frames never invent bottles or single-phase observations', () => {
  for (const value of [0, 128, 255]) {
    const result = analyze(frame(600, 300, value));
    assert.deepEqual(result.rois, []);
    assert.deepEqual(result.bottles, []);
    assert.notEqual(result.detection.status, 'ok');
    assert.equal(result.validated, false);
  }
});

test('random image noise is not assigned five bottle numbers', () => {
  const image = frame();
  let seed = 41;
  for (let i = 0; i < image.data.length; i += 4) {
    seed = (1664525 * seed + 1013904223) >>> 0;
    image.data[i] = image.data[i + 1] = image.data[i + 2] = seed >>> 24;
  }
  const result = analyze(image);
  assert.notEqual(result.detection.status, 'ok');
  allUncertain(result);
});

test('five bounded upright bottle candidates are numbered from right to left', () => {
  const result = analyze(fixtures().image);
  assert.equal(result.detection.status, 'ok');
  assert.equal(result.detection.source, 'auto');
  assert.equal(result.rois.length, 5);
  assert.deepEqual(result.rois.map(r => r.sample), [1, 2, 3, 4, 5]);
  assert.ok(result.rois[0].x > 450 && result.rois[4].x < 50);
  for (let i = 1; i < 5; i++) assert.ok(result.rois[i - 1].x > result.rois[i].x);
});

test('one or four bottles leave assignments uncertain instead of filling missing boxes', () => {
  for (const count of [1, 4]) {
    const result = analyze(fixtures({ count }).image);
    assert.notEqual(result.detection.status, 'ok');
    assert.equal(result.detection.candidate_count, count);
    assert.deepEqual(result.rois, []);
    assert.deepEqual(result.bottles, []);
  }
});

test('manually bounded clear liquid without an internal interface is only an optical single candidate', () => {
  const { image, rois } = fixtures();
  const result = analyze(image, { rois });
  assert.equal(result.detection.source, 'manual');
  assert.equal(result.bottles.length, 5);
  assert.ok(result.bottles.every(b => b.classification === 'single'));
  assert.ok(result.bottles.every(b => b.interface_y === null));
  assert.equal(result.validated, false);
  assert.ok(result.method && result.settings);
});

test('a persistent horizontal color transition inside the liquid is a two-phase candidate', () => {
  const { image, rois } = fixtures({ layer: true });
  const result = analyze(image, { rois });
  assert.ok(result.bottles.every(b => b.classification === 'two_phase'));
  assert.ok(result.bottles.every(b => Math.abs(b.interface_y - 160) <= 4));
});

test('valid manual geometry does not turn a blank photograph into a single-phase observation', () => {
  const { rois } = fixtures();
  const result = analyze(frame(), { rois });
  assert.equal(result.bottles.length, 5);
  allUncertain(result);
});

test('insufficient optical contrast remains uncertain even if ROIs are supplied', () => {
  const { image, rois } = fixtures({ layer: true, lowContrast: true });
  allUncertain(analyze(image, { rois }));
});

test('interior glare prevents a phase declaration', () => {
  const { image, rois } = fixtures({ layer: true });
  for (const r of rois) rect(image, r.x + 18, 105, 24, 110, [255, 255, 255]);
  const result = analyze(image, { rois });
  allUncertain(result);
  assert.ok(result.bottles.every(b => b.quality.flags.includes('glare')));
});

test('many small bright bubble-like rings prevent a phase declaration', () => {
  const { image, rois } = fixtures({ layer: true });
  for (const r of rois) for (let y = 100; y < 225; y += 12) for (let x = r.x + 12; x < r.x + 48; x += 12) {
    rect(image, x, y, 8, 8, [230, 230, 230]);
    rect(image, x + 2, y + 2, 4, 4, [120, 140, 150]);
  }
  const result = analyze(image, { rois });
  allUncertain(result);
  assert.ok(result.bottles.every(b => b.quality.flags.includes('texture_or_bubbles')));
});

test('strongly blurred bottle edges do not support a phase declaration', () => {
  const { image, rois } = fixtures({ layer: true });
  const original = new Uint8ClampedArray(image.data);
  for (let y = 0; y < image.height; y++) for (let x = 14; x < image.width - 14; x++) {
    for (let c = 0; c < 3; c++) {
      let sum = 0;
      for (let dx = -14; dx <= 14; dx++) sum += original[(y * image.width + x + dx) * 4 + c];
      image.data[(y * image.width + x) * 4 + c] = sum / 29;
    }
  }
  const result = analyze(image, { rois });
  allUncertain(result);
  assert.ok(result.bottles.every(b => b.quality.flags.includes('blur_or_weak_edges')));
});

test('cap and near-base lines are excluded from the liquid search area', () => {
  const { image, rois } = fixtures();
  for (const r of rois) {
    rect(image, r.x + 3, 55, 54, 4, [80, 80, 80]);
    rect(image, r.x + 3, 255, 54, 4, [80, 80, 80]);
  }
  assert.ok(analyze(image, { rois }).bottles.every(b => b.classification === 'single'));
});

test('invalid, overlapping, empty or duplicate manual ROIs are rejected without automatic fallback', () => {
  const { image, rois } = fixtures();
  const variants = [[], rois.map(r => ({ ...r, sample: 1 })),
    rois.map(r => ({ ...r, x: 10 })), rois.map(r => ({ ...r, width: 600 })),
    rois.map(r => ({ ...r, y: -1 })), rois.map(r => ({ ...r, x: r.x + 0.5 }))];
  for (const invalid of variants) {
    const result = analyze(image, { rois: invalid });
    assert.equal(result.detection.status, 'invalid');
    assert.deepEqual(result.rois, []);
    assert.deepEqual(result.bottles, []);
  }
});

test('manual labels must preserve the physical right-to-left sample order', () => {
  const { image, rois } = fixtures();
  const result = analyze(image, { rois: rois.map(r => ({ ...r, sample: 6 - r.sample })) });
  assert.equal(result.detection.status, 'invalid');
});

test('an interface shifting between frames is uncertain; a stable second frame remains a candidate', () => {
  const a = fixtures({ layer: true, interfaceY: 145 });
  const previous = analyze(a.image, { rois: a.rois });
  const stable = analyze(a.image, { rois: a.rois, previous });
  assert.ok(stable.bottles.every(b => b.classification === 'two_phase'));
  const b = fixtures({ layer: true, interfaceY: 190 });
  const moving = analyze(b.image, { rois: b.rois, previous });
  allUncertain(moving);
  assert.ok(moving.bottles.every(b => b.quality.flags.includes('unstable_frame')));
});

test('invalid, transparent and undersized images cannot become phase observations', () => {
  for (const image of [null, {}, frame(5, 5), { width: 600, height: 300, data: new Uint8ClampedArray(2) },
    { width: 600, height: 300, data: new Uint8ClampedArray(600 * 300 * 4) }]) {
    const result = analyze(image);
    assert.notEqual(result.detection.status, 'ok');
    allUncertain(result);
  }
});

test('the result is JSON serializable and analysis does not mutate input pixels or ROIs', () => {
  const { image, rois } = fixtures({ layer: true });
  const pixels = new Uint8ClampedArray(image.data);
  const geometry = structuredClone(rois);
  const result = analyze(image, { rois });
  assert.deepEqual(JSON.parse(JSON.stringify(result)), result);
  assert.deepEqual(image.data, pixels);
  assert.deepEqual(rois, geometry);
});

test('a faint internal transition stays uncertain despite sharp bottle edges', () => {
  const { image, rois } = fixtures();
  for (const r of rois) rect(image, r.x + 3, 160, 54, 106, [151, 171, 186]);
  const result = analyze(image, { rois });
  allUncertain(result);
  assert.ok(result.bottles.every(b => b.quality.flags.includes('weak_or_diffuse_interface')));
});

test('a thin horizontal printed line is not mistaken for two broad phases', () => {
  const { image, rois } = fixtures();
  for (const r of rois) rect(image, r.x + 3, 160, 54, 2, [65, 65, 65]);
  assert.ok(analyze(image, { rois }).bottles.every(b => b.classification !== 'two_phase'));
});

test('a smooth lighting gradient is uncertain rather than a sharp phase boundary', () => {
  const { image, rois } = fixtures();
  for (const r of rois) for (let y = 34; y < 266; y++) {
    const shade = 210 - Math.round((y - 34) * 0.55);
    rect(image, r.x + 3, y, 54, 1, [shade, shade, shade]);
  }
  allUncertain(analyze(image, { rois }));
});

test('pixels outside manually specified bottles cannot introduce an internal interface', () => {
  const { image, rois } = fixtures();
  for (let y = 0; y < image.height; y++) for (let x = 0; x < image.width; x++) {
    if (!rois.some(r => x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height)) {
      image.data.set(y < 160 ? [20, 20, 20, 255] : [250, 250, 250, 255], (y * image.width + x) * 4);
    }
  }
  assert.ok(analyze(image, { rois }).bottles.every(b => b.classification === 'single'));
});

test('a supplied but unusable previous frame cannot silently count as stability checked', () => {
  const { image, rois } = fixtures({ layer: true });
  const result = analyze(image, { rois, previous: { rois, bottles: [] } });
  allUncertain(result);
  assert.ok(result.bottles.every(b => b.quality.flags.includes('previous_frame_unusable')));
});

test('browser script exports the same API without accessing camera or network', () => {
  const vm = require('node:vm');
  const sandbox = {};
  Object.defineProperty(sandbox, 'navigator', { get() { throw new Error('Camera access is forbidden during analysis'); } });
  sandbox.fetch = () => { throw new Error('Network access is forbidden during analysis'); };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(source, 'utf8'), sandbox);
  const result = sandbox.PhaseAnalysis.analyzeFrame(frame());
  assert.equal(result.validated, false);
  assert.equal(result.rois.length, 0);
});

test('manual sample 1 and 3 can be analyzed without inventing the missing bottles', () => {
  const { image, rois } = fixtures({ layer: true });
  const subset = rois.filter(r => r.sample === 1 || r.sample === 3);
  const result = analyze(image, { rois: subset });
  assert.equal(result.detection.status, 'partial');
  assert.equal(result.detection.source, 'manual');
  assert.equal(result.detection.candidate_count, 2);
  assert.deepEqual(result.detection.missing_samples, [2, 4, 5]);
  assert.deepEqual(result.rois, [subset[1], subset[0]]);
  assert.deepEqual(result.bottles.map(b => b.sample), [1, 3]);
  assert.ok(result.bottles.every(b => b.classification === 'two_phase'));
  assert.ok(result.detection.reason.length > 0);
  assert.equal(result.validated, false);
});

test('one through four explicitly labeled manual bottles remain partial; five remain complete', () => {
  const { image, rois } = fixtures();
  const samples = [1, 2, 3, 4, 5];
  for (let count = 1; count <= 5; count++) {
    const subset = rois.slice(0, count);
    const result = analyze(image, { rois: subset });
    assert.equal(result.detection.status, count === 5 ? 'ok' : 'partial');
    assert.equal(result.detection.candidate_count, count);
    assert.equal(result.rois.length, count);
    assert.equal(result.bottles.length, count);
    assert.deepEqual(result.detection.missing_samples, samples.slice(0, 5 - count));
  }
});

test('partial manual labels reject zero, six, noninteger, duplicate or reversed sample IDs', () => {
  const { image, rois } = fixtures();
  const right = rois.find(r => r.sample === 1), left = rois.find(r => r.sample === 3);
  const invalid = [
    [{ ...right, sample: 0 }], [{ ...right, sample: 6 }], [{ ...right, sample: 1.5 }],
    [right, { ...left, sample: 1 }], [{ ...right, sample: 3 }, { ...left, sample: 1 }]
  ];
  for (const subset of invalid) {
    const result = analyze(image, { rois: subset });
    assert.equal(result.detection.status, 'invalid');
    assert.deepEqual(result.rois, []);
    assert.deepEqual(result.bottles, []);
  }
});

test('partial manual ROIs still reject out-of-bounds, overlap and invalid dimensions', () => {
  const { image, rois } = fixtures();
  const right = rois.find(r => r.sample === 1), left = rois.find(r => r.sample === 3);
  const invalid = [[{ ...right, x: 599 }], [{ ...right, y: -1 }], [{ ...right, width: 19 }],
    [{ ...right, height: 39 }], [{ ...right, x: right.x + 0.5 }],
    [right, { ...left, x: right.x - 10 }]];
  for (const subset of invalid) {
    const result = analyze(image, { rois: subset });
    assert.equal(result.detection.status, 'invalid');
    assert.deepEqual(result.rois, []);
  }
});
