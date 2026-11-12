(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.PhaseAnalysis = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // These are engineering starting values, not experimentally calibrated limits.
  const SETTINGS = Object.freeze({
    max_detection_width: 640, max_detection_height: 480,
    vertical_edge_min: 14, minimum_vertical_span: 0.35,
    interior_left: 0.20, interior_right: 0.80,
    interior_top: 0.25, interior_bottom: 0.85,
    interface_contrast_min: 8, weak_interface_contrast: 3,
    maximum_glare_fraction: 0.03, maximum_texture_fraction: 0.12,
    minimum_edge_sharpness: 10, maximum_interface_shift_fraction: 0.06
  });
  const METHOD = 'local-optical-heuristic-v1';
  const rounded = v => Math.round(v * 1000) / 1000;
  const mean = a => a.reduce((s, v) => s + v, 0) / (a.length || 1);
  const difference = (a, b) => mean(a.map((v, i) => Math.abs(v - b[i])));
  const luminance = (data, i) => 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];

  function invalid(result, reason, status = 'invalid') {
    result.detection.status = status;
    result.detection.reason = reason;
    return result;
  }

  function validateRois(rois, width, height) {
    if (!Array.isArray(rois) || rois.length < 1 || rois.length > 5) return '请明确框选 1–5 个瓶体，并填写各自的实际瓶号。';
    const sorted = rois.slice().sort((a, b) => (a && a.sample) - (b && b.sample));
    const samples = new Set();
    for (let i = 0; i < sorted.length; i++) {
      const r = sorted[i];
      if (!r || !['sample', 'x', 'y', 'width', 'height'].every(k => Number.isInteger(r[k])) ||
          r.sample < 1 || r.sample > 5 || samples.has(r.sample) ||
          r.x < 0 || r.y < 0 || r.width < 20 || r.height < 40 ||
          r.x + r.width > width || r.y + r.height > height) {
        return '框选必须使用图像内的整数像素坐标，至少 20×40 像素，瓶号必须为不重复的 1–5 整数。';
      }
      samples.add(r.sample);
      if (i && sorted[i - 1].x + sorted[i - 1].width / 2 <= r.x + r.width / 2) {
        return '瓶号必须从未镜像画面的右侧向左依次为 1–5。';
      }
      for (let j = 0; j < i; j++) {
        const other = sorted[j];
        if (r.x < other.x + other.width && r.x + r.width > other.x &&
            r.y < other.y + other.height && r.y + r.height > other.y) return '瓶体框选不能相互重叠。';
      }
    }
    return null;
  }

  function detectBottles(image) {
    // Column edge runs + paired sides + top/base closure. No fixed five-way grid.
    const scale = Math.max(1, Math.ceil(image.width / SETTINGS.max_detection_width),
      Math.ceil(image.height / SETTINGS.max_detection_height));
    const width = Math.floor(image.width / scale), height = Math.floor(image.height / scale);
    const gray = new Float32Array(width * height);
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      gray[y * width + x] = luminance(image.data, (y * scale * image.width + x * scale) * 4);
    }
    const minRun = height * SETTINGS.minimum_vertical_span;
    const features = [];
    for (let x = 1; x < width; x++) {
      let start = -1, last = -1, bestStart = 0, bestEnd = -1;
      for (let y = 0; y <= height; y++) {
        const edge = y < height && Math.abs(gray[y * width + x] - gray[y * width + x - 1]) >= SETTINGS.vertical_edge_min;
        if (edge) { if (start < 0) start = y; last = y; }
        if ((!edge && y - last > 2) || y === height) {
          if (last - start > bestEnd - bestStart && start >= 0) { bestStart = start; bestEnd = last; }
          start = -1;
        }
      }
      if (bestEnd - bestStart >= minRun) features.push({ x, top: bestStart, bottom: bestEnd });
    }
    const sides = [];
    const clusterGap = Math.max(3, Math.round(width * 0.009));
    for (const f of features) {
      const last = sides[sides.length - 1];
      if (last && f.x - last.lastX <= clusterGap &&
          Math.abs(f.top - last.top) < height * 0.08 && Math.abs(f.bottom - last.bottom) < height * 0.08) {
        last.x = (last.x * last.count + f.x) / (last.count + 1);
        last.top = Math.min(last.top, f.top); last.bottom = Math.max(last.bottom, f.bottom);
        last.count++; last.lastX = f.x;
      } else sides.push({ ...f, lastX: f.x, count: 1 });
    }
    function closure(left, right, y) {
      let best = 0;
      const x0 = Math.ceil(left + (right - left) * 0.15), x1 = Math.floor(right - (right - left) * 0.15);
      for (let yy = Math.max(1, y - 5); yy <= Math.min(height - 1, y + 5); yy++) {
        let hits = 0;
        for (let x = x0; x <= x1; x++) if (Math.abs(gray[yy * width + x] - gray[(yy - 1) * width + x]) >= 10) hits++;
        best = Math.max(best, hits / Math.max(1, x1 - x0 + 1));
      }
      return best >= 0.65;
    }
    const candidates = [];
    for (let i = 0; i + 1 < sides.length; i++) {
      const left = sides[i], right = sides[i + 1];
      const span = right.x - left.x;
      const top = Math.max(left.top, right.top), bottom = Math.min(left.bottom, right.bottom);
      const tall = bottom - top;
      if (span < width * 0.025 || span > width * 0.23 || tall / span < 1.3 || tall / span > 9 ||
          tall < minRun || Math.abs(left.top - right.top) > height * 0.08 ||
          Math.abs(left.bottom - right.bottom) > height * 0.08 ||
          !closure(left.x, right.x, top) || !closure(left.x, right.x, bottom)) continue;
      const x = Math.max(0, Math.floor((left.x - 2) * scale));
      const y = Math.max(0, Math.floor(top * scale));
      candidates.push({ x, y, width: Math.min(image.width - x, Math.ceil((span + 4) * scale)),
        height: Math.min(image.height - y, Math.ceil((tall + 1) * scale)) });
      i++;
    }
    if (candidates.length !== 5) return { candidates, reason: `检测到 ${candidates.length} 个瓶体候选；需确认五个瓶体或手动框选。` };
    const widths = candidates.map(r => r.width), heights = candidates.map(r => r.height);
    const centers = candidates.map(r => r.y + r.height / 2);
    if (Math.max(...widths) / Math.min(...widths) > 1.7 || Math.max(...heights) / Math.min(...heights) > 1.5 ||
        Math.max(...centers) - Math.min(...centers) > mean(heights) * 0.25) {
      return { candidates, reason: '候选瓶体尺寸或排列不一致；请手动确认。' };
    }
    return { candidates, reason: null };
  }

  function inspectBottle(image, roi, previous, previousRoi) {
    const { width, data } = image;
    const x0 = roi.x + Math.ceil(roi.width * SETTINGS.interior_left);
    const x1 = roi.x + Math.floor(roi.width * SETTINGS.interior_right);
    const y0 = roi.y + Math.ceil(roi.height * SETTINGS.interior_top);
    const y1 = roi.y + Math.floor(roi.height * SETTINGS.interior_bottom);
    const stripes = 5, rows = [], signatures = Array(27).fill(0), signatureCounts = Array(9).fill(0);
    let glare = 0, dark = 0, texture = 0, opaque = 0, total = 0;
    let minimum = 255, maximum = 0, sharpness = 0;
    for (let x = roi.x + 1; x < roi.x + roi.width; x++) {
      if (x > roi.x + roi.width * 0.20 && x < roi.x + roi.width * 0.80) continue;
      let edgeSum = 0, n = 0;
      for (let y = y0; y < y1; y += 2) {
        const i = (y * width + x) * 4;
        const a = luminance(data, i), b = luminance(data, i - 4);
        edgeSum += Math.abs(a - b); n++;
        minimum = Math.min(minimum, a, b); maximum = Math.max(maximum, a, b);
      }
      sharpness = Math.max(sharpness, edgeSum / Math.max(1, n));
    }
    for (let y = y0; y < y1; y++) {
      const row = Array.from({ length: stripes }, () => [0, 0, 0]);
      const counts = Array(stripes).fill(0);
      for (let x = x0; x < x1; x++) {
        const i = (y * width + x) * 4;
        const s = Math.min(stripes - 1, Math.floor((x - x0) * stripes / (x1 - x0)));
        const cell = Math.min(2, Math.floor((y - y0) * 3 / (y1 - y0))) * 3 + Math.min(2, Math.floor((x - x0) * 3 / (x1 - x0)));
        for (let c = 0; c < 3; c++) { row[s][c] += data[i + c]; signatures[cell * 3 + c] += data[i + c]; }
        signatureCounts[cell]++; counts[s]++; total++;
        if (data[i + 3] >= 250) opaque++;
        if (Math.min(data[i], data[i + 1], data[i + 2]) >= 248) glare++;
        if (Math.max(data[i], data[i + 1], data[i + 2]) <= 12) dark++;
        const lum = luminance(data, i); minimum = Math.min(minimum, lum); maximum = Math.max(maximum, lum);
      }
      row.forEach((rgb, s) => rgb.forEach((_, c) => { rgb[c] /= Math.max(1, counts[s]); }));
      for (let x = x0; x < x1; x++) {
        const i = (y * width + x) * 4, s = Math.min(stripes - 1, Math.floor((x - x0) * stripes / (x1 - x0)));
        if ((Math.abs(data[i] - row[s][0]) + Math.abs(data[i + 1] - row[s][1]) + Math.abs(data[i + 2] - row[s][2])) / 3 > 18) texture++;
      }
      rows.push(row);
    }
    for (let i = 0; i < signatures.length; i++) signatures[i] = rounded(signatures[i] / Math.max(1, signatureCounts[Math.floor(i / 3)]));
    const flags = [];
    if (opaque / total < 0.99) flags.push('missing_pixels');
    if (glare / total > SETTINGS.maximum_glare_fraction) flags.push('glare');
    if (dark / total > 0.15) flags.push('underexposed');
    if (maximum - minimum < 18) flags.push('low_contrast');
    if (sharpness < SETTINGS.minimum_edge_sharpness) flags.push('blur_or_weak_edges');
    if (texture / total > SETTINGS.maximum_texture_fraction) flags.push('texture_or_bubbles');

    const band = Math.max(3, Math.min(10, Math.round(roi.height * 0.025)));
    function bandColor(start, end, stripe) {
      const rgb = [0, 0, 0];
      for (let y = start; y < end; y++) for (let c = 0; c < 3; c++) rgb[c] += rows[y][stripe][c] / (end - start);
      return rgb;
    }
    let best = null, strongestContrast = 0;
    const candidates = [];
    for (let y = band * 2; y < rows.length - band * 2; y++) {
      const contrasts = [], vectors = [];
      let supported = 0, plateau = 0;
      for (let s = 0; s < stripes; s++) {
        const above = bandColor(y - band, y, s), below = bandColor(y, y + band, s);
        const contrast = difference(above, below);
        contrasts.push(contrast); vectors.push(below.map((v, c) => v - above[c]));
        let localStep = 0;
        for (let yy = y - 2; yy <= y + 2; yy++) localStep = Math.max(localStep, difference(rows[yy][s], rows[yy - 1][s]));
        if (contrast >= SETTINGS.interface_contrast_min && localStep >= contrast * 0.35) supported++;
        if (difference(above, bandColor(y - band * 2, y - band, s)) <= Math.max(3, contrast * 0.25) &&
            difference(below, bandColor(y + band, y + band * 2, s)) <= Math.max(3, contrast * 0.25)) plateau++;
      }
      const contrast = mean(contrasts);
      strongestContrast = Math.max(strongestContrast, contrast);
      const reference = vectors[2];
      const agreement = vectors.filter(v => v.reduce((sum, value, c) => sum + value * reference[c], 0) > 0).length;
      if (supported >= 4 && plateau >= 4 && agreement >= 4) {
        const candidate = { y: y + y0, contrast, support: supported / stripes };
        candidates.push(candidate);
        if (!best || contrast > best.contrast) best = candidate;
      }
    }
    if (best && candidates.some(c => Math.abs(c.y - best.y) > band * 2)) flags.push('multiple_interfaces');
    if (!best && strongestContrast >= SETTINGS.weak_interface_contrast) flags.push('weak_or_diffuse_interface');
    let classification = best ? 'two_phase' : 'single';
    let stability = 'not_checked';
    if (previous) {
      stability = 'unavailable';
      const priorSignature = previous.quality && previous.quality.signature;
      if (previousRoi && Array.isArray(priorSignature) && priorSignature.length === signatures.length && priorSignature.every(Number.isFinite)) {
        const moved = ['x', 'y', 'width', 'height'].some(k => Math.abs(roi[k] - previousRoi[k]) > Math.max(3, roi[k === 'x' || k === 'width' ? 'width' : 'height'] * 0.05));
        const changed = difference(signatures, priorSignature) > 12;
        const shifted = best && Number.isFinite(previous.interface_y) && Math.abs(best.y - previous.interface_y) > roi.height * SETTINGS.maximum_interface_shift_fraction;
        const flipped = previous.classification !== 'uncertain' && previous.classification !== classification;
        stability = moved || changed || shifted || flipped ? 'unstable' : 'stable';
        if (stability === 'unstable') flags.push('unstable_frame');
      } else flags.push('previous_frame_unusable');
    }
    if (flags.length) classification = 'uncertain';
    const explanations = {
      missing_pixels: '图像数据不完整', glare: '液体区域反光或过曝', underexposed: '液体区域曝光不足',
      low_contrast: '光学对比度不足', blur_or_weak_edges: '瓶边模糊或边缘不足', texture_or_bubbles: '存在气泡样纹理或局部干扰',
      multiple_interfaces: '存在多个候选界面', weak_or_diffuse_interface: '仅见微弱或弥散变化',
      unstable_frame: '与上一帧相比不稳定', previous_frame_unusable: '上一帧信息不足以检查稳定性'
    };
    return {
      sample: roi.sample, classification,
      reason: flags.length ? flags.map(f => explanations[f]).join('；') : best ?
        '检测到横向持续颜色界面，仅为光学分相候选，需人工复核。' :
        '在所检液体区域未检出明显界面，仅为光学单相候选，不证明科学意义的单相。',
      interface_y: classification === 'two_phase' ? best.y : null,
      candidate_interface_y: best ? best.y : null,
      quality: { flags, glare_fraction: rounded(glare / total), texture_fraction: rounded(texture / total),
        edge_sharpness: rounded(sharpness), contrast_range: rounded(maximum - minimum),
        interface_contrast: rounded(best ? best.contrast : strongestContrast),
        horizontal_support: best ? best.support : 0, stability, signature: signatures },
      inspected_region: { x: x0, y: y0, width: x1 - x0, height: y1 - y0 }
    };
  }

  function analyzeFrame(imageData, options = {}) {
    options = options && typeof options === 'object' ? options : {};
    const manual = Object.prototype.hasOwnProperty.call(options, 'rois');
    const result = { rois: [], bottles: [], detection: { status: 'uncertain', source: manual ? 'manual' : 'auto', candidate_count: 0, reason: '' },
      method: METHOD, validated: false, settings: { ...SETTINGS },
      limitation: '未经真实实验图像验证；瓶体与界面均为光学候选。无可见界面不等于科学意义的单相。' };
    if (!imageData || !Number.isInteger(imageData.width) || !Number.isInteger(imageData.height) ||
        imageData.width < 80 || imageData.height < 60 || imageData.width * imageData.height > 24000000 ||
        !imageData.data || imageData.data.BYTES_PER_ELEMENT !== 1 || imageData.data.length !== imageData.width * imageData.height * 4) {
      return invalid(result, '缺少有效 RGBA 图像，或分辨率不足/过大。');
    }
    let opaque = 0, pixels = 0;
    for (let i = 3; i < imageData.data.length; i += 4) { if (imageData.data[i] >= 250) opaque++; pixels++; }
    if (opaque / pixels < 0.99) return invalid(result, '图像包含缺失或透明像素。');
    if (manual) {
      const error = validateRois(options.rois, imageData.width, imageData.height);
      if (error) return invalid(result, error);
      result.rois = options.rois.map(({ sample, x, y, width, height }) => ({ sample, x, y, width, height })).sort((a, b) => a.sample - b.sample);
      result.detection.candidate_count = result.rois.length;
    } else {
      const detected = detectBottles(imageData);
      result.detection.candidate_count = detected.candidates.length;
      if (detected.reason) return invalid(result, detected.reason, 'uncertain');
      result.rois = detected.candidates.sort((a, b) => b.x - a.x).map((r, i) => ({ sample: i + 1, ...r }));
      const error = validateRois(result.rois, imageData.width, imageData.height);
      if (error) { result.rois = []; return invalid(result, error, 'uncertain'); }
    }
    result.detection.missing_samples = [1, 2, 3, 4, 5].filter(sample => !result.rois.some(r => r.sample === sample));
    const partial = manual && result.detection.missing_samples.length > 0;
    result.detection.status = partial ? 'partial' : 'ok';
    result.detection.reason = manual ? (partial ?
      `使用人工指定的 ${result.rois.length} 个瓶体区域；未指定瓶号 ${result.detection.missing_samples.join('、')}，不补框或推断缺失瓶结果。` :
      '使用人工指定的五个瓶体区域。') : '五个形状一致的直立瓶体候选，按画面从右到左编号；仍需人工确认。';
    const previous = options.previous && typeof options.previous === 'object' ? options.previous : null;
    result.bottles = result.rois.map(roi => inspectBottle(imageData, roi,
      previous ? (Array.isArray(previous.bottles) && previous.bottles.find(b => b && b.sample === roi.sample)) || {} : null,
      previous && Array.isArray(previous.rois) ? previous.rois.find(r => r && r.sample === roi.sample) : null));
    return result;
  }
  return { analyzeFrame };
}));
