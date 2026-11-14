'use strict';
window.ATPSLabPages.monitor = (ctx, page) => {
  const card = ctx.card(
    page,
    '当前项目运行范围',
    '开始时固定项目范围。切换页面项目不会改变正在运行的批次；立即停止作用于设备当前运动。',
  );
  const scope = ctx.node('p', '', card, { id: 'start-preview' });
  ctx.button(
    card,
    '开始当前项目',
    async () => {
      if (!ctx.projectId) throw Error('先选择项目');
      await ctx.api('start', { project_id: ctx.projectId });
      ctx.tell('当前项目批次已启动，实际运行范围见设备栏。');
    },
    'start-current-project',
  );
  ctx.button(card, '暂停后续任务', async () => {
    await ctx.api('pause', {});
    ctx.tell('已暂停后续任务；当前瓶继续完成。');
  });
  const stop = ctx.node('button', '立即停止 · 保留连接', card, { type: 'button' });
  stop.onclick = () => ctx.stop();
  const vial = ctx.node('div', null, card, { id: 'inventory-table' }),
    rows = ctx.node('div', null, card);
  const continuation = ctx.card(
    page,
    '同瓶研究与累计物料',
    '泵阀串行，等待复核按瓶独立。累计质量来自标定与已完成动作估计，未做独立组分测量时不是实测组成。',
  );
  const runSelect = ctx.node('select', null, continuation, {
    id: 'continuation-monitor-select',
    ariaLabel: '监控同瓶研究',
  });
  runSelect.onchange = () => {
    ctx.continuationRunId = runSelect.value;
    ctx.render();
  };
  const runInfo = ctx.node('div', null, continuation, { id: 'continuation-monitor' });
  ctx.button(
    continuation,
    '暂停后续补加',
    async () => {
      await ctx.continuationCommand('pause');
      ctx.tell('已暂停后续补加；已开始的有限动作按服务状态处理。');
    },
    'continuation-pause',
  );
  ctx.button(
    continuation,
    '恢复所选同瓶研究',
    async () => {
      await ctx.continuationCommand('resume');
      ctx.tell('已请求恢复，仍核对剩余墙钟预算及各瓶有效复核。');
    },
    'continuation-resume',
  );
  const ops = ctx.card(
    page,
    '排队、重配与换瓶',
    '取消排队不转泵。中断样品重配会建立新样品，不自动补足未知交付量。换瓶前先保留样品。',
  );
  const select = ctx.node('select', null, ops, { id: 'monitor-sample' });
  ctx.button(ops, '取消所选未执行样品', async () => {
    await ctx.api('cancel', { sample_id: select.value });
    ctx.tell('已取消预约，无泵阀动作。');
  });
  const remakeReason = ctx.node('input', null, ops, {
    id: 'remake-reason',
    placeholder: '填写重配原因',
    ariaLabel: '重配原因',
  });
  let remakeId = null,
    remakeSample = null;
  ctx.button(ops, '重配为新样品', async () => {
    if (!remakeReason.value.trim()) throw Error('请填写重配原因');
    if (remakeSample !== select.value) {
      remakeId = null;
      remakeSample = select.value;
    }
    remakeId ||= crypto.randomUUID();
    await ctx.api('remake', {
      sample_id: select.value,
      request_id: remakeId,
      reason: remakeReason.value.trim(),
    });
    remakeId = null;
    ctx.tell('已建立新的重配样品；原记录保留。');
  });
  const release = ctx.form(
    ops,
    [['bottle', '瓶位', 1, [1, 2, 3, 4, 5].map((n) => [n, n + '号瓶'])]],
    '确认换好干净空瓶',
    async (d, requestId) => {
      if (!release.elements.confirmed_empty.checked) throw Error('确认已更换干净空瓶后再释放');
      await ctx.api('release', { bottle: +d.bottle, confirmed_empty: true, request_id: requestId });
      release.elements.confirmed_empty.checked = false;
      ctx.tell('瓶位已释放；历史样品与照片保留。');
    },
    'release-form',
  );
  const l = ctx.node('label', '我已放入干净空瓶', release);
  ctx.node('input', null, l, { type: 'checkbox', name: 'confirmed_empty', required: true });
  const names = {
    queued: '排队',
    preparing: '配液中',
    settling: '静置中',
    observation_complete: '观察完成',
    interrupted: '中断',
    cancelled: '已取消',
    released: '历史样品',
  };
  return {
    render() {
      const p = ctx.project(),
        previews = ctx.state.start_previews || {},
        preview = Array.isArray(previews)
          ? previews.find((v) => v.project_id === ctx.projectId)
          : previews[ctx.projectId];
      const samples = ctx.samples(),
        queued = samples.filter((s) => (s.execution_state || s.status) === 'queued');
      scope.textContent = p
        ? `将启动：${p.name}；PEG＋${p.stocks?.find((s) => s.port === p.salt_port)?.name || '盐' + p.salt_port + '口'}＋水；待执行${preview?.count ?? queued.length}个样品；预约瓶位：${(preview?.bottles || queued.map((s) => s.bottle).filter(Boolean)).join('、') || '等待空瓶'}。${(preview?.issues || []).join('；')}`
        : '请先选择项目';
      ctx.table(
        vial,
        [
          '瓶位',
          '占用状态',
          '关联样品',
          '估计累计 / g',
          '距项目质量上限 / g',
          '物料依据 / 续加条件',
        ],
        (ctx.state.bottles || []).map((b) => [
          b.bottle,
          { free: '空闲', assigned: '已分配', occupied: '占用', needs_replacement: '待换瓶' }[
            b.state
          ] ||
            b.state ||
            '未知',
          b.sample_id?.slice(0, 8) || '无',
          b.inventory?.known ? Number(b.inventory.total_mass_g).toFixed(4) : '未知',
          b.inventory?.known && Number.isFinite(p?.safe_mass_limit_g)
            ? (p.safe_mass_limit_g - b.inventory.total_mass_g).toFixed(4)
            : '未知',
          !b.inventory?.known
            ? b.inventory?.reason || '缺少可靠物料记录，不能定量续加'
            : b.inventory.total_mass_g === 0
              ? '已确认干净空瓶，可首次配样'
              : b.inventory.calibration_valid
                ? '按适用标定估计；下一步仍须有效人工复核'
                : '现有物料缺少适用标定依据',
        ]),
      );
      ctx.table(
        rows,
        [
          '样品',
          '执行',
          '当前来源/步骤',
          '指令进度（非实测质量）',
          '静置/s',
          '下一照片',
          '判相复核',
          '训练资格',
        ],
        samples.map((s) => {
          const r = s.progress || {},
            c = r.current;
          const row = c && r.items?.[c.item_index];
          const job = (ctx.state.photo_jobs || [])
            .filter(
              (j) =>
                j.sample_id === s.id && ['waiting', 'due', 'missed', 'failed'].includes(j.status),
            )
            .sort((a, b) => a.due_at - b.due_at)[0];
          return [
            ctx.sampleName(s),
            names[s.execution_state || s.status] || s.status,
            c
              ? `${row?.source || '瓶末'}口 / ${{ aspirate: '吸取', chase: '追送', mixing: '混合', purge: '吹扫' }[c.phase] || c.phase}`
              : '—',
            `${r.completed_commands || 0}/${r.total_commands || '—'}`,
            s.mixed_at
              ? Math.max(0, Math.floor((Date.now() - Date.parse(s.mixed_at)) / 1000))
              : '未开始',
            job ? new Date(job.due_at * 1000).toLocaleTimeString() + ' ' + job.status : '无',
            { single: '单相', two_phase: '双相', uncertain: '待确认', unreviewed: '未复核' }[
              s.review_state
            ] || '未复核',
            s.training_eligibility?.eligible
              ? '可用'
              : (s.training_eligibility?.reasons || []).join('；') || '待核对',
          ];
        }),
      );
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
      runInfo.replaceChildren();
      if (run) {
        ctx.node(
          'p',
          `${ctx.runState(run.status)}；${run.reason || '按瓶调度'}。墙钟时间预算含等待人工复核；暂停不应理解为时间预算停止。`,
          runInfo,
        );
        const budget = ctx.node('div', null, runInfo);
        ctx.table(
          budget,
          ['预算', '已用/保守占用', '剩余'],
          [
            ['补加次数', run.used?.rounds ?? 0, run.remaining?.rounds ?? '未知'],
            [
              '补加质量 / g',
              ctx.mass(run.used?.added_mass_g ?? 0),
              ctx.mass(run.remaining?.added_mass_g),
            ],
            [
              '墙钟时间 / s',
              Number.isFinite(run.used?.elapsed_s)
                ? run.used.elapsed_s.toFixed(0)
                : Number.isFinite(run.remaining?.time_s)
                  ? Math.max(0, (run.budgets?.time_budget_s ?? 0) - run.remaining.time_s).toFixed(0)
                  : '未启动',
              Number.isFinite(run.remaining?.time_s) ? run.remaining.time_s.toFixed(0) : '未知',
            ],
          ],
        );
        const bottles = ctx.node('div', null, runInfo);
        ctx.table(
          bottles,
          ['研究瓶位', '下一次补加条件 / 等待原因'],
          (run.bottles || []).map((b) => {
            const bottle = (ctx.state.bottles || []).find((x) => x.bottle === b),
              sample = (ctx.state.samples || []).find((s) => s.id === bottle?.sample_id);
            const reason = run.waiting_reasons?.[b] || run.bottle_states?.[b]?.reason;
            return [
              b,
              reason ||
                (!bottle?.inventory?.known
                  ? '瓶内物料未知'
                  : !sample
                    ? '已确认空瓶，可建立首次观测'
                    : sample.execution_state === 'preparing'
                      ? '正在执行本轮动作'
                      : sample.training_eligibility?.eligible
                        ? '人工有效复核完成，可规划下一剂'
                        : (sample.training_eligibility?.reasons || ['等待照片与有效人工复核']).join(
                            '；',
                          )),
            ];
          }),
        );
        if (run.next_candidate) {
          const c = run.next_candidate;
          ctx.node(
            'p',
            `最近冻结方案，瓶 ${c.bottle ?? c.planned_bottle ?? '?'}：PEG ${ctx.fraction(c.composition?.peg_fraction ?? c.peg_fraction)}，盐 ${ctx.fraction(c.composition?.salt_fraction ?? c.salt_fraction)}；理由 ${ctx.selectionReason(c.reason)}。`,
            runInfo,
          );
        }
      } else ctx.node('p', '没有已建立的同瓶研究。', runInfo);
      ctx.selectSample(select);
    },
  };
};
