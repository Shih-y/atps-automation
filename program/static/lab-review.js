'use strict';
window.ATPSLabPages.review = (ctx, page) => {
  const card = ctx.card(
    page,
    '按样品复核照片',
    '有效观察期内拍下的照片可稍后复核。时间达标不等于已经分相平衡；没有界面也不能直接作为单相标签。',
  );
  const select = ctx.node('select', null, card, { id: 'review-sample', ariaLabel: '复核样品' });
  ctx.button(
    card,
    '查看此瓶当前观测',
    async () => {
      const selected = ctx.samples().find((s) => s.id === select.value),
        current = (ctx.state.bottles || []).find((b) => b.bottle === selected?.bottle);
      if (!current?.sample_id) throw Error('此瓶目前没有占用样品');
      select.value = current.sample_id;
      review.elements.clear.checked = false;
      renderSelected();
    },
    'review-current-observation',
  );
  const summary = ctx.node('p', '', card),
    jobs = ctx.node('div', null, card);
  ctx.node(
    'p',
    '同瓶补加会生成新的观测编号。旧照片与旧组成保留；只有当前瓶的有效人工复核能放行下一次补加，不确定时继续观察。',
    card,
    { className: 'muted' },
  );
  const lineage = ctx.node('div', null, card, { id: 'observation-lineage' });
  const gallery = ctx.node('div', null, card, { className: 'lab-gallery', id: 'sample-gallery' });
  const review = ctx.form(
    card,
    [
      ['frame_id', '作为证据的照片', '', []],
      [
        'classification',
        '人工结论',
        'uncertain',
        [
          ['uncertain', '待确认'],
          ['single', '单相'],
          ['two_phase', '双相'],
        ],
      ],
      ['note', '观察依据', ''],
    ],
    '保存样品复核',
    async (d, requestId) => {
      await ctx.api('review', {
        ...d,
        request_id: requestId,
        sample_id: select.value,
        clear: review.elements.clear.checked,
      });
      ctx.tell('复核已保存；训练资格由照片、组成及样品状态共同确定。');
    },
    'review-form',
  );
  const clear = ctx.node('label', '已核对照片清晰、稳定且符合本实验判据', review);
  ctx.node('input', null, clear, { type: 'checkbox', name: 'clear' });
  const observations = ctx.card(
    page,
    '补拍与延长观察',
    '补拍只拍照片，不重新配液。修改观察计划保留原计划；再次混合会建立新观察阶段。',
  );
  const retry = ctx.node('select', null, observations, {
    id: 'retry-photo-job',
    ariaLabel: '补拍任务',
  });
  ctx.button(observations, '重试所选漏拍或失败任务', async () => {
    if (!retry.value) throw Error('没有可重试的拍照任务');
    await ctx.api('retry_photo', { job_id: retry.value });
    ctx.tell('补拍任务已安排。');
  });
  ctx.button(observations, '为所选样品追加一张照片', async () => {
    await ctx.api('retry_photo', { sample_id: select.value });
    ctx.tell('追加拍照已安排。');
  });
  ctx.form(
    observations,
    [
      ['max_observe_s', '延长后的观察上限 / s', 3600],
      ['offsets_s', '新增拍照时刻 / 混合后秒数', '2400,3600'],
      ['reason', '修改原因', '需要继续观察'],
    ],
    '保存观察计划修改',
    async (d, requestId) => {
      await ctx.api('extend_observation', {
        sample_id: select.value,
        request_id: requestId,
        max_observe_s: +d.max_observe_s,
        offsets_s: d.offsets_s.split(/[,，]/).filter(Boolean).map(Number),
        reason: d.reason,
      });
      ctx.tell('观察计划已修改，原计划保留。');
    },
    'observation-form',
  );
  const integrity = ctx.card(
    page,
    '排除、更正与独立组成测量',
    '仅称整瓶总质量无法测得各组分质量分数。中断配液、组成被扰动等硬性失效不能通过清空原因恢复。',
  );
  const exclusions = ctx.node('div', null, integrity);
  ctx.form(
    integrity,
    [
      ['reason', '排除原因', ''],
      [
        'code',
        '原因类型',
        'manual_exclusion',
        [
          ['manual_exclusion', '人工排除'],
          ['unclear_image', '照片仍不确定'],
          ['manual_composition', '样品组成扰动（不可简单清除）'],
          ['composition_invalid', '组成失效'],
        ],
      ],
    ],
    '记录排除原因',
    async (d, requestId) => {
      await ctx.api('exclude', { sample_id: select.value, ...d, request_id: requestId });
      ctx.tell('已记录结构化排除原因。');
    },
    'exclude-form',
  );
  const correction = ctx.form(
    integrity,
    [
      ['exclusion_id', '可申请更正的记录', '', []],
      ['reason', '更正依据', ''],
    ],
    '申请更正误操作',
    async (d, requestId) => {
      await ctx.api('correct_exclusion', { sample_id: select.value, ...d, request_id: requestId });
      ctx.tell('更正已保存；资格重新计算。');
    },
    'correction-form',
  );
  ctx.form(
    integrity,
    [
      ['peg', '实测 PEG / w/w %', ''],
      ['salt', '实测盐 / w/w %', ''],
      ['method', '独立测量方法', ''],
      ['evidence', '测量记录或报告编号', ''],
    ],
    '保存独立测量组成',
    async (d, requestId) => {
      if (d.peg === '' || d.salt === '') throw Error('填写独立测量所得的组分质量分数');
      await ctx.api('measured_composition', {
        sample_id: select.value,
        request_id: requestId,
        peg_fraction: +d.peg / 100,
        salt_fraction: +d.salt / 100,
        method: d.method,
        evidence: d.evidence,
      });
      ctx.tell('独立测量组成已记录。');
    },
    'measured-composition-form',
  );
  let galleryKey = '';
  const jobNames = {
    waiting: '计划中',
    due: '待拍摄',
    captured: '已保存',
    missed: '漏拍',
    failed: '失败',
    cancelled: '已取消',
    superseded: '旧阶段',
  };
  function drawGallery(s) {
    const frames = [...(s?.frames || [])].sort(
      (a, b) => Date.parse(a.captured_at) - Date.parse(b.captured_at),
    );
    const key = JSON.stringify([s?.id, s?.generation, frames.map((f) => [f.id, f.analysis])]);
    if (key === galleryKey) return;
    galleryKey = key;
    gallery.replaceChildren();
    for (const f of frames) {
      const item = ctx.node('figure', null, gallery),
        line = ctx.node(
          'figcaption',
          `${f.settle_s?.toFixed(1) ?? '?'} s · 第${f.generation ?? 0}观察阶段 · ${new Date(f.captured_at).toLocaleString()}`,
          item,
        );
      if (f.generation !== s.generation) line.append(' · 旧阶段，仅供对照');
      const link = ctx.node('a', '查看整幅原图', item, {
        href: f.url || '/api/lab/image/' + encodeURIComponent(f.id),
        target: '_blank',
        rel: 'noopener',
      });
      const image = ctx.node('img', null, item, {
        src: link.href,
        alt: '完整原图：' + ctx.sampleName(s),
        loading: 'lazy',
      });
      const roi = (f.rois || []).find((r) => +r.sample === +s.bottle),
        candidate = (f.analysis?.bottles || []).find((r) => +r.sample === +s.bottle);
      if (roi) {
        const canvas = ctx.node('canvas', null, item, {
          width: roi.width,
          height: roi.height,
          ariaLabel: '样品瓶区域与候选界面',
        });
        image.onload = () => {
          const g = canvas.getContext('2d');
          g.drawImage(image, roi.x, roi.y, roi.width, roi.height, 0, 0, roi.width, roi.height);
          const y = candidate?.candidate_interface_y ?? candidate?.interface_y;
          if (Number.isFinite(y)) {
            g.strokeStyle = '#111';
            g.lineWidth = Math.max(1, roi.width / 80);
            g.setLineDash([6, 4]);
            g.beginPath();
            g.moveTo(0, y - roi.y);
            g.lineTo(roi.width, y - roi.y);
            g.stroke();
          }
        };
      }
      ctx.node(
        'p',
        `ROI 版本 ${f.mapping_version ?? '?'}；候选：${{ single: '未见明显界面', two_phase: '双相候选', uncertain: '待确认' }[candidate?.classification] || '无分析'}；${candidate?.reason || (candidate?.reasons || []).join('；')}`,
        item,
      );
    }
  }
  function renderSelected() {
    const s = ctx.samples().find((s) => s.id === select.value),
      photoJobs = (ctx.state.photo_jobs || []).filter((j) => j.sample_id === s?.id);
    summary.textContent = s
      ? `${ctx.sampleName(s)}；当前观察阶段 ${s.generation ?? 0}；训练${s.training_eligibility?.eligible ? '可用' : '不可用'}：${(s.training_eligibility?.reasons || []).join('；') || '见复核状态'}；采用组成：${ctx.basis(s.composition?.basis)}`
      : '选择样品后查看照片。';
    if (s?.continuation_replaced_at)
      summary.append(' 这是历史观测；复核可更新旧相图点，但不能代替此瓶当前观测的复核。');
    const chain = [],
      seen = new Set();
    let record = s;
    while (record && !seen.has(record.id)) {
      seen.add(record.id);
      chain.unshift(record);
      record = (ctx.state.samples || []).find((x) => x.id === record.parent_sample_id);
    }
    ctx.table(
      lineage,
      ['同瓶观测链', '父观测', '累计质量估计 / g', '采用PEG / 盐', '图像数', '复核'],
      chain.map((item, index) => [
        `${index === 0 ? '初始' : '补加后'}：${ctx.sampleName(item)}`,
        item.parent_sample_id?.slice(0, 8) || '起点',
        item.recipe?.estimated?.total_mass_g ?? '未知',
        ctx.fraction(item.composition?.peg_fraction) +
          ' / ' +
          ctx.fraction(item.composition?.salt_fraction),
        item.frames?.length || 0,
        { single: '单相', two_phase: '双相', uncertain: '待确认', unreviewed: '未复核' }[
          item.review_state
        ] || '未复核',
      ]),
    );
    ctx.table(
      jobs,
      ['计划 / 混合后秒', '实际拍摄', '迟拍 / s', '状态', '失败原因'],
      photoJobs.map((j) => {
        const f = (s.frames || []).find((f) => f.job_id === j.id);
        return [
          j.offset_s,
          f?.captured_at ? new Date(f.captured_at).toLocaleString() : '—',
          f?.lateness_s?.toFixed(1) ?? j.lateness_s ?? '—',
          jobNames[j.status] || j.status,
          j.failure_reason || j.error || j.reason || '',
        ];
      }),
    );
    ctx.options(
      review.elements.frame_id,
      [...(s?.frames || [])]
        .sort((a, b) => b.settle_s - a.settle_s)
        .map((f) => [
          f.id,
          `${f.settle_s?.toFixed(1)}秒 · 阶段${f.generation ?? 0} · ${new Date(f.captured_at).toLocaleString()}`,
        ]),
      '选择证据照片',
    );
    ctx.options(
      retry,
      photoJobs
        .filter((j) => ['failed', 'missed'].includes(j.status))
        .map((j) => [
          j.id,
          `${j.offset_s}秒 · ${jobNames[j.status]} · ${j.failure_reason || j.error || ''}`,
        ]),
      '没有漏拍或失败任务',
    );
    ctx.table(
      exclusions,
      ['类型', '原因', '状态', '记录时间'],
      (s?.exclusions || []).map((e) => [
        e.code,
        e.reason,
        e.active ? (e.hard ? '硬性失效' : '有效排除') : '已更正',
        e.at,
      ]),
    );
    ctx.options(
      correction.elements.exclusion_id,
      (s?.exclusions || []).filter((e) => e.active && !e.hard).map((e) => [e.id, e.reason]),
      '无可更正的软性排除',
    );
    drawGallery(s);
  }
  select.onchange = () => {
    review.elements.clear.checked = false;
    renderSelected();
  };
  return {
    render() {
      ctx.selectSample(select);
      renderSelected();
    },
    projectChanged() {
      galleryKey = '';
      review.elements.clear.checked = false;
    },
  };
};
