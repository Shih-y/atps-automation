'use strict';
window.ATPSLabPages.model = (ctx, page) => {
  const card = ctx.card(
    page,
    '相图与下一批选点',
    'RBF 给出启发式分类分数与选点分数，不是校准后的概率。待确认和失败样品不用于监督训练；重复实验保留独立记录。',
  );
  let round = null,
    approvalId = null,
    approvalKey = null;
  const rounds = ctx.node('select', null, card, { id: 'model-round', ariaLabel: '历史模型轮次' });
  const status = ctx.node('p', '', card, { id: 'model-summary' });
  const canvas = ctx.node('canvas', null, card, {
    width: 900,
    height: 520,
    id: 'phase-diagram',
    ariaLabel: '采用组成坐标的相图',
  });
  ctx.node(
    'p',
    '○ 单相可用　● 双相可用　△ 待确认　× 排除/失效　□ 候选；坐标来自记录的采用组成。灰线为启发式分类分数 0.5 附近，未经物理验证。',
    card,
    { className: 'muted' },
  );
  ctx.form(
    card,
    [
      [
        'method',
        '选点方法',
        'active',
        [
          ['active', '主动学习启发式'],
          ['uniform', '均匀覆盖'],
        ],
      ],
      ['count', '候选数量', 5],
      ['peg_min', 'PEG 最小 / %', 0],
      ['peg_max', 'PEG 最大 / %', 30],
      ['salt_min', '盐最小 / %', 0],
      ['salt_max', '盐最大 / %', 20],
      ['step', '网格间隔 / 百分点', 2],
    ],
    '建立新模型轮次与候选',
    async (d, requestId) => {
      if (!ctx.projectId) throw Error('先选择项目');
      const result = await ctx.api('suggest', {
        project_id: ctx.projectId,
        request_id: requestId,
        method: d.method,
        count: +d.count,
        peg_range: [+d.peg_min / 100, +d.peg_max / 100],
        salt_range: [+d.salt_min / 100, +d.salt_max / 100],
        step: +d.step / 100,
      });
      round = result.round;
      approvalId = null;
      drawRound();
      ctx.tell('新轮次已保存，审核候选后加入队列。');
    },
    'suggest-form',
  );
  const approve = ctx.node('form', null, card, { id: 'approve-form', className: 'lab-form' }),
    choices = ctx.node('div', null, approve, { className: 'candidate-list' });
  ctx.node('button', '批准所选候选加入当前项目', approve, { type: 'submit' });
  approve.onsubmit = async (e) => {
    e.preventDefault();
    const b = approve.querySelector('button');
    if (b.disabled) return;
    if (!round || round.project_id !== ctx.projectId) {
      ctx.tell('请为当前项目选择模型轮次。', true);
      return;
    }
    const indices = [...choices.querySelectorAll('input:checked')].map((n) => +n.value);
    if (!indices.length) {
      ctx.tell('先勾选候选。', true);
      return;
    }
    const key = JSON.stringify([round.id, indices]);
    if (key !== approvalKey) {
      approvalKey = key;
      approvalId = null;
    }
    b.disabled = true;
    approvalId ||= crypto.randomUUID();
    try {
      await ctx.api('approve', { round_id: round.id, indices, request_id: approvalId });
      approvalId = null;
      ctx.tell('批准记录和样品队列已保存。');
      await ctx.refresh();
    } catch (err) {
      ctx.tell(err.message, true);
    } finally {
      b.disabled = false;
    }
  };
  const points = ctx.node('div', null, card),
    comparison = ctx.card(
      page,
      '模型轮次与比较条件',
      '比较主动学习和均匀取点时，应使用相同体系、条件、预算和独立评价数据。此表仅记录轮次；候选图不能证明准确率提高。',
    );
  const comparisonTable = ctx.node('div', null, comparison);
  const continuationCard = ctx.card(
    page,
    '同瓶研究的冻结轮次',
    '每次补加保存起始物料版本、采用组成、动作与选点理由。双相邻域探索不测两相具体组成或系线；父观测标签不覆盖子观测。',
  );
  const continuationRounds = ctx.node('div', null, continuationCard, { id: 'continuation-rounds' });
  function candidateCoordinates(candidate) {
    if (Object.hasOwn(candidate, 'composition')) return candidate.composition || {};
    return {
      peg_fraction: candidate.peg_fraction,
      salt_fraction: candidate.salt_fraction,
      basis: 'legacy_target',
    };
  }
  function draw() {
    const g = canvas.getContext('2d');
    g.clearRect(0, 0, 900, 520);
    g.fillStyle = '#fff';
    g.fillRect(0, 0, 900, 520);
    const data = ctx
      .samples()
      .filter(
        (s) =>
          Number.isFinite(s.composition?.peg_fraction) &&
          Number.isFinite(s.composition?.salt_fraction),
      );
    const candidates = (round?.suggestions || [])
        .map(candidateCoordinates)
        .filter((c) => Number.isFinite(c.peg_fraction) && Number.isFinite(c.salt_fraction)),
      grid = round?.grid || [];
    const maxX =
      Math.max(
        0.1,
        ...data.map((s) => s.composition.peg_fraction),
        ...candidates.map((c) => c.peg_fraction),
        ...grid.map((c) => c.peg_fraction),
      ) * 1.12;
    const maxY =
      Math.max(
        0.1,
        ...data.map((s) => s.composition.salt_fraction),
        ...candidates.map((c) => c.salt_fraction),
        ...grid.map((c) => c.salt_fraction),
      ) * 1.12;
    const xy = (a, b) => [70 + (a / maxX) * 790, 450 - (b / maxY) * 410];
    g.font = '14px sans-serif';
    g.strokeStyle = '#444';
    g.fillStyle = '#222';
    g.lineWidth = 1;
    for (let i = 0; i <= 5; i++) {
      const x = 70 + i * 158,
        y = 450 - i * 82;
      g.beginPath();
      g.moveTo(x, 40);
      g.lineTo(x, 450);
      g.moveTo(70, y);
      g.lineTo(860, y);
      g.strokeStyle = '#e2e2e2';
      g.stroke();
      g.fillText((maxX * i * 20).toFixed(1), x - 10, 475);
      g.fillText((maxY * i * 20).toFixed(1), 20, y + 5);
    }
    g.fillText('PEG 质量分数 / %', 390, 505);
    g.fillText('盐质量分数 / %', 15, 22);
    if (round?.model?.two_class_boundary) {
      for (const p of grid) {
        const score = p.classification_score ?? p.score ?? p.probability;
        if (Number.isFinite(score) && Math.abs(score - 0.5) < 0.06) {
          const [x, y] = xy(p.peg_fraction, p.salt_fraction);
          g.fillStyle = '#aaa';
          g.fillRect(x - 2, y - 2, 4, 4);
        }
      }
    }
    for (const s of data) {
      const [x, y] = xy(s.composition.peg_fraction, s.composition.salt_fraction),
        eligible = s.training_eligibility?.eligible,
        label = s.review_state || s.label?.classification;
      g.strokeStyle = '#111';
      g.fillStyle = '#111';
      g.lineWidth = 1.6;
      g.beginPath();
      if (eligible) {
        g.arc(x, y, 5, 0, Math.PI * 2);
        if (label === 'two_phase') g.fill();
        else g.stroke();
      } else if ((s.exclusions || []).some((e) => e.active) || !s.composition.valid) {
        g.moveTo(x - 5, y - 5);
        g.lineTo(x + 5, y + 5);
        g.moveTo(x + 5, y - 5);
        g.lineTo(x - 5, y + 5);
        g.stroke();
      } else {
        g.moveTo(x, y - 6);
        g.lineTo(x - 5, y + 4);
        g.lineTo(x + 5, y + 4);
        g.closePath();
        g.stroke();
      }
    }
    for (const p of candidates) {
      const [x, y] = xy(p.peg_fraction, p.salt_fraction);
      g.strokeStyle = '#666';
      g.strokeRect(x - 7, y - 7, 14, 14);
    }
    // Expose the exact plotted dataset for accessibility and regression checks.
    canvas.dataset.points = JSON.stringify(
      data.map((s) => ({
        sample_id: s.id,
        basis: s.composition.basis,
        peg_fraction: s.composition.peg_fraction,
        salt_fraction: s.composition.salt_fraction,
      })),
    );
    canvas.dataset.candidates = JSON.stringify(candidates);
  }
  function drawRound() {
    status.textContent = round
      ? `轮次 ${round.id.slice(0, 8)}；${round.created_at || ''}；模型 ${round.model?.kind || 'RBF'}；有效训练样品 ${round.summary?.training_count ?? round.training_ids?.length ?? 0}；冻结训练依据、标签版本和参数随本轮保存。${round.model?.two_class_boundary ? '已具备两类标签。' : '尚无两类有效标签，不能解释为边界。'} ${(round.conflicts || round.summary?.conflicts || []).length ? '存在重复点标签冲突，请复核。' : ''}`
      : '尚无本项目模型轮次。';
    if (round?.candidate_filter) {
      const reasons = {
        unexecutable: '不可执行',
        existing_execution: '与历史动作重复',
        duplicate_execution: '候选动作重复',
        existing_composition: '与历史采用组成重复',
        duplicate_composition: '候选采用组成重复',
      };
      const excluded = Object.entries(round.candidate_filter)
        .map(([key, count]) => `${reasons[key] || key} ${count}`)
        .join('、');
      status.append(
        ` 唯一可执行候选 ${round.summary?.executable_candidate_count ?? 0}，本轮推荐 ${round.summary?.suggested_count ?? round.suggestions?.length ?? 0}；已剔除：${excluded}。`,
      );
    }
    choices.replaceChildren();
    (round?.suggestions || []).forEach((c, i) => {
      const l = ctx.node('label', null, choices),
        target = c.target || c,
        adopted = candidateCoordinates(c);
      ctx.node('input', null, l, { type: 'checkbox', name: 'indices', value: String(i) });
      const difference = (key) =>
        Number.isFinite(adopted[key]) && Number.isFinite(target[key])
          ? ((adopted[key] - target[key]) * 100).toFixed(3)
          : '未知';
      const estimate =
        adopted.basis === 'legacy_target'
          ? '历史轮次仅有目标坐标，未保存量化估计'
          : `${ctx.basis(adopted.basis)}：PEG ${ctx.fraction(adopted.peg_fraction)}，盐 ${ctx.fraction(adopted.salt_fraction)}；偏差 ${difference('peg_fraction')} / ${difference('salt_fraction')} 百分点`;
      ctx.node(
        'span',
        `目标：PEG ${ctx.fraction(target.peg_fraction)}，盐 ${ctx.fraction(target.salt_fraction)}\n${estimate}\n计划瓶位：${c.planned_bottle ?? '待分配'}；${{ uniform_coverage: '均匀覆盖', uncertainty_and_coverage: '分类不确定与覆盖距离' }[c.reason] || c.reason}；分类分数 ${(c.classification_score ?? c.probability)?.toFixed(3) ?? '不足两类'}；选点分数 ${c.selection_score?.toPrecision(3) ?? '见冻结记录'}${adopted.valid === false ? '；' + (adopted.reasons || []).join('；') : ''}`,
        l,
        { className: 'candidate-description' },
      );
    });
    draw();
  }
  rounds.onchange = () => {
    round = (ctx.state.rounds || []).find((r) => r.id === rounds.value) || null;
    approvalId = null;
    drawRound();
  };
  return {
    render() {
      ctx.table(
        continuationRounds,
        [
          '研究 / 次数',
          '瓶位',
          '新观测 / 父观测',
          '最终估计PEG / 盐',
          '补加质量 / g',
          '选择理由',
          '轮次状态',
        ],
        ctx.continuationRuns().flatMap((run) =>
          (run.rounds || []).map((r, index) => {
            const c = r.candidate || {},
              sample = (ctx.state.samples || []).find((s) => s.id === r.sample_id),
              composition = c.composition || sample?.composition || {};
            return [
              run.id.slice(0, 8) + ' / ' + (index + 1),
              c.bottle ?? c.planned_bottle ?? sample?.bottle,
              `${r.sample_id?.slice(0, 8) || '未建立'} / ${sample?.parent_sample_id?.slice(0, 8) || '起点'}`,
              ctx.fraction(composition.peg_fraction) +
                ' / ' +
                ctx.fraction(composition.salt_fraction),
              ctx.mass(r.charged_mass_g ?? c.added_mass_g),
              ctx.selectionReason(c.reason || r.reason),
              {
                claimed: '已冻结并占用预算',
                completed: '已完成',
                interrupted: '中断',
                preparing: '执行中',
              }[r.status] ||
                r.status ||
                '已冻结',
            ];
          }),
        ),
      );
      const scoped = (ctx.state.rounds || []).filter((r) => r.project_id === ctx.projectId);
      ctx.options(
        rounds,
        scoped.map((r) => [
          r.id,
          `${r.created_at || r.id.slice(0, 8)} · ${r.summary?.method === 'uniform' ? '均匀覆盖' : '主动学习'}`,
        ]),
        '选择历史轮次',
      );
      if (round && round.project_id !== ctx.projectId) round = null;
      if (!round && scoped.length) {
        round = scoped.at(-1);
        drawRound();
      }
      if (round) {
        round = scoped.find((r) => r.id === round.id) || round;
        rounds.value = round.id;
      }
      ctx.table(
        points,
        ['样品', '采用依据', 'PEG', '盐', '与目标偏差 / 百分点', '标签', '训练资格'],
        ctx.samples().map((s) => {
          const c = s.composition || {};
          return [
            ctx.sampleName(s),
            ctx.basis(c.basis),
            ctx.fraction(c.peg_fraction),
            ctx.fraction(c.salt_fraction),
            `${((c.peg_fraction - s.target?.peg_fraction) * 100).toFixed(3)} / ${((c.salt_fraction - s.target?.salt_fraction) * 100).toFixed(3)}`,
            { single: '单相', two_phase: '双相', uncertain: '待确认', unreviewed: '未复核' }[
              s.review_state
            ] || '未复核',
            s.training_eligibility?.eligible
              ? '可用'
              : (s.training_eligibility?.reasons || []).join('；'),
          ];
        }),
      );
      ctx.table(
        comparisonTable,
        ['轮次', '方法', '训练数', '候选数', '已批准样品数', '创建时间'],
        scoped.map((r) => [
          r.id.slice(0, 8),
          r.summary?.method === 'uniform' ? '均匀覆盖' : '主动学习启发式',
          r.summary?.training_count,
          r.suggestions?.length,
          ctx.samples().filter((s) => s.selection?.round_id === r.id).length,
          r.created_at,
        ]),
      );
      draw();
      if (!round) drawRound();
    },
    projectChanged() {
      round = null;
      approvalId = null;
      drawRound();
    },
  };
};
