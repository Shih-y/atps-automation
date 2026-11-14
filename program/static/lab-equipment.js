'use strict';
window.ATPSLabPages = window.ATPSLabPages || {};
window.ATPSLabPages.equipment = (ctx, page) => {
  const stockCard = ctx.card(
    page,
    '母液信息',
    '质量分数w/w；未知密度留空。g/步交付标定不依赖密度，1g不等于1mL。密度条件单独填写，不自动把当前母液条件当成密度测量依据。',
  );
  const sources = [10, 11, 12, 13, 14, 15].map((n) => [n, n + '口']);
  const stock = ctx.form(
    stockCard,
    [
      ['port', '来源口', 10, sources],
      ['name', '母液名称', '柠檬酸三钠'],
      ['solute', '溶质名称', 'sodium_citrate'],
      ['percent', '配制质量分数 / 按所选试剂 %', 30],
      [
        'reagent_form',
        '试剂形态 / 换算',
        'as_configured',
        [
          ['as_configured', '按已配置浓度，不换算'],
          ['anhydrous', '无水试剂'],
          ['dihydrate', '二水柠檬酸三钠（仅10口）'],
        ],
      ],
      ['batch', '批次', ''],
      ['temperature_c', '条件温度 / ℃', 25],
      ['density_g_ml', '已记录密度 / g·mL⁻¹', ''],
      [
        'density_basis',
        '密度依据',
        'unknown',
        [
          ['unknown', '未测'],
          ['measured', '本批次实测'],
          ['reference', '文献参考（非本批次实测）'],
          ['legacy_unverified', '旧值待核对'],
        ],
      ],
      ['density_temperature_c', '密度证据中的温度 / ℃', ''],
      ['density_percent', '密度证据浓度 / 无水等效 %', ''],
      ['density_batch', '密度实测对应批次', ''],
      ['density_evidence', '密度测量依据', ''],
    ],
    '保存母液新版本',
    async (d, requestId) => {
      const reagentFraction = +d.percent / 100;
      if (d.reagent_form === 'dihydrate' && (+d.port !== 10 || d.solute !== 'sodium_citrate'))
        throw Error('二水柠檬酸三钠换算仅适用于10口 sodium_citrate');
      const effectiveFraction =
        d.reagent_form === 'dihydrate' ? (reagentFraction * 258.06) / 294.1 : reagentFraction;
      await ctx.api('stock', {
        ...d,
        request_id: requestId,
        port: +d.port,
        mass_fraction: effectiveFraction,
        reagent_mass_fraction: reagentFraction,
        composition_basis:
          d.reagent_form === 'as_configured' ? 'as_configured' : 'anhydrous_equivalent',
        temperature_c: +d.temperature_c,
        density_g_ml: d.density_g_ml ? +d.density_g_ml : null,
        density_temperature_c: d.density_temperature_c === '' ? null : +d.density_temperature_c,
        density_mass_fraction: d.density_percent === '' ? null : +d.density_percent / 100,
      });
      ctx.tell('母液信息已保存。');
    },
    'stock-form',
  );
  const compositionNote = ctx.node('p', '', stockCard, { id: 'stock-composition-note' });
  let stockInitialized = false,
    stockDirty = false;
  function showConcentration() {
    const value = +stock.elements.percent.value;
    compositionNote.textContent =
      stock.elements.reagent_form.value === 'dihydrate'
        ? `按二水试剂配制 ${value}%；无水柠檬酸三钠等效 ${Number.isFinite(value) ? ((value * 258.06) / 294.1).toFixed(3) : '未知'}%。结晶水计入溶剂；旧项目不自动更改，请建立项目新版本。`
        : `配置浓度 ${value}%；${stock.elements.reagent_form.value === 'anhydrous' ? '按无水试剂基准' : '未进行试剂形态换算'}。旧项目保留原浓度版本。`;
  }
  stock.addEventListener('input', () => {
    stockDirty = true;
    showConcentration();
  });
  stock.elements.port.onchange = () => {
    const s = (ctx.state.stocks || []).find((s) => s.port === +stock.elements.port.value);
    if (s)
      for (const [k, v] of Object.entries({
        ...s,
        percent: (s.reagent_mass_fraction ?? s.mass_fraction) * 100,
        reagent_form: s.reagent_form ?? 'as_configured',
        density_basis:
          s.density_basis ?? (s.density_g_ml == null ? 'unknown' : 'legacy_unverified'),
        density_g_ml: s.density_g_ml ?? '',
        density_evidence: s.density_evidence ?? '',
        density_batch: s.density_batch ?? '',
        density_temperature_c: s.density_temperature_c ?? '',
        density_percent: s.density_mass_fraction == null ? '' : s.density_mass_fraction * 100,
      }))
        if (stock.elements[k]) stock.elements[k].value = v ?? '';
    showConcentration();
  };
  const densityCard = ctx.card(
    page,
    '母液密度核查',
    '参考值仅供核对，不会自动写入母液或换算标定。固体密度不可用作母液密度；结晶水及浓度基准未核实时保留提示。',
  );
  const densityRows = ctx.node('div', null, densityCard, { id: 'density-audit' });
  const calCard = ctx.card(
    page,
    '样品端试送与称量录入',
    '先预充来源硬管；净质量=装液瓶质量−空瓶质量。0.01g天平可用5剂一组、重复3组；分组RSD不能证明单剂重复性。',
  );
  const cal = ctx.form(
    calCard,
    [
      ['source', '来源口', 10, sources],
      ['sample', '收集瓶', 1, [1, 2, 3, 4, 5, 6].map((n) => [n, n + '号瓶'])],
      ['groups', '各组净质量 / g（逗号分隔）', ''],
      ['doses_per_group', '每组剂数', 5],
      ['aspirate_steps', '每剂吸取步数', 45],
      ['aspirate_rpm', '吸取 / rpm', 5],
      ['chase_steps', '追送步数', 1000],
      ['chase_rpm', '追送 / rpm', 15],
      ['min_steps', '适用最小吸取步数', 45],
      ['max_steps', '适用最大吸取步数', 45],
      [
        'status',
        '标定状态',
        'trial',
        [
          ['trial', '试用'],
          ['validated', '已验证'],
          ['invalid', '失效'],
        ],
      ],
      ['sample_bottles', '适用瓶位（逗号分隔）', '1'],
      ['validation_evidence', '剂量范围验证依据', ''],
      ['bottle_scope_evidence', '跨瓶位共用验证依据', ''],
      ['note', '称量与初始液路备注', ''],
    ],
    '保存本次标定',
    async (d, requestId) => {
      const p = { ...d, request_id: requestId };
      for (const k of [
        'source',
        'sample',
        'doses_per_group',
        'aspirate_steps',
        'aspirate_rpm',
        'chase_steps',
        'chase_rpm',
        'min_steps',
        'max_steps',
      ])
        p[k] = +p[k];
      p.groups_g = d.groups.split(/[,，]/).map(Number);
      p.sample_bottles = d.sample_bottles.split(/[,，]/).map(Number);
      await ctx.api('calibration', p);
      ctx.tell('标定原始读数和适用范围已保存；新项目版本可采用它。');
    },
    'mass-calibration-form',
  );
  [1, 5].forEach((count) =>
    ctx.button(calCard, count === 1 ? '向收集瓶试送1剂' : '向收集瓶试送5剂', async () => {
      const v = (k) => +cal.elements[k].value;
      const pulse = (prefix, min) => ({
        steps: v(prefix + '_steps'),
        rpm: v(prefix + '_rpm'),
        timeout_s: Math.max(
          min,
          Math.ceil((v(prefix + '_steps') * 0.36) / v(prefix + '_rpm') + 10),
        ),
      });
      await ctx.request('/api/action', {
        action: 'stock_calibration',
        source: v('source'),
        sample: v('sample'),
        doses: count,
        aspirate: pulse('aspirate', 20),
        chase: pulse('chase', 35),
      });
      cal.elements.doses_per_group.value = count;
      ctx.tell('试送已启动；停止后记录瓶内净质量。');
    }),
  );
  const compatible = ctx.card(page, '当前项目的标定适用情况');
  const rows = ctx.node('div', null, compatible);
  return {
    render() {
      if (!stockInitialized && (ctx.state.stocks || []).length) {
        stockInitialized = true;
        if (!stockDirty) stock.elements.port.onchange();
      }
      ctx.table(
        densityRows,
        ['口 / 母液', '已记录密度 g/mL', '依据状态', '参考值及条件', '核查提示'],
        (ctx.state.density_audit || []).map((a) => [
          `${a.port} / ${a.name}`,
          a.configured_density_g_ml ?? '未测',
          a.status_label || a.basis,
          a.reference?.density_g_ml != null
            ? `${a.reference.density_g_ml} g/mL；${ctx.fraction(a.reference.mass_fraction)}；${a.reference.temperature_c ?? '?'}℃；${a.reference.note || ''}`
            : a.reference?.note || '无可采用参考值',
          (a.issues || []).join('；'),
        ]),
      );
      for (const a of ctx.state.density_audit || [])
        if (a.reference?.url)
          ctx.node('a', `${a.port}口参考证据`, densityRows, {
            href: a.reference.url,
            target: '_blank',
            rel: 'noopener',
            className: 'density-source-link',
          });
      const records = ctx.state.compatible_calibrations?.[ctx.projectId] || [];
      ctx.table(
        rows,
        ['来源', '当前项目兼容', '状态', '适用步数', '瓶位', '每步估计质量/g', '批次', '依据'],
        records.map((c) => [
          c.source,
          c.compatible === false ? '不兼容' : '兼容',
          { trial: '试用', validated: '已验证', invalid: '失效' }[c.status] || c.status,
          `${c.min_steps ?? '?'}—${c.max_steps ?? c.max_segment_steps ?? '?'}`,
          (c.sample_bottles || []).join('、'),
          c.mass_per_step_g,
          c.stock_batch,
          c.bottle_scope_evidence,
        ]),
      );
      if (!records.length)
        ctx.node('p', '请选择项目；没有兼容标定时，先完成对应来源的标定。', rows);
    },
  };
};
