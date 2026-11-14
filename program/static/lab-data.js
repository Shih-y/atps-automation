'use strict';
window.ATPSLabPages.data = (ctx, page) => {
  const card = ctx.card(
    page,
    '数据范围与导出',
    '导出保留项目引用的历史母液、标定和方法版本，以及原图、人工标签、模型轮次、采用组成及软件版本。CSV 使用独立字段。',
  );
  const filters = ctx.node('div', null, card, { className: 'lab-form' });
  function field(title, name, options) {
    const l = ctx.node('label', title, filters);
    const n = ctx.node(options ? 'select' : 'input', null, l, { id: 'data-' + name });
    if (options) options.forEach(([v, t]) => ctx.node('option', t, n, { value: v }));
    n.onchange = render;
    return n;
  }
  const scope = field('导出范围', 'scope', [
    ['current', '当前项目'],
    ['all', '全部项目'],
  ]);
  const system = field('盐体系', 'system', [
    ['', '全部'],
    ...[10, 11, 12, 13].map((n) => [n, '盐' + n + '口']),
  ]);
  const status = field('执行状态', 'status', [
    ['', '全部'],
    ['queued', '排队'],
    ['preparing', '配液中'],
    ['settling', '静置中'],
    ['observation_complete', '观察完成'],
    ['interrupted', '中断'],
    ['cancelled', '取消'],
  ]);
  const since = field('记录起始日期', 'since'),
    until = field('记录截止日期', 'until');
  since.type = until.type = 'date';
  const preview = ctx.node('p', '', card, { id: 'export-preview' }),
    link = ctx.node('a', '导出当前项目 ZIP', card, { id: 'export-project' });
  link.onclick = (e) => {
    if (scope.value === 'current' && !ctx.project()) {
      e.preventDefault();
      ctx.tell('请选择需要导出的项目；不会自动导出全部数据。', true);
    }
  };
  const backupLink = ctx.node('a', '下载刚创建的备份', card, {
    id: 'backup-download',
    hidden: true,
  });
  ctx.button(
    card,
    '创建本地项目备份',
    async () => {
      const body = scope.value === 'current' ? { project_id: ctx.projectId } : {};
      if (scope.value === 'current' && !ctx.project()) throw Error('先选择项目');
      const result = await ctx.api('backup', body);
      ctx.tell('本地备份已保存：' + (result.backup?.created_at || result.backup?.id || ''));
      if (result.backup?.download_url) {
        backupLink.href = result.backup.download_url;
        backupLink.hidden = false;
      }
    },
    'backup-project',
  );
  ctx.node(
    'p',
    '下方日期、体系和状态筛选只影响查询列表；ZIP 按明确选择的当前项目/全部项目完整导出，以保持引用记录完整。',
    card,
    { className: 'muted' },
  );
  const rows = ctx.node('div', null, card);
  function render() {
    const current = scope.value === 'current',
      samples = (ctx.state.samples || []).filter((s) => !current || s.project_id === ctx.projectId),
      ids = new Set(samples.map((s) => s.id));
    const projects = (ctx.state.projects || []).filter((p) => !current || p.id === ctx.projectId),
      rounds = (ctx.state.rounds || []).filter((r) => !current || r.project_id === ctx.projectId),
      jobs = (ctx.state.photo_jobs || []).filter((j) => ids.has(j.sample_id));
    preview.textContent = `导出 ${current ? ctx.project()?.name || '未选项目' : '全部项目'}：${projects.length}个项目、${samples.length}个样品、${jobs.length}个拍照任务、${samples.reduce((n, s) => n + (s.frames || []).length, 0)}张原图、${rounds.length}个模型轮次。引用记录按历史版本收录。`;
    link.textContent = current ? '导出当前项目 ZIP' : '导出全部数据 ZIP';
    const unavailable = current && !ctx.project();
    link.setAttribute('aria-disabled', String(unavailable));
    if (unavailable) link.removeAttribute('href');
    else
      link.href =
        '/api/lab/export' + (current ? '?project_id=' + encodeURIComponent(ctx.projectId) : '');
    const filtered = samples.filter((s) => {
      const p = projects.find((p) => p.id === s.project_id),
        date = (s.created_at || p?.created_at || '').slice(0, 10);
      return (
        (!system.value || +p?.salt_port === +system.value) &&
        (!status.value || (s.execution_state || s.status) === status.value) &&
        (!since.value || date >= since.value) &&
        (!until.value || date <= until.value)
      );
    });
    ctx.table(
      rows,
      [
        '项目 / 样品',
        '目标 PEG / 盐',
        '采用 PEG / 盐',
        '组成依据',
        '执行状态',
        '复核',
        '训练资格 / 原因',
        '照片数',
      ],
      filtered.map((s) => {
        const c = s.composition || {};
        return [
          (ctx.state.projects || []).find((p) => p.id === s.project_id)?.name +
            ' / ' +
            ctx.sampleName(s),
          ctx.fraction(s.target?.peg_fraction) + ' / ' + ctx.fraction(s.target?.salt_fraction),
          ctx.fraction(c.peg_fraction) + ' / ' + ctx.fraction(c.salt_fraction),
          ctx.basis(c.basis),
          {
            queued: '排队',
            preparing: '配液中',
            settling: '静置中',
            observation_complete: '观察完成',
            interrupted: '中断',
            cancelled: '已取消',
          }[s.execution_state || s.status] || s.status,
          { single: '单相', two_phase: '双相', uncertain: '待确认', unreviewed: '未复核' }[
            s.review_state
          ] || '未复核',
          s.training_eligibility?.eligible
            ? '可用'
            : (s.training_eligibility?.reasons || []).join('；'),
          s.frames?.length || 0,
        ];
      }),
    );
  }
  return { render };
};
