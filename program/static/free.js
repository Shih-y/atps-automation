'use strict';
(() => {
  const volumeCard=document.createElement('section');volumeCard.id='volumeDebug';volumeCard.className='card';
  volumeCard.innerHTML=`<h2>指定毫升加料 · 一次吸取、一次追送</h2><div class="row three"><label>来源口<select id="volumeSource">${[11,12,13,14,15].map(n=>`<option value="${n}" ${n===15?'selected':''}>${n}口</option>`).join('')}</select></label><label>目标样品瓶<select id="volumeSample">${[1,2,3,4,5,6].map(n=>`<option value="${n}">${n}号瓶</option>`).join('')}</select></label><label>本次加入 / mL<input id="volumeMl" type="number" value="0.15" min="0.001" max="10" step="0.001"></label></div><p id="volumePreview" class="note"></p><button id="volumeSend" class="primary">一次加入指定毫升（不追加混合吹气）</button><p class="subtle">每次只吸取一次、追送一次，再16口向C端废液吹扫一次，不自动分段。空气追送仍可能在入瓶末尾出现气泡；额外混合吹气由你最后手动启动。A段容积约0.80 mL，大液段可能进入泵头，0.15 mL参考按比例放大尚未验证。超过单次120秒的参数会提示调整，不会暗中分段。</p><div class="row three"><label>最后混合：吹气步数<input id="volumeMixSteps" type="number" min="1" max="20000" value="20"></label><label>吹气转速 / rpm<input id="volumeMixRpm" type="number" min="0.1" max="100" step="0.1" value="5"></label><label>吹气超时 / s<input id="volumeMixTimeout" type="number" min="1" max="120" value="10"></label></div><button id="volumeMix">全部加完，向所选瓶吹气混合</button><p id="volumeProgress" class="note" aria-live="polite"></p>`;
  $('tubeWorkflows').before(volumeCard);
  $('volumeSend').onclick=()=>act(()=>postAction('volume',{source:Number($('volumeSource').value),sample:Number($('volumeSample').value),volume_ml:finite($('volumeMl').value,'体积mL',.001)}));
  $('volumeMix').onclick=()=>act(()=>postAction('bubble',{debug_parameters:{source:Number($('volumeSource').value),sample:Number($('volumeSample').value),bubble:{steps:finite($('volumeMixSteps').value,'吹气步数',1,true),rpm:finite($('volumeMixRpm').value,'吹气rpm',.1),timeout_s:finite($('volumeMixTimeout').value,'吹气超时',1)}}}));
  const preview=()=>{
    const r=state.water_dose_reference,p=r?.parameters,ml=Number($('volumeMl').value);
    if(!p||!r.mean_per_dose_ml)return;
    const steps=Math.floor(ml*p.aspirate_steps/r.mean_per_dose_ml+.5),q=steps*r.mean_per_dose_ml/p.aspirate_steps,cs=p.chase_steps+Math.max(0,steps-p.aspirate_steps);
    $('volumePreview').textContent=`当前比例：${p.aspirate_steps}步 ≈ ${r.mean_per_dose_ml} mL；1 mL ≈ ${(p.aspirate_steps/r.mean_per_dose_ml).toFixed(3)}步。\n一次吸取${steps}步／${p.aspirate_rpm} rpm，一次追送${cs}步／${p.chase_rpm} rpm；整数步数对应估算${q.toFixed(4)} mL。追送按原参考步数加上新增吸取步数估算，仍需实测是否全部送达。最后16口向废液吹扫，不向样品追加混合吹气。`;
  };
  $('volumeMl').oninput=preview;
  const manual=document.createElement('section');manual.className='card';manual.id='manualConsole';
  manual.innerHTML=`<h2>泵阀状态与手动控制</h2><p id="manualReadback" class="note statuschecks" aria-live="polite">等待设备读回</p><div class="row"><label>操作阀口<select id="manualPort">${[1,2,3,4,5,6,11,12,13,14,15,16].map(n=>`<option value="${n}" ${n===16?'selected':''}>${n}口 · ${n<=6?'样品'+n:n===16?'空气':n===15?'清水':'来源'}</option>`).join('')}</select></label><label>泵转向<select id="manualRotation"><option value="ccw">CCW反转 · 所选口→C端</option><option value="cw">CW正转 · C端→所选口</option></select></label></div><div class="row"><label>转速 / rpm<input id="manualRpm" type="number" min="0.1" max="100" step="0.1" value="5"></label><label>本次圈数（整数）<input id="manualTurns" type="number" min="1" max="100" step="1" value="1"></label></div><div class="actions"><button id="manualSelect">只切换阀口</button><button id="manualRead">刷新泵阀状态</button><button id="manualRun" class="primary">按所选转向、转速、圈数运行</button><button id="manualStop" class="stop">立即停止</button></div><p id="manualEstimate" class="mini"></p><p class="subtle">修改下拉框不会立即动作。运行时先停泵、切到所选口并读回，再执行有限整圈。CW/CCW可自由选择；反向可能把液体送回来源瓶或从样品瓶抽入公共管。这里的整圈命令直接使用设备圈数指令。</p>`;
  volumeCard.before(manual);
  $('manualSelect').onclick=()=>act(()=>postAction('select_port',{port:Number($('manualPort').value)}));
  $('manualRead').onclick=()=>act(()=>postAction('read_status'));
  $('manualRun').onclick=()=>act(()=>postAction('manual_motion',{port:Number($('manualPort').value),rotation:$('manualRotation').value,turns:finite($('manualTurns').value,'圈数',1,true),rpm:finite($('manualRpm').value,'rpm',.1)}));
  $('manualStop').onclick=()=>act(()=>api('/api/stop',{}),true);
  const box=document.createElement('div');box.className='note';box.id='freeDebugInfo';
  box.innerHTML='<b>自由调试</b><p>按钮不要求已预充、已吹扫、试送观察或校准档案。下方“快速选择来源／目标样品”决定吸取追送路线；吸取和追送参数沿用上方清水试送输入框。只按有限动作执行，实际体积与管内状态自行观察。</p><div class="row three"><label>独立鼓泡步数<input id="freeBubbleSteps" type="number" value="20" min="1" max="20000"></label><label>鼓泡转速 / rpm<input id="freeBubbleRpm" type="number" value="5" min="0.1" max="100" step="0.1"></label><label>鼓泡超时 / s<input id="freeBubbleTimeout" type="number" value="10" min="1" max="120"></label></div>';
  $('profileSummary').before(box);
  window.freeDebugParameters=()=>{
    const steps=finite($('benchAspirateSteps').value,'吸取步数',1,true),chase=finite($('benchChaseSteps').value,'追送步数',1,true),ar=finite($('benchAspirateRpm').value,'吸取rpm',.1),cr=finite($('benchChaseRpm').value,'追送rpm',.1);
    return {source:Number($('quickSource').value),sample:Number($('quickSample').value),aspirate:{steps,rpm:ar,timeout_s:Math.max(20,Math.ceil(steps*.36/ar+10))},chase:{steps:chase,rpm:cr,timeout_s:Math.max(35,Math.ceil(chase*.36/cr+10))},bubble:{steps:finite($('freeBubbleSteps').value,'鼓泡步数',1,true),rpm:finite($('freeBubbleRpm').value,'鼓泡rpm',.1),timeout_s:finite($('freeBubbleTimeout').value,'鼓泡超时',1)}};
  };
  const oldAdd=$('addQueueBtn').onclick;
  $('addQueueBtn').onclick=()=>{
    if(!state.free_debug)return oldAdd();
    try{const p=window.freeDebugParameters();queue.push({debug_parameters:p,bubble:$('withBubble').checked,label:`调试 ${p.source}→${p.sample}，吸${p.aspirate.steps}步／${p.aspirate.rpm}rpm，送${p.chase.steps}步／${p.chase.rpm}rpm`});drawQueue();}catch(e){error(e);}
  };
  window.renderFreeDebug=()=>{
    box.classList.toggle('hidden',!state.free_debug);volumeCard.classList.toggle('hidden',!state.free_debug);manual.classList.toggle('hidden',!state.free_debug);if(!state.free_debug)return;
    const busy=requestBusy||state.busy||!!state.prompt;
    for(const e of volumeCard.querySelectorAll('input,select'))e.disabled=!!busy;
    gate('volumeSend',busy?'当前动作尚未结束':!state.connected?'请先连接':'');preview();
    gate('volumeMix',busy?'当前动作尚未结束':!state.connected?'请先连接':'');
    for(const e of manual.querySelectorAll('input,select'))e.disabled=!!busy;
    for(const id of ['manualSelect','manualRun','manualRead'])gate(id,busy?'当前动作尚未结束':!state.connected?'请先连接':'');
    $('manualReadback').textContent=$('liveValve').textContent+'\n'+$('livePump').textContent+'\n'+$('liveFlow').textContent+'\n'+$('liveDetail').textContent;
    const sec=60*Number($('manualTurns').value)/Number($('manualRpm').value);
    $('manualEstimate').textContent=`预计纯转动${Number.isFinite(sec)?sec.toFixed(1):'?'}秒，另需切阀和通信；本次超时${Math.max(15,Math.ceil(sec*1.25+10))}秒（最长120秒）。`;
    const run=state.volume_run;
    $('volumeProgress').textContent=run?`${run.source}口 → ${run.sample}号瓶：请求${run.requested_ml} mL，${run.completed?'一次加料动作已完成':'正在执行一次加料'}；未自动追加混合吹气，实际体积待测。`:'选择来源、瓶号和体积后点击按钮；所有加料结束后再点吹气混合。';
    for(const e of box.querySelectorAll('input'))e.disabled=!!busy;
    $('profileSummary').textContent='自由调试直接采用当前输入参数；校准档案不作为动作前提。';
    $('quickStatus').textContent='可直接选择来源和样品，不需先建立档案。';
    $('pendingPlug').textContent='自由调试不锁定液段与目标瓶；吸取、追送、切阀可独立执行。';
    $('chaseBtn').textContent='② 只追送到当前所选样品';
    $('runBtn').textContent='开始调试队列';$('addQueueBtn').textContent='把当前参数加入队列';
    $('primeBtn').textContent='只执行一次来源引液脉冲';
    $('discardPlugBtn').textContent='排向废液并洗→吹';
    $('doseReason').textContent='单次组合：吸取→追送；勾选可追加鼓泡。队列每项后洗→吹。';
    $('prepareCaptureBtn').textContent='直接建立采集任务';
    $('stepReason').textContent='自由调试：不检查前序状态。按钮执行所写有限动作，校准和液路观察由你决定。';
    $('benchStatus').textContent='自由调试：可直接点单剂、五剂、收集5圈或吹扫，无需完成前序步骤；观察按钮仅用于自愿记录。';
    $('benchFillBtn').textContent='15口单独预充6圈（不含吹扫）';
    $('benchFillPlan').textContent='6转 · 50 rpm · 预计转动7.2 s；超时20 s。不要求C端连续出水。';
    if(!busy)$('nextStep').textContent='自由调试已启用。选好端口和有限参数后直接操作；来源预充6圈后自动16口吹扫，不弹检查。';
  };
})();
