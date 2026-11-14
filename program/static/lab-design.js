'use strict';
window.ATPSLabPages.design = (ctx, page) => {
  const projectCard = ctx.card(
    page,
    '建立项目或条件新版本',
    '温度为条件记录；没有传感器或恒温控制。默认观察秒数只是工程设置，应由实验确定。',
  );
  const projectForm = ctx.form(
    projectCard,
    [
      ['name', '项目名称', '双水相实验'],
      ['salt_port', '盐来源口', 13, [10, 11, 12, 13].map((n) => [n, n + '口'])],
      ['temperature_c', '设定温度 / ℃', 25],
      ['measured_temperature_c', '手工实测温度 / ℃', ''],
      ['total_mass_g', '总质量 / g', 1],
      ['safe_mass_limit_g', '已确认安全总质量上限 / g', ''],
      ['composition_tolerance', '允许组成偏差 / 百分点', 1],
      ['photo_offsets_s', '计划拍照秒数', '60,180,300,600'],
      ['min_settle_s', '最早有效观察 / s', 300],
      ['max_observe_s', '观察上限 / s', 1800],
    ],
    '建立项目',
    async (d, requestId) => {
      const p = { ...projectPayload(d), request_id: requestId };
      const result = await ctx.api('project', p);
      ctx.chooseProject(result.project.id);
      form.elements.mass.value = result.project.total_mass_g;
      ctx.tell('新项目已建立；使用本次母液与标定快照。');
    },
    'project-form',
  );
  function projectPayload(d) {
    return {
      ...d,
      salt_port: +d.salt_port,
      temperature_c: +d.temperature_c,
      measured_temperature_c: d.measured_temperature_c === '' ? null : +d.measured_temperature_c,
      total_mass_g: +d.total_mass_g,
      safe_mass_limit_g: d.safe_mass_limit_g === '' ? null : +d.safe_mass_limit_g,
      composition_tolerance_fraction: +d.composition_tolerance / 100,
      photo_offsets_s: d.photo_offsets_s.split(/[,，]/).map(Number),
      min_settle_s: +d.min_settle_s,
      max_observe_s: +d.max_observe_s,
    };
  }
  let versionId = null,
    versionKey = null;
  ctx.button(projectCard, '由当前项目创建条件新版本', async () => {
    if (!ctx.project()) throw Error('先选择原项目');
    const d = Object.fromEntries(
      [...projectForm.elements].filter((n) => n.name).map((n) => [n.name, n.value]),
    );
    const body = { project_id: ctx.projectId, ...projectPayload(d) },
      key = JSON.stringify(body);
    if (key !== versionKey) {
      versionId = null;
      versionKey = key;
    }
    versionId ||= crypto.randomUUID();
    const result = await ctx.api('project_version', { ...body, request_id: versionId });
    versionId = null;
    ctx.chooseProject(result.project.id);
    ctx.tell('项目新版本已创建，历史样品保持原记录。');
  });
  const designCard = ctx.card(
    page,
    '组成与动作预览',
    '目标、整数步估计和偏差分开显示。加入队列使用当前项目；大体积按标定范围分段。',
  );
  let preview = null,
    previewKey = null;
  const form = ctx.form(
    designCard,
    [
      ['peg', 'PEG质量分数 / %', 10],
      ['salt', '盐质量分数 / %', 5],
      ['mass', '总质量 / g', 1],
      ['replicates', '独立重复数', 1],
    ],
    '预览全部独立样品',
    async () => {
      const recipe = values();
      const result = await ctx.api('preview', { project_id: ctx.projectId, ...recipe });
      preview = result.preview;
      previewKey = JSON.stringify([ctx.projectId, recipe]);
      renderPreview(recipe);
      ctx.tell(preview.executable ? '预览完成，可加入队列。' : '配方需要调整，请查看原因。');
    },
    'composition-form',
  );
  function values() {
    if (!ctx.project()) throw Error('请先选择项目');
    return {
      peg_fraction: +form.elements.peg.value / 100,
      salt_fraction: +form.elements.salt.value / 100,
      total_mass_g: +form.elements.mass.value,
      replicates: +form.elements.replicates.value,
    };
  }
  const previewBox = ctx.node('div', null, designCard, { id: 'composition-preview' });
  function renderPreview(recipe) {
    const e = preview.estimated || {},
      target = preview.target || recipe;
    ctx.table(
      previewBox,
      ['值', 'PEG w/w', '盐 w/w', '总质量 / g'],
      [
        [
          '目标',
          ctx.fraction(target.peg_fraction),
          ctx.fraction(target.salt_fraction),
          target.total_mass_g,
        ],
        [
          '整数步估计（非实测）',
          ctx.fraction(e.peg_fraction),
          ctx.fraction(e.salt_fraction),
          e.total_mass_g,
        ],
        [
          '偏差 / 百分点',
          Number.isFinite(e.peg_fraction)
            ? ((e.peg_fraction - target.peg_fraction) * 100).toFixed(3)
            : '未知',
          Number.isFinite(e.salt_fraction)
            ? ((e.salt_fraction - target.salt_fraction) * 100).toFixed(3)
            : '未知',
          '—',
        ],
      ],
    );
    const actions = ctx.node('div', null, previewBox);
    ctx.table(
      actions,
      ['来源', '目标质量/g', '估计质量/g', '液段数', '吸取 / 追送参数'],
      (preview.plan?.items || []).map((r) => [
        r.source,
        r.requested_mass_g,
        r.estimated_mass_g,
        r.parameters?.length,
        (r.parameters || [])
          .map(
            (p) =>
              `${p.aspirate.steps}步/${p.aspirate.rpm}rpm → ${p.chase.steps}步/${p.chase.rpm}rpm`,
          )
          .join('；'),
      ]),
    );
    const samples = ctx.node('div', null, previewBox);
    ctx.table(
      samples,
      ['待建样品', '目标PEG/盐', '总质量/g'],
      Array.from({ length: Math.min(100, Math.max(0, recipe.replicates)) }, (_, i) => [
        '独立重复 ' + (i + 1),
        ctx.fraction(recipe.peg_fraction) + ' / ' + ctx.fraction(recipe.salt_fraction),
        recipe.total_mass_g,
      ]),
    );
    ctx.node(
      'p',
      (preview.executable ? '可执行' : '不可执行') +
        '；' +
        [...(preview.issues || []), ...(preview.warnings || [])].join('；'),
      previewBox,
    );
  }
  let enqueueId = null;
  ctx.button(
    designCard,
    '将预览的独立样品加入队列',
    async () => {
      const recipe = values();
      if (!preview?.executable || previewKey !== JSON.stringify([ctx.projectId, recipe]))
        throw Error('参数有变化，请先重新预览');
      enqueueId ||= crypto.randomUUID();
      await ctx.api('enqueue', {
        project_id: ctx.projectId,
        recipes: [recipe],
        request_id: enqueueId,
      });
      enqueueId = null;
      previewKey = null;
      ctx.tell('独立样品已加入当前项目队列。');
    },
    'enqueue-preview',
  );
  const csvCard = ctx.card(
    page,
    '表格导入',
    'CSV表头：peg_percent,salt_percent,total_mass_g,replicates。导入前显示全部独立重复。',
  );
  const csv = ctx.node('textarea', null, csvCard, { id: 'recipe-csv' });
  let imported = [],
    importKey = null,
    importId = null;
  const importedBox = ctx.node('div', null, csvCard);
  ctx.button(csvCard, '检查表格', async () => {
    const lines = csv.value.trim().split(/\r?\n/);
    if (lines.shift() !== 'peg_percent,salt_percent,total_mass_g,replicates')
      throw Error('CSV表头不正确');
    imported = lines.map((line, index) => {
      const v = line.split(',');
      if (
        v.length !== 4 ||
        v.some((x) => !x.trim() || !Number.isFinite(+x)) ||
        +v[2] <= 0 ||
        !Number.isInteger(+v[3]) ||
        +v[3] < 1 ||
        +v[3] > 5
      )
        throw Error(`第${index + 2}行无效`);
      return {
        peg_fraction: +v[0] / 100,
        salt_fraction: +v[1] / 100,
        total_mass_g: +v[2],
        replicates: +v[3],
      };
    });
    if (!imported.length) throw Error('表格没有样品');
    for (const r of imported) {
      const result = await ctx.api('preview', { project_id: ctx.projectId, ...r });
      if (!result.preview.executable) throw Error(result.preview.issues.join('；'));
    }
    ctx.table(
      importedBox,
      ['表格行', '独立重复', 'PEG/盐 w/w', '质量/g'],
      imported.flatMap((r, i) =>
        Array.from({ length: r.replicates }, (_, j) => [
          i + 2,
          j + 1,
          ctx.fraction(r.peg_fraction) + ' / ' + ctx.fraction(r.salt_fraction),
          r.total_mass_g,
        ]),
      ),
    );
    importKey = JSON.stringify([ctx.projectId, csv.value]);
    importId = null;
    ctx.tell('表格检查通过，请核对后加入。');
  });
  ctx.button(csvCard, '将已检查表格加入当前项目', async () => {
    if (!imported.length || importKey !== JSON.stringify([ctx.projectId, csv.value]))
      throw Error('表格有变化，请先检查表格');
    importId ||= crypto.randomUUID();
    await ctx.api('enqueue', {
      project_id: ctx.projectId,
      recipes: imported,
      request_id: importId,
    });
    imported = [];
    importId = null;
    importKey = null;
    ctx.tell('表格样品已入队。');
  });
  return {
    projectChanged() {
      preview = null;
      previewKey = null;
      imported = [];
      previewBox.replaceChildren();
      importedBox.replaceChildren();
      const p = ctx.project();
      if (p) {
        form.elements.mass.value = p.total_mass_g;
        const values = {
          ...p,
          composition_tolerance: (p.composition_tolerance_fraction ?? 0.01) * 100,
          photo_offsets_s: (p.photo_offsets_s || []).join(','),
        };
        for (const [key, value] of Object.entries(values))
          if (projectForm.elements[key]) projectForm.elements[key].value = value ?? '';
      }
    },
  };
};
