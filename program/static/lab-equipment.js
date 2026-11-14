'use strict';
window.ATPSLabPages = window.ATPSLabPages || {};
window.ATPSLabPages.equipment = (ctx, page) => {
  const stockCard = ctx.card(
    page,
    '母液信息',
    '质量分数w/w；未知密度留空。保存新批次会产生新版本。',
  );
  const sources = [10, 11, 12, 13, 14, 15].map((n) => [n, n + '口']);
  const stock = ctx.form(
    stockCard,
    [
      ['port', '来源口', 10, sources],
      ['name', '母液名称', '柠檬酸三钠'],
      ['solute', '溶质名称', 'sodium_citrate'],
      ['percent', '质量分数 / %', 30],
      ['batch', '批次', ''],
      ['temperature_c', '条件温度 / ℃', 25],
      ['density_g_ml', '实测密度 / g·mL⁻¹', ''],
      ['density_evidence', '密度测量依据', ''],
    ],
    '保存母液新版本',
    async (d, requestId) => {
      await ctx.api('stock', {
        ...d,
        request_id: requestId,
        port: +d.port,
        mass_fraction: +d.percent / 100,
        temperature_c: +d.temperature_c,
        density_g_ml: d.density_g_ml ? +d.density_g_ml : null,
      });
      ctx.tell('母液信息已保存。');
    },
    'stock-form',
  );
  stock.elements.port.onchange = () => {
    const s = (ctx.state.stocks || []).find((s) => s.port === +stock.elements.port.value);
    if (s)
      for (const [k, v] of Object.entries({ ...s, percent: s.mass_fraction * 100 }))
        if (stock.elements[k]) stock.elements[k].value = v ?? '';
  };
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
