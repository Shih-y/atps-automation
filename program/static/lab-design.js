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
    '目标、整数步估计和偏差分开显示。首次使用请先在运行监控确认换好干净空瓶；空闲瓶位不代表瓶内确实为零。加入队列使用当前项目，大体积按标定范围分段。',
  );
  let preview = null,
    previewKey = null;
  const form = ctx.form(
    designCard,
    [
      [
        'mode',
        '配样方式',
        'independent',
        [
          ['independent', '独立配样：干净空瓶'],
          ['continuation', '同瓶补加：保留现有液体'],
        ],
      ],
      ['bottle', '同瓶补加瓶位', 1, [1, 2, 3, 4, 5].map((n) => [n, n + '号瓶'])],
      ['peg', 'PEG质量分数 / %', 10],
      ['salt', '盐质量分数 / %', 5],
      ['mass', '总质量 / g', 1],
      ['replicates', '独立重复数', 1],
    ],
    '预览全部独立样品',
    async () => {
      const recipe = values();
      const result = await ctx.api(
        form.elements.mode.value === 'continuation' ? 'continuation_preview' : 'preview',
        { project_id: ctx.projectId, ...recipe },
      );
      preview = result.preview;
      previewKey = JSON.stringify([ctx.projectId, recipe]);
      renderPreview(recipe);
      ctx.tell(
        preview.executable
          ? preview.kind === 'continuation'
            ? '补加预览完成，建立任务后还须明确启动。'
            : '预览完成，可加入队列。'
          : '配方需要调整，请查看原因。',
      );
    },
    'composition-form',
  );
  function values() {
    if (!ctx.project()) throw Error('请先选择项目');
    if (form.elements.mode.value === 'continuation')
      return {
        bottle: +form.elements.bottle.value,
        peg_fraction: +form.elements.peg.value / 100,
        salt_fraction: +form.elements.salt.value / 100,
      };
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
    if (preview.kind === 'continuation') {
      const inventory = preview.initial_inventory || {};
      const initial = ctx.node('section', null, null, { className: 'continuation-stage' });
      ctx.node('h3', '1 · 现有瓶内物料', initial);
      ctx.node(
        'p',
        inventory.known
          ? `瓶${recipe.bottle}；按既往标定估计累计 ${inventory.total_mass_g} g；PEG ${inventory.peg_mass_g} g，盐 ${inventory.salt_mass_g} g，水 ${inventory.water_mass_g} g。库存版本 ${inventory.revision}。`
          : '物料未知，不能定量续加。请处理样品并确认换入干净空瓶。',
        initial,
      );
      previewBox.prepend(initial);
      ctx.node('h3', '2 · 最终目标与整数步估计', initial);
      ctx.node('h3', '3 · 仅补加所缺组分', previewBox);
      const additions = ctx.node('div', null, previewBox);
      ctx.table(
        additions,
        ['来源口', '连续数学理论补加 / g', '整数步执行估计补加 / g'],
        Object.entries(preview.theoretical_additions_g || preview.stock_masses || {}).map(
          ([port, mass]) => [
            port,
            ctx.mass(mass),
            ctx.mass(
              preview.selected_additions_g?.[port] ??
                (preview.plan?.items || []).find((row) => +row.source === +port)?.estimated_mass_g,
            ),
          ],
        ),
      );
      ctx.node(
        'p',
        `本次预计补加 ${ctx.mass(preview.added_mass_g)} g；预计动作 ${Number.isFinite(preview.estimated_duration_s) ? preview.estimated_duration_s.toFixed(1) : '未知'} s（含混合/公共吹扫，不含后续观察与人工复核）。最终累计 ${ctx.mass(e.total_mass_g)} g，不等于相同数字的 mL；只有补加动作进入泵阀。`,
        previewBox,
      );
    }
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
      preview.kind === 'continuation'
        ? [
            [
              '本瓶新增观测（保留父记录）',
              ctx.fraction(recipe.peg_fraction) + ' / ' + ctx.fraction(recipe.salt_fraction),
              e.total_mass_g,
            ],
          ]
        : Array.from({ length: Math.min(100, Math.max(0, recipe.replicates)) }, (_, i) => [
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
  const enqueueButton = ctx.button(
    designCard,
    '将预览的独立样品加入队列',
    async () => {
      const recipe = values();
      if (form.elements.mode.value !== 'independent')
        throw Error('同瓶补加请建立单次补加任务，不使用独立配样队列');
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
  const runCard = ctx.card(
    page,
    '同瓶研究：建立设置，再明确启动',
    '有效人工单相/双相复核后，才允许该瓶下一次补加；其他瓶可继续。时间预算从启动起计算，包含等待人工复核。不确定结果先复拍或复核。',
  );
  const runForm = ctx.form(
    runCard,
    [
      ['bottles', '研究瓶位（逗号分隔）', '1,2,3,4,5'],
      [
        'objective',
        '研究目标',
        'boundary',
        [
          ['boundary', '寻找相边界'],
          ['two_phase_neighborhood', '探索双相邻域（不测系线）'],
        ],
      ],
      ['peg_min', 'PEG下限 / %', 5],
      ['peg_max', 'PEG上限 / %', 20],
      ['salt_min', '盐下限 / %', 2],
      ['salt_max', '盐上限 / %', 12],
      ['step', '候选间隔 / 百分点', 2],
      ['round_budget', '最多补加次数', 10],
      ['added_mass_budget_g', '总补加质量预算 / g', 5],
      ['time_budget_min', '墙钟时间预算 / min', 60],
    ],
    '仅建立同瓶研究设置',
    async (d, requestId) => {
      await createRun(runPayload(d), requestId);
    },
    'continuation-run-form',
  );
  function runPayload(d) {
    if (!ctx.project()) throw Error('先选择项目');
    return {
      project_id: ctx.projectId,
      bottles: d.bottles
        .split(/[,，]/)
        .filter((x) => x.trim())
        .map(Number),
      objective: d.objective,
      peg_min: +d.peg_min / 100,
      peg_max: +d.peg_max / 100,
      salt_min: +d.salt_min / 100,
      salt_max: +d.salt_max / 100,
      step: +d.step / 100,
      round_budget: +d.round_budget,
      added_mass_budget_g: +d.added_mass_budget_g,
      time_budget_s: +d.time_budget_min * 60,
    };
  }
  async function createRun(body, requestId) {
    const result = await ctx.api('continuation_run', { ...body, request_id: requestId });
    ctx.continuationRunId = result.run.id;
    ctx.tell('研究设置已建立；尚未启动泵阀。核对预算后点“启动所选同瓶研究”。');
  }
  let singleId = null,
    singleKey = null;
  const singleButton = ctx.button(
    designCard,
    '建立本次单瓶补加任务',
    async () => {
      const recipe = values();
      if (
        form.elements.mode.value !== 'continuation' ||
        !preview?.executable ||
        previewKey !== JSON.stringify([ctx.projectId, recipe])
      )
        throw Error('请先完成当前瓶的有效补加预览');
      const current = (ctx.state.bottles || []).find((b) => b.bottle === recipe.bottle)?.inventory;
      if (!current?.known || current.revision !== preview.initial_inventory?.revision)
        throw Error('瓶内物料记录已改变，请重新预览后建立任务');
      const data = Object.fromEntries(
        [...runForm.elements].filter((n) => n.name).map((n) => [n.name, n.value]),
      );
      const body = {
        ...runPayload(data),
        bottles: [recipe.bottle],
        peg_min: recipe.peg_fraction,
        peg_max: recipe.peg_fraction,
        salt_min: recipe.salt_fraction,
        salt_max: recipe.salt_fraction,
        round_budget: 1,
      };
      const key = JSON.stringify(body);
      if (key !== singleKey) {
        singleId = null;
        singleKey = key;
      }
      singleId ||= crypto.randomUUID();
      await createRun(body, singleId);
      singleId = null;
    },
    'continuation-single-create',
  );
  const runSelect = ctx.node('select', null, runCard, {
    id: 'continuation-design-select',
    ariaLabel: '同瓶研究任务',
  });
  runSelect.onchange = () => {
    ctx.continuationRunId = runSelect.value;
    ctx.render();
  };
  const runSummary = ctx.node('p', '', runCard, { id: 'continuation-design-summary' });
  ctx.button(
    runCard,
    '启动所选同瓶研究',
    async () => {
      await ctx.continuationCommand('start');
      ctx.tell('已启动同瓶研究；先完成的瓶等待有效人工复核，其余瓶继续。');
    },
    'continuation-start',
  );
  function updateMode() {
    const continuation = form.elements.mode.value === 'continuation';
    form.elements.bottle.closest('label').hidden = !continuation;
    form.elements.mass.disabled = continuation;
    form.elements.replicates.disabled = continuation;
    enqueueButton.hidden = continuation;
    singleButton.hidden = !continuation;
    preview = null;
    previewKey = null;
    previewBox.replaceChildren();
  }
  form.elements.mode.onchange = updateMode;
  updateMode();
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
    render() {
      ctx.options(
        runSelect,
        ctx
          .continuationRuns()
          .map((r) => [
            r.id,
            `${ctx.runState(r.status)} · ${(r.bottles || []).join('、')}号瓶 · ${r.id.slice(0, 8)}`,
          ]),
        '尚无同瓶研究',
      );
      runSelect.value = ctx.continuationRunId || '';
      const run = ctx.continuationRun();
      runSummary.textContent = run
        ? `${ctx.runState(run.status)}；${run.reason || ''}。补加预算 ${run.budgets?.added_mass_budget_g ?? '?'} g / ${run.budgets?.round_budget ?? '?'}次；墙钟预算 ${(run.budgets?.time_budget_s ?? 0) / 60} min（含等待人工复核）。`
        : '先建立设置；此操作本身不会配液。';
    },
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
