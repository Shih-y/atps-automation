'use strict';
// Endpoint volume records remain separate from the formal mass calibration.
window.ATPSSourceVolume = (ctx, page) => {
  const ports = [10, 11, 12, 13, 14, 15].map(n => [n, `${n}口`]);
  const bottles = Object.entries(window.ATPSBottleRouting || {1:1,2:2,3:3,4:4,5:5,6:6}).map(([n,p]) => [Number(n), `${n}号瓶（阀${p}口）`]);
  const card = ctx.card(page, '各来源样品端体积标定（手动 mL 试用）',
    '①先在上方保存本批次母液及温度，确认预充和管口位置。②在实际样品管出口收集，每组倒空并去皮，点击试送后等停止。③填写每组收集总量并保存。保存和预览均不启动泵；正式质量实验仍使用独立的 g/步标定。');
  const positive = (raw, label, integer = false) => {
    const n = Number(raw);
    if (!String(raw).trim() || !Number.isFinite(n) || n <= 0 || (integer && !Number.isInteger(n)))
      throw Error(`${label}须为有限正${integer ? '整数' : '数值'}`);
    return n;
  };
  const finite = (raw, label) => {
    if (!String(raw).trim() || !Number.isFinite(Number(raw))) throw Error(`${label}须为有限数值`);
    return Number(raw);
  };
  function motion(d) {
    const pulse = prefix => ({steps: positive(d[prefix + '_steps'], '步数', true), rpm: positive(d[prefix + '_rpm'], '转速'), timeout_s: positive(d[prefix + '_timeout_s'], '超时')});
    const doses = positive(d.doses, '每组剂数', true);
    if (doses > 20) throw Error('每组最多20剂');
    const chase=d.chase_unit==='turns'?{turns:positive(d.chase_turns,'追送整圈数',true),rpm:positive(d.chase_rpm,'追送转速'),timeout_s:positive(d.chase_timeout_s,'超时')}:pulse('chase');
    return {source: Number(d.source), sample: Number(d.sample), doses, aspirate: pulse('aspirate'), chase};
  }
  const data = form => Object.fromEntries(new FormData(form));
  let readingBinding = null, collectionRunId = null, collectionBeforeId = null, collectionPending = false, readingStock = null;
  const binding = d => JSON.stringify([motion(d), d.temperature_c, d.reading_unit, d.density_g_ml, d.density_evidence]);
  const form = ctx.form(card, [
    ['source', '来源口', 10, ports], ['sample', '实际收集瓶', 1, bottles],
    ['doses', '每组试送剂数（1—20）', 1],
    ['aspirate_steps', '每剂吸取步数', 45], ['aspirate_rpm', '吸取 / rpm', 5], ['aspirate_timeout_s', '吸取超时 / s', 30],
    ['chase_steps', '每剂追送步数', 2000], ['chase_rpm', '追送 / rpm', 30], ['chase_timeout_s', '追送超时 / s', 40],
    ['chase_unit', '追送方式（与实际加液一致）', 'steps', [['steps', '有限步数'], ['turns', '原生整圈（PEG本次为30圈）']]], ['chase_turns', '整圈方式：每剂追送圈数', 30],
    ['temperature_c', '本次温度 / ℃（与母液记录一致）', 25],
    ['reading_unit', '原始读数单位', 'ml', [['ml', '每组总收集体积 / mL'], ['g', '每组总净质量 / g（须实测密度）']]],
    ['groups', '各组总读数（逗号分隔；不是每剂量）', ''],
    ['density_g_ml', '仅称重换算：本批次实测密度 / g·mL⁻¹', ''], ['density_evidence', '仅称重换算：密度测量依据', ''],
    ['range_mode', '步数适用范围', 'point', [['point', '仅本次实测步数'], ['proportional', '允许指定步数范围内按比例试用（精度未验证）']]],
    ['min_steps', '按比例试用最小吸取步数', 1], ['max_steps', '按比例试用最大吸取步数', 240],
    ['sample_bottles', '适用瓶位（逗号分隔；默认本次瓶）', ''], ['bottle_scope_evidence', '跨瓶位共用验证依据（多瓶必填）', ''],
    ['note', '液路初始状态、出口高度及测量备注', ''],
  ], '保存本来源体积参考（不动作）', async d => {
    const payload = motion(d);
    if (collectionPending && !collectionRunId) {
      const hardware = await ctx.request('/api/state');
      const run = hardware.recipe_run;
      if (!run?.id || run.id === collectionBeforeId || run.method !== 'sample_endpoint_collection') throw Error('尚未读到本次样品端收集记录，请等试送完成后再保存');
      collectionRunId = run.id;
    }
    if (collectionRunId) payload.collection_run_id = collectionRunId;
    const currentStock = (ctx.state.stocks || []).find(s => Number(s.port) === payload.source);
    if (readingStock) {
      if (JSON.stringify(currentStock) !== JSON.stringify(readingStock)) throw Error('收集读数对应的母液版本或批次已改变；请清空旧读数并重新收集');
      payload.stock_version = readingStock.version;
    } else if (!collectionRunId) throw Error('尚未读取读数对应的母液版本；请等母液信息载入后清空并重新填写读数');
    if (readingBinding && readingBinding !== binding(d)) throw Error('收集参数或读数单位已改变；请恢复本轮参数，或清空读数后重新收集');
    const groups = d.groups.split(/[,，]/).map(s => positive(s, '各组总读数'));
    payload[d.reading_unit === 'g' ? 'groups_g' : 'groups_ml'] = groups;
    if (d.reading_unit === 'g') {
      payload.density_g_ml = positive(d.density_g_ml, '本批次实测密度');
      if (!d.density_evidence.trim()) throw Error('请填写密度测量依据，并先保存母液密度信息');
      payload.density_evidence = d.density_evidence.trim();
    }
    Object.assign(payload, {temperature_c: finite(d.temperature_c, '温度'), note: d.note,
      sample_bottles: d.sample_bottles.trim() ? d.sample_bottles.split(/[,，]/).map(s => positive(s, '瓶位', true)) : [payload.sample], bottle_scope_evidence: d.bottle_scope_evidence});
    if (d.range_mode === 'proportional') {
      payload.min_steps = positive(d.min_steps, '最小步数', true);
      payload.max_steps = positive(d.max_steps, '最大步数', true);
      if (payload.min_steps > payload.aspirate.steps || payload.max_steps < payload.aspirate.steps) throw Error('试用范围须包含本次实测步数');
    }
    const r = await ctx.request('/api/source-volume-calibration', payload);
    invalidate(); await refreshRecords();
    ctx.tell(`已保存 ${payload.source}口第${r.reference.version}版体积试用参考；未启动泵。`);
  }, 'source-volume-form');
  const bindingNote = ctx.node('p', '填写读数时会绑定当前来源母液版本；更换批次后须重新收集。', card, {id: 'source-volume-reading-binding', className: 'muted'});
  form.elements.groups.addEventListener('input', () => {
    if (!form.elements.groups.value.trim()) { readingBinding = null; collectionRunId = null; collectionPending = false; readingStock = null; bindingNote.textContent = '已清空读数；下次填写将绑定当前来源母液版本。'; }
    else {
      if (!readingStock) {
        const stock = (ctx.state.stocks || []).find(s => Number(s.port) === Number(form.elements.source.value));
        if (stock?.version != null) {
          readingStock = JSON.parse(JSON.stringify(stock));
          bindingNote.textContent = `本轮读数绑定 ${stock.port}口母液 v${stock.version}；批次 ${stock.batch || '未填写'}。`;
        } else bindingNote.textContent = '尚未读取当前来源母液版本；未绑定版本，请等载入后清空并重新填写读数。';
      }
      if (!readingBinding) { try { readingBinding = binding(data(form)); } catch (_) { /* Save reports invalid parameters. */ } }
    }
  });
  ctx.button(card, '按上方参数试送一组（会动作）', async () => {
    const d = data(form), parameters = motion(d);
    if (form.elements.groups.value.trim() && readingBinding !== binding(d)) throw Error('请先清空旧读数，再按新参数收集');
    collectionBeforeId = (await ctx.request('/api/state')).recipe_run?.id || null;
    const response = await ctx.request('/api/action', {action: 'stock_calibration', ...parameters});
    collectionPending = true;
    const returnedRun = response.state?.recipe_run || response.recipe_run;
    collectionRunId = returnedRun?.method === 'sample_endpoint_collection' && returnedRun.id !== collectionBeforeId ? returnedRun.id : null;
    readingBinding = binding(d);
    ctx.tell('已请求试送一组；等设备停止后，记录样品端本组总收集量。');
  }, 'source-volume-collect');
  ctx.node('p', '一个步数点的实测均值不能证明较大剂量准确；按比例试用范围不等于验证范围。组间重复读数也不能证明每一单剂的重复性。称重换算必须先记录该批次实测密度，不能假定密度为1。', card, {className: 'muted'});
  const records = ctx.node('div', '', card, {id: 'source-volume-records'});
  let fetching = false;
  async function refreshRecords() {
    if (fetching) return;
    fetching = true;
    try {
      const state = await ctx.request('/api/state');
      ctx.table(records, ['来源 / 版本', '原始每组总量', '实测每剂均值', '条件与适用范围', '证据限制'], (state.source_volume_calibrations || []).map(r => [
        `${r.source}口 / v${r.version} / ${r.id}`,
        `体积 mL: ${(r.groups_ml || []).join('、')}；每组${r.doses ?? '?'}剂${r.raw_evidence?.groups_g ? `；原始净质量 g: ${r.raw_evidence.groups_g.join('、')}；实测密度 ${r.raw_evidence.density_g_ml} g/mL；${r.raw_evidence.density_evidence}` : ''}`,
        `${r.mean_per_dose_ml} mL；${r.aspirate?.steps}步`,
        `批次 ${r.stock_batch || '未记录'}；${r.temperature_c}℃；瓶${(r.sample_bottles || []).join('、')}；${r.min_steps}—${r.max_steps}步；吸取 ${r.aspirate?.rpm} rpm；追送 ${r.chase?.turns!=null?r.chase.turns+'圈':r.chase?.steps+'步'} / ${r.chase?.rpm} rpm`,
        `${r.collection_basis === 'completed_controller_collection' ? '已关联完成的试送记录' : '人工录入的样品端读数'}；${r.range_basis === 'measured_single_dose_only' ? '仅实测步数点' : '指定范围按比例试用'}；剂量精度与单剂重复性未验证。当前条件是否适用，请用下方预览核查。`,
      ]));
      if (!(state.source_volume_calibrations || []).length) ctx.node('p', '尚无各来源样品端体积参考。', records);
    } catch (e) { records.textContent = '读取体积参考失败：' + e.message; }
    finally { fetching = false; }
  }
  const planCard = ctx.card(page, '指定 mL 预览与单来源手动试送', '先预览当前来源与瓶位的适用情况。预览不动作；实际体积仍须测量。每次执行由服务重新检查当前标定。');
  let accepted = null;
  const plan = ctx.node('p', '尚未预览。', planCard, {id: 'source-volume-plan', className: 'result', role: 'status'});
  const pf = ctx.form(planCard, [['source', '来源口', 10, ports], ['sample', '目标瓶', 1, bottles], ['volume_ml', '本次请求 / mL', .2]], '预览步数与适用情况（不动作）', async d => {
    invalidate();
    const payload = {source: Number(d.source), sample: Number(d.sample), volume_ml: positive(d.volume_ml, '请求体积')};
    try {
      const r = await ctx.request('/api/source-volume-preview', payload);
      // A response belongs only to the parameters submitted, never to newer edits.
      if (JSON.stringify(data(pf)) !== JSON.stringify(d)) return;
      const p = r.plan;
      plan.textContent = `${p.calibration_basis === 'source_endpoint_trial' ? '本来源样品端实测参考（试用）' : p.calibration_basis === 'peg_operator_report_trial' ? 'PEG实收0.42 mL比例修正（新追送待复测）' : '旧清水参考（母液未验证）'}；参考 ${p.reference?.id || p.calibration_reference_id || p.source_volume_calibration_id || '见服务记录'}。请求 ${payload.volume_ml} mL；整数吸取 ${p.total_steps}步；估计 ${p.estimated_ml} mL。\n${p.uncertainty_note || '比例换算仅是估计；实际体积与单剂重复性未验证。'}`;
      const segments = (p.parameters || []).map((segment, i) => `第${i + 1}次：吸取 ${segment.aspirate?.steps}步 / ${segment.aspirate?.rpm} rpm，追送 ${segment.chase?.turns!=null?segment.chase.turns+'圈':segment.chase?.steps+'步'} / ${segment.chase?.rpm} rpm。`);
      plan.textContent += `\n一次吸取步数：${(p.chunks || []).join('、') || p.total_steps}。\n${segments.join('\n')}\n单来源加液不自动混合或清洗；组合加料末尾才清洗。`;
      accepted = payload; run.disabled = false;
    } catch (e) { plan.textContent = '不适用 / 无法预览：' + e.message; throw e; }
  }, 'source-volume-preview-form');
  const run = ctx.button(planCard, '执行已预览的单来源加液（会动作）', async () => {
    if (!accepted) throw Error('请先预览');
    await ctx.request('/api/action', {action: 'volume', ...accepted});
    ctx.tell('已请求单来源加液；实际体积待测。');
  }, 'source-volume-run');
  run.disabled = true;
  function invalidate() { accepted = null; run.disabled = true; plan.textContent = '参数或参考已改变 / 尚未预览，请重新预览。'; }
  pf.addEventListener('input', invalidate);pf.addEventListener('change', invalidate);
  return {render: refreshRecords};
};
