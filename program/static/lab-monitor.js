'use strict';
window.ATPSLabPages.monitor = (ctx, page) => {
  const observationReasons = {
    awaiting_photographs: '等待观察照片',
    missing_frames: '尚无观察照片',
    invalid_observation_window: '观察时间窗口无效',
    no_current_generation_frames: '本观察阶段尚无照片',
    sample_identity_mismatch: '照片与样品编号不一致',
    invalid_frame_observation_time: '照片拍摄时刻不在有效观察窗口内',
    invalid_frame_provenance: '照片来源或映射记录无效',
    duplicate_frame_evidence: '证据照片重复，不能重复计数',
    missing_analysis: '照片缺少光学分析',
    unsupported_analysis_method: '光学分析方法不适用',
    analysis_roi_mismatch: '分析区域与照片映射不一致',
    missing_bottle_analysis: '缺少此瓶的光学分析',
    mapping_or_roi_changed: '瓶位映射或分析区域已改变，须重新收集稳定证据',
    unstable_frame_sequence: '连续照片尚不稳定',
    insufficient_frame_spacing: '证据照片间隔不足',
    insufficient_stable_frames: '稳定证据照片数量不足',
    single_phase_criterion_unvalidated: '单相判据未经独立验证，未见界面仍为未知',
    no_interface_unvalidated: '单相判据未经独立验证，未见界面仍为未知',
    stable_optical_single: '符合已验证单相判据的稳定光学候选',
    stable_optical_two_phase: '稳定双相光学候选',
    human_review_authoritative: '已采用人工复核结论',
    observation_window_expired: '观察时间窗口已结束',
    retry_budget_exhausted: '自动补拍次数已用尽',
    no_retry_within_window: '剩余观察时间不足以再次补拍',
    missing_quality: '缺少图像质量记录',
    invalid_quality: '图像质量记录无效',
    quality_flags: '图像质量检查未通过',
    invalid_quality_glare_fraction: '图像反光比例不符合要求',
    invalid_quality_texture_fraction: '图像纹理比例不符合要求',
    invalid_quality_edge_sharpness: '图像清晰度不符合要求',
    invalid_quality_contrast_range: '图像对比度不符合要求',
    invalid_quality_interface_contrast: '界面对比度记录无效',
    invalid_quality_horizontal_support: '水平界面支持度记录无效',
    invalid_quality_signature: '图像稳定性特征记录无效',
    unstable_or_unavailable_quality: '图像不稳定或缺少稳定性信息',
    uncertain_optical_classification: '光学判相仍不确定',
    invalid_inspected_region: '有效分析区域记录无效',
    weak_interface: '候选界面强度不足',
    invalid_interface: '候选界面位置无效',
    phase_fraction_out_of_bounds: '候选相层高度不符合工程阈值',
    contradictory_single_phase_evidence: '单相候选与界面证据矛盾',
  };
  const observationReasonText = (codes) =>
    (codes || []).map((code) => observationReasons[code] || code).join('；');
  const runState = (status) =>
    ({
      waiting_observation: '等待自动观察',
      observation_complete: '观察完成',
      exhausted: '研究已结束（候选或条件耗尽）',
    }[status] || ctx.runState(status));
  const autoStatus = (status) =>
    ({ pending: '等待证据', candidate: '光学候选', unknown: '未知', human_reviewed: '人工复核' }[status] || status || '未启用');
  const labelSource = (s) =>
    s.label?.source === 'automatic_optical'
      ? '自动光学候选 (automatic_optical)'
      : s.label ? '人工复核 (human)' : '尚无标签';
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
    waiting_observation: '等待自动观察',
    exhausted: '观察机会耗尽',
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
                ? '按适用标定估计；下一步须满足所选研究的判相策略'
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
          '标签来源',
          '自动观察 / 已用补拍次数 / 原因 / 证据',
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
            labelSource(s),
            s.auto_observation
              ? `${autoStatus(s.auto_observation.status)}；补拍 ${s.auto_observation.retries_used ?? 0} 次；${observationReasonText(s.auto_observation.reason_codes)}；证据 ${(s.auto_observation.evidence_frame_ids || []).join('、') || '无'}`
              : '人工模式 / 无自动记录',
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
            `${runState(r.status)} · ${(r.bottles || []).join('、')}号瓶 · ${r.id.slice(0, 8)}`,
          ]),
        '尚无同瓶研究',
      );
      runSelect.value = ctx.continuationRunId || '';
      const run = ctx.continuationRun();
      runInfo.replaceChildren();
      if (run) {
        ctx.node(
          'p',
          `${runState(run.status)}；${run.reason || '按瓶调度'}。墙钟时间预算含等待人工复核与自动观察；暂停不应理解为时间预算停止。`,
          runInfo,
        );
        const policy = run.observation_policy || { mode: 'human' };
        ctx.node('p', `已冻结观察策略：${policy.mode === 'automatic' ? '自动光学候选 (automatic)' : '人工复核 (human)'}；最多补拍 ${policy.max_auto_retries ?? 2} 次，间隔 ${policy.retry_interval_s ?? 60} s；稳定 ${policy.stable_frames ?? 2} 帧，帧间隔至少 ${policy.min_frame_spacing_s ?? 30} s；界面位移上限 ${policy.max_interface_shift_fraction ?? 0.03}，最小相层高度比例 ${policy.min_phase_fraction ?? 0.1}。工程阈值不代表已验证相平衡。${policy.single_phase_validated ? '单相独立验证证据：' + (policy.single_phase_evidence || '未提供') : '单相判据未验证；未见界面仍为未知。'}`, runInfo);
        ctx.node('p', `首次成像最低质量 ${ctx.mass(run.min_initial_mass_g ?? run.budgets?.min_initial_mass_g ?? p?.total_mass_g)} g；预留容量 ${ctx.mass(run.reserve_mass_g ?? run.budgets?.reserve_mass_g ?? 0)} g。`, runInfo);
        if (Object.keys(run.budgets?.source_mass_budget_g || {}).length) {
          const sources = ctx.node('div', null, runInfo);
          ctx.table(
            sources,
            ['来源口', '母液预算 / g', '已用/保守占用估计 / g', '剩余 / g'],
            Object.entries(run.budgets.source_mass_budget_g).map(([port, mass]) => [
              port,
              ctx.mass(mass),
              ctx.mass(run.used?.source_mass_g?.[port] ?? 0),
              ctx.mass(run.remaining?.source_mass_g?.[port]),
            ]),
          );
        }
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
                        ? `${labelSource(sample)}有效，可规划下一剂`
                        : sample.auto_observation
                          ? `${autoStatus(sample.auto_observation.status)}；补拍 ${sample.auto_observation.retries_used ?? 0}/${policy.max_auto_retries ?? 2}；${observationReasonText(sample.auto_observation.reason_codes)}`
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
