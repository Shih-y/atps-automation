'use strict';

// Pure-water bench work has its own records; it never signs a stock profile.
(() => {
  const host = $('benchWizard');
  if (!host) return;
  host.innerHTML = `
    <h2>清水试送与交付量校准</h2>
    <p>使用现有0.01 g天平。每转出水量已保存，无需重复测量。按<strong>5剂一组、共3组</strong>校正0.15 mL的平均交付量。准备清水、废液容器、可称重的小瓶；15口放清水，16口接清洁空气。</p>
    <p class="note">这里可以直接做清水试验，无需先填写母液档案或签核清洗。记录可在连接状态下保存。全程只用清水；水校准结果不能直接作为PEG或其他母液的交付校准。</p>
    <p id="benchStatus" class="statuschecks" aria-live="polite"></p>
    <div class="formblock">
      <h3>1 · 先让清水从C端连续流出</h3>
      <p>C端对准废液，出口保持畅通。点击灌管，停泵后看C端；尚未连续出水可再运行一次。15口支管已预先充满时可跳过，本次不转泵；空管时不要跳过，否则会先吸入空气。</p>
      <p id="benchFillPlan" class="mini timing"></p>
      <div class="actions"><button id="benchFillBtn" class="primary">15口清水灌管</button><button id="benchWaterSeenBtn">已看到C端连续出水</button><button id="benchSkipFillBtn">跳过第1步：15口支管已经充满</button></div>
    </div>
    <div class="formblock">
      <h3>2 · 每转出水量已保存，可直接跳过</h3><p id="benchBulkReference" class="note statuschecks"></p><details id="bulkRecalibration"><summary>可选：重新校准C端每转出水量</summary>
      <p>把空收集瓶放上天平，按去皮；取下后放到C端收水。每次完成后称净质量并填入一格。再倒空、去皮，重复收集，三次使用相同参数。</p>
      <p id="benchCollectPlan" class="mini timing"></p>
      <button id="benchCollectBtn">瓶已去皮，C端收集5转清水</button>
      <div class="row three">
        <label class="field" for="benchBulk1">第1次净质量 / g<input id="benchBulk1" type="number" min="0" step="0.01" placeholder="实测值"></label>
        <label class="field" for="benchBulk2">第2次净质量 / g<input id="benchBulk2" type="number" min="0" step="0.01" placeholder="实测值"></label>
        <label class="field" for="benchBulk3">第3次净质量 / g<input id="benchBulk3" type="number" min="0" step="0.01" placeholder="实测值"></label>
      </div>
      <details><summary>称重换算设置</summary><div class="row">
        <label class="field" for="benchDensity">清水密度 / g·mL⁻¹<input id="benchDensity" type="number" min="0.95" max="1.05" step="0.0001" value="1.0000"></label>
        <label class="field" for="benchNote">记录备注<input id="benchNote" placeholder="如水温、管口高度、观察情况"></label>
      </div><p class="subtle">1.0000为便于初调的近似值，可按水温修改。净质量÷密度＝收集体积；体积÷5转＝每转出水量。天平分度固定为0.01 g。</p></details>
      <div class="actions"><button id="benchBulkReportBtn">计算并保存为新的每转出水量</button></div>
      <p id="benchBulkReport" class="note statuschecks" aria-live="polite">仅在需要重新测量时填写3次净质量；保存后下次自动沿用。</p></details>
    </div>
    <div class="formblock">
      <h3>3 · 排出公共管内的连续水柱</h3>
      <p>C端重新对准废液。吹扫结束后观察公共管；仍有连续水柱时可再吹一次。公共管已处理好时可直接跳过，本次不转泵。跳过按操作者确认的状态继续，不表示程序已经吹扫或洗净。残留液膜尚未测定。</p>
      <p id="benchPurgePlan" class="mini timing"></p>
      <div class="actions"><button id="benchPurgeBtn">16口空气吹扫</button><button id="benchAirSeenBtn">已无可见连续水柱，C端无悬滴</button><button id="benchSkipPurgeBtn">跳过第3步：公共管已无连续水柱</button></div>
    </div>
    <div class="formblock">
      <h3>4 · 向样品瓶试送一次，先确认水确实到瓶</h3>
      <p>把所选样品支管放进清洁收集瓶，瓶盖通气。C端保持清洁、悬空，避开废液。观察液段经过A段后追送入瓶；没有到瓶时，先调整追送步数再试一次。</p>
      <div class="row three">
        <label class="field" for="benchSample">收集样品口<select id="benchSample">${[1,2,3,4,5,6].map(n=>`<option value="${n}">${n}口 · 样品${n}</option>`).join('')}</select></label>
        <label class="field" for="benchAspirateSteps">每剂吸取步数<input id="benchAspirateSteps" type="number" min="1" max="100" step="1" value="45"></label>
        <label class="field" for="benchChaseSteps">每剂追送步数<input id="benchChaseSteps" type="number" min="1" max="3000" step="1" value="300"></label>
      </div>
      <div class="row"><label class="field" for="benchAspirateRpm">吸取转速 / rpm<input id="benchAspirateRpm" type="number" min="0.1" max="100" step="0.1" value="5"></label><label class="field" for="benchChaseRpm">追送转速 / rpm<input id="benchChaseRpm" type="number" min="0.1" max="100" step="0.1" value="10"></label></div><p id="benchDosePlan" class="mini timing"></p>
      <div class="actions"><button id="benchTestBtn">只试送1剂清水</button><button id="benchDoseSeenBtn">已看到本次清水进入所选瓶</button></div>
      <p class="subtle">追送上限3000步，吸取与追送转速均可调0.1—100 rpm，默认5／15 rpm。提高转速不保证一定送达；观察液段到瓶并避免连续鼓泡、飞溅。高转速下步数与实际转数的关系尚未实测，时间仅作估计。每次“只试送1剂”会重新吸水、追送及吹扫，不是补吹上一剂。目标是送达液段，不以吹干管壁水膜为终点。本次仅核对路线和追送是否足够；0.01 g天平的单次0.15 mL读数不用于证明单剂精度。更换样品口、追送步数或转速后需重新试送观察。</p>
    </div>
    <div class="formblock">
      <h3>5 · 已保存的入瓶量参考与可选校准</h3><p id="benchSavedDose" class="note statuschecks"></p><details id="doseRecalibration"><summary>可选：重新校准入瓶量（每组5剂，共3组）</summary>
      <p>倒掉刚才试送的水，空瓶去皮，再把同一支管放回相同位置。每次点击收集一组5剂，目标总量0.75 mL（水按1 g/mL约0.75 g）；停泵后称净质量。倒空、去皮后重复，共3组。</p>
      <p id="benchGroupPlan" class="mini timing"></p>
      <div class="actions"><button id="benchAfterDoseAirSeenBtn">上次试送后已无连续水柱，C端无悬滴</button><button id="benchGroupBtn" class="primary">瓶已倒空并去皮，连续试送5剂</button></div>
      <p class="subtle">程序在每剂后用16口空气排出公共管内主体水量，包含最后一剂后；这组纯水试验不在剂间额外用15口水冲洗。样品支管继续沿用，倒空瓶子不会使支管重新成为“首次空管”。</p>
      <div class="row three">
        <label class="field" for="benchBatch1">第1组（5剂）净质量 / g<input id="benchBatch1" type="number" min="0" step="0.01" placeholder="实测值"></label>
        <label class="field" for="benchBatch2">第2组（5剂）净质量 / g<input id="benchBatch2" type="number" min="0" step="0.01" placeholder="实测值"></label>
        <label class="field" for="benchBatch3">第3组（5剂）净质量 / g<input id="benchBatch3" type="number" min="0" step="0.01" placeholder="实测值"></label>
      </div>
      <div class="actions"><button id="benchBatchReportBtn">计算并保存组平均交付量</button><button id="benchApplyBtn" disabled>采用建议吸取步数，准备下一轮</button><button id="benchClearReadingsBtn" class="hidden">清空旧组读数，开始新参数的一轮</button></div>
      <p id="benchBatchReport" class="note statuschecks" aria-live="polite">均值＝每组净质量÷密度÷5剂。结果用于调整平均量；组间RSD不能替代单剂重复性，也不证明残水或交叉污染合格。</p>
      <p id="benchHistory" class="subtle"></p></details>
    </div>`;

  let savedDoseLoaded = false;
  let recommendation = null;
  let observedContext = null;
  let batchContext = null;
  const value = (id, name, integer=false) => finite($(id).value, name, 0.0001, integer);
  const chosen = () => {
    const c={sample:value('benchSample','样品口',true),aspirate_steps:value('benchAspirateSteps','吸取步数',true),chase_steps:value('benchChaseSteps','追送步数',true),aspirate_rpm:value('benchAspirateRpm','吸取转速'),chase_rpm:value('benchChaseRpm','追送转速')};
    if(c.sample>6||c.aspirate_steps>100||c.chase_steps>3000)throw Error('清水液段试验范围：样品1—6口、吸取1—100步、追送1—3000步');
    if(c.aspirate_rpm<.1||c.aspirate_rpm>100||c.chase_rpm<.1||c.chase_rpm>100)throw Error("转速范围0.1—100 rpm");
    if(Math.max(20,Math.ceil(c.aspirate_steps*.36/c.aspirate_rpm+10),35,Math.ceil(c.chase_steps*.36/c.chase_rpm+10))>120)throw Error("预计动作超出120秒，请减少步数或提高转速");
    return c;
  };
  const fingerprint = () => JSON.stringify([$('benchSample').value,$('benchAspirateSteps').value,$('benchChaseSteps').value,$('benchAspirateRpm').value,$('benchChaseRpm').value,$('benchDensity').value]);
  const motionFingerprint = () => JSON.stringify([$('benchSample').value,$('benchAspirateSteps').value,$('benchChaseSteps').value,$('benchAspirateRpm').value,$('benchChaseRpm').value]);
  const short = (n, digits=2) => n!=null&&Number.isFinite(Number(n)) ? String(Number(Number(n).toFixed(digits))) : '待测';
  const plan = (key, fallback) => state.motion_plan?.recipes?.[key] || fallback;
  const motionText = (p) => `${p.turns}转 · ${p.rpm} rpm · 预计转动${short(60*p.turns/p.rpm,1)} s；动作超时${p.timeout_s} s`;
  const stepsEstimate = () => Number(state.motion_plan?.steps_per_turn_estimate) || 200;
  const weights = (prefix) => [1,2,3].map(n => finite($(prefix+n).value,`第${n}次净质量`,0));

  function reportText(r, method) {
    if (method === 'bulk_turns') return `清水每转出水量：${short(r.mean_ml_per_turn,4)} mL/转。\n已保存3次5转称重记录。已更新默认参考，下次自动沿用。此系数仅适用于C端充水收集；空气液段仍需按下方5剂一组实测。`;
    return `平均每剂交付：${short(r.mean_per_dose_ml,4)} mL（5剂一组）。${r.deviation_percent==null?'':'目标'+short(r.target_ml??.15,3)+' mL的均值偏差：'+short(r.deviation_percent,2)+'%。'}\n组间RSD：${r.group_rsd_percent == null ? '尚不能计算' : short(r.group_rsd_percent,2)+'%'}。${r.suggested_aspirate_steps ? '\n下一轮建议吸取：'+r.suggested_aspirate_steps+'步；追送参数保持本轮设置，重新测3组。' : ''}${r.recommendation?'\n'+r.recommendation:''}\n单剂重复性：未验证。母液等效量、残水和交叉污染：未验证。`;
  }

  async function action(operation, extra={}) {
    const context = ['dose_water','dose_seen'].includes(operation) ? chosen() : {};
    await postAction('bench',{operation,...context,...extra});
    if(operation==='dose_seen') observedContext={sample:context.sample,chase_steps:context.chase_steps,aspirate_rpm:context.aspirate_rpm,chase_rpm:context.chase_rpm};
  }
  for (const [id,op] of [['benchSkipFillBtn','skip_fill'],['benchSkipPurgeBtn','skip_purge'],['benchFillBtn','fill_water'],['benchWaterSeenBtn','water_seen'],['benchCollectBtn','collect_water'],['benchPurgeBtn','purge'],['benchAirSeenBtn','air_seen'],['benchAfterDoseAirSeenBtn','air_seen'],['benchDoseSeenBtn','dose_seen']]) {
    $(id).onclick=()=>act(()=>action(op));
  }
  $('benchTestBtn').onclick=()=>act(()=>action('dose_water',{count:1}));
  $('benchGroupBtn').onclick=()=>act(()=>action('dose_water',{count:5}));

  async function saveReport(method) {
    if(method==='batch_water'&&batchContext&&batchContext!==motionFingerprint())throw Error('当前参数与已填的组读数不一致；恢复本轮参数，或清空旧读数重新测量');
    const context=method==='batch_water'?chosen():{sample:1,aspirate_steps:45,chase_steps:300};
    const body={method,readings_g:weights(method==='bulk_turns'?'benchBulk':'benchBatch'),density_g_ml:value('benchDensity','清水密度'),balance_resolution_g:.01,turns:5,doses_per_group:5,...context,note:$('benchNote').value.trim()+(method==='batch_water'?'；清水5剂一组，组前倒空并去皮，样品支管沿用，为使用后支管。':'；15口清水经公共管从C端收集，每次5转。')};
    const response=await api('/api/bench/report',body),r=response.report;
    if(!r)throw Error('服务未返回称重报告，请保留输入读数');
    $(method==='bulk_turns'?'benchBulkReport':'benchBatchReport').textContent=reportText(r,method);
    if(method==='batch_water') recommendation=Number.isInteger(r.suggested_aspirate_steps)&&r.suggested_aspirate_steps>0 ? {steps:r.suggested_aspirate_steps,fingerprint:fingerprint()} : null;
    toast('称重原始数据与计算结果已保存；设备连接保持不变');
  }
  $('benchBulkReportBtn').onclick=()=>act(()=>saveReport('bulk_turns'));
  $('benchBatchReportBtn').onclick=()=>act(()=>saveReport('batch_water'));
  $('benchApplyBtn').onclick=()=>{
    if(!recommendation||recommendation.fingerprint!==fingerprint())return;
    $('benchAspirateSteps').value=recommendation.steps;
    for(const n of [1,2,3])$('benchBatch'+n).value='';
    batchContext=null;
    recommendation=null;
    $('benchBatchReport').textContent='已采用建议吸取步数，尚未验证新参数。上一轮原始数据已归档；倒空并去皮，用新参数再收集3组。此按钮不会开泵。';
    window.renderBench();
  };
  for (const id of ['benchSample','benchAspirateSteps','benchChaseSteps','benchAspirateRpm','benchChaseRpm','benchDensity']) $(id).addEventListener('input',()=>{
    recommendation=null;
    if(id!=='benchDensity'&&batchContext&&batchContext!==motionFingerprint()) {
      $('benchBatchReport').textContent='样品口或动作参数已改变，已填读数仍保留，但不能用于新参数。可恢复本轮参数后保存，或点击“清空旧组读数”再收集3组。';
    }
    window.renderBench();
  });
  for(const n of [1,2,3])$('benchBatch'+n).addEventListener('input',()=>{
    const any=[1,2,3].some(i=>$('benchBatch'+i).value!=='');
    if(!any)batchContext=null;
    else if(!batchContext)batchContext=motionFingerprint();
    recommendation=null;
    window.renderBench();
  });
  $('benchClearReadingsBtn').onclick=()=>{
    for(const n of [1,2,3])$('benchBatch'+n).value='';
    batchContext=null;recommendation=null;
    $('benchBatchReport').textContent='已清空表单旧组读数；本机已归档报告保留。按当前参数重新收集3组，每组5剂。';
    window.renderBench();
  };

  window.renderBench=()=>{
    const b=state.bench||{},busy=requestBusy||state.busy||!!state.prompt;
    const savedDose=state.water_dose_reference;
    if(savedDose){
      const p=savedDose.parameters;
      $('benchSavedDose').textContent=savedDose.method==='operator_reported_single_volume'?`当前采用用户实测参考：只试送1剂清水约${short(savedDose.mean_per_dose_ml,3)} mL；5剂目标0.75 mL。\n动作设置：${p.sample}号瓶；吸取${p.aspirate_steps}步／${p.aspirate_rpm} rpm，追送${p.chase_steps}步／${p.chase_rpm} rpm。\n此前5剂1.02 g保留为历史，本次未提供新的分组称重，尚未完成3组验证。可选校准仍可更新该参考。`:`已保存：${savedDose.readings_g.join('、')} g，每组${savedDose.doses_per_group}剂，共${savedDose.groups}组。平均每剂约${short(savedDose.mean_per_dose_ml,4)} mL，偏差${short(savedDose.deviation_percent,2)}%。\n${p?`记录参数：${p.sample}号瓶；吸取${p.aspirate_steps}步／${p.aspirate_rpm??'未记录'} rpm，追送${p.chase_steps??'未记录'}步／${p.chase_rpm??'未记录'} rpm。`:'动作参数尚待确认，未据此改动默认值。'}\n${savedDose.groups<3?'只有1组数据，尚未完成3组验证。':'已记录多组数据，仍不代表每剂都准确。'}清水参考不等于母液定量验证。需要重测时展开下方校准。`;
      if(!savedDoseLoaded){
        if(p&&!state.busy&&!requestBusy)for(const [id,k] of [['benchSample','sample'],['benchAspirateSteps','aspirate_steps'],['benchChaseSteps','chase_steps'],['benchAspirateRpm','aspirate_rpm'],['benchChaseRpm','chase_rpm']])if(p[k]!=null)$(id).value=p[k];
        savedDoseLoaded=true;
      }
    }
    const bulk=state.bulk_water_reference?.report;
    $('benchBulkReference').textContent=bulk?`已保存净质量：${bulk.readings_g.join('、')} g；每次${bulk.turns}圈。\n平均每圈约 ${short(bulk.mean_ml_per_turn,6)} mL（水密度按${short(bulk.density_g_ml,4)} g/mL）。不用重复称重，可直接做第3步吹扫。仅为C端充水参考，不代表样品瓶端0.15 mL交付已校准。`:'正在读取已保存的充水参考……';
    const reason=busy?'当前动作尚未结束':!state.connected?'先连接设备':state.pending_plug?'先处理正式流程的待送液段':'';
    const current={sample:Number($('benchSample').value),chase_steps:Number($('benchChaseSteps').value),aspirate_rpm:Number($('benchAspirateRpm').value),chase_rpm:Number($('benchChaseRpm').value)};
    const serverContext=b.delivery_seen_context||(b.delivery_seen_sample!=null?{sample:b.delivery_seen_sample,chase_steps:b.delivery_seen_chase_steps}:null);
    const seen=serverContext||observedContext;
    const routeMatches=(x)=>!!x&&Number(x.sample)===current.sample&&Number(x.chase_steps)===current.chase_steps&&Number(x.aspirate_rpm??5)===current.aspirate_rpm&&Number(x.chase_rpm??10)===current.chase_rpm;
    const matches=!!b.delivery_seen&&routeMatches(seen);
    const fill=plan('first_flush',{turns:20,rpm:50,timeout_s:40});
    const purge=plan('purge',{turns:15,rpm:60,timeout_s:30});
    $('benchFillPlan').textContent=motionText(fill);
    $('benchCollectPlan').textContent=motionText({turns:5,rpm:50,timeout_s:20});
    $('benchPurgePlan').textContent=motionText(purge);
    const asp=Number($('benchAspirateSteps').value),chase=Number($('benchChaseSteps').value),spr=stepsEstimate(),aspRpm=Number($('benchAspirateRpm').value),chaseRpm=Number($('benchChaseRpm').value);
    const doseSeconds=60*(asp/spr/aspRpm+chase/spr/chaseRpm);
    $('benchDosePlan').textContent=`吸取：${asp}步，约${short(asp/spr,3)}转，${aspRpm} rpm，约${short(60*asp/spr/aspRpm,1)} s。追送：${chase}步，约${short(chase/spr,3)}转，${chaseRpm} rpm，约${short(60*chase/spr/chaseRpm,1)} s，追送超时${Math.max(35,Math.ceil(chase*.36/chaseRpm+10))} s。随后自动16口吹扫${purge.turns}转、${purge.rpm} rpm、约${short(60*purge.turns/purge.rpm,1)} s。步数转数按约${spr}步/转估算，另需切阀和通信时间。`;
    $('benchGroupPlan').textContent=`一组5剂：预计转动合计约${short(5*(doseSeconds+60*purge.turns/purge.rpm),0)} s，另需切阀和通信时间。每剂吸取${asp}步、追送${chase}步；每次运行自动停止。`;
    for(const id of ['benchFillBtn','benchPurgeBtn','benchSkipFillBtn','benchSkipPurgeBtn'])gate(id,reason);
    const last=b.last_run||{};
    const op=last.completed===true?last.operation:null;
    gate('benchWaterSeenBtn',reason||(!['fill_water','collect_water','water_seen'].includes(op)?'先运行15口清水灌管':''));
    gate('benchCollectBtn',reason||(!b.water_ready?'先灌管，并确认C端连续出水':state.common!=='wet'?'当前公共管不是充水状态，请重新灌管':''));
    const airObservationReason=reason||(!['purge','dose_water','air_seen'].includes(op)?'先完成空气吹扫或试送后的自动吹扫':'');
    gate('benchAirSeenBtn',airObservationReason);
    gate('benchAfterDoseAirSeenBtn',airObservationReason);
    const readyReason=!b.water_ready?'先完成15口引水并观察C端连续出水':!b.air_ready||state.common!=='air_unverified'?'先吹扫并确认无连续水柱':'';
    gate('benchTestBtn',reason||readyReason);
    gate('benchDoseSeenBtn',reason||(op!=='dose_water'||last.count!==1?'先试送1剂清水':!routeMatches(last)?'请恢复刚才的样品口、步数和转速，或按新设置再试送1剂':''));
    gate('benchGroupBtn',reason||readyReason||(!matches?'先向本样品试送1剂，并确认水已到瓶':''));
    const staleReadings=batchContext&&batchContext!==motionFingerprint();
    gate('benchBulkReportBtn',busy?'当前动作尚未结束':'');
    gate('benchBatchReportBtn',busy?'当前动作尚未结束':staleReadings?'当前参数与旧组读数不一致，请恢复参数或重新测量':'');
    $('benchClearReadingsBtn').classList.toggle('hidden',!staleReadings);
    gate('benchClearReadingsBtn',busy?'当前动作尚未结束':'');
    gate('benchApplyBtn',busy?'当前动作尚未结束':!recommendation||recommendation.fingerprint!==fingerprint()?'先计算本轮组平均报告':'');
    for(const id of ['benchSample','benchAspirateSteps','benchChaseSteps','benchAspirateRpm','benchChaseRpm'])$(id).disabled=!!busy;
    const count=b.sample_counts?.[current.sample]||0;
    let next='从第1步灌管开始。';
    if(op==='fill_water'&&!b.water_ready)next='第1步：看到C端连续出水后点击“已看到C端连续出水”。';
    else if(b.water_ready&&state.common==='wet')next='每转出水量已保存，直接到第3步吹扫；需要重测时才展开第2步可选校准。';
    else if(op==='dose_water'&&last.count===1&&!matches)next='第4步：核对水已进入本瓶，点击对应按钮；再观察公共管无连续水柱，准备下一组。';
    else if(!b.air_ready&&(op==='purge'||op==='dose_water'))next='观察公共管和C端，点击“已无连续水柱”后再继续。';
    else if(b.air_ready&&matches)next='第5步：瓶子倒空、去皮，收集5剂一组；每组后填写净质量。';
    else if(b.air_ready)next='第4步：先向所选样品口试送1剂，观察清水是否到瓶。';
    $('benchStatus').textContent=reason||`本次清水准备：${b.water_ready?'已观察出水':'待观察出水'}；样品${current.sample}试送累计${count}剂。\n下一步：${next}`;
    $('benchHistory').textContent=b.reports?.length?`本次会话可见${b.reports.length}份称重报告；原始记录由本机保存。保存报告不代表母液定量签核。`:'原始称重数据与参数由本机保存。';
  };
  window.renderBench();
})();
