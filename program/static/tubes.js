'use strict';
(() => {
  const anchor=$('benchWizard');
  if(!anchor)return;
  const card=document.createElement('section');card.className='card';card.id='tubeWorkflows';
  card.innerHTML=`<h2>来源管预充与结束排液</h2>
  <p>勾选本次实际使用的管路。默认13、14、15来源口和1号样品口；其他管路不会自动处理。</p>
  <h3>① 选择来源硬管</h3><div class="actions">${[11,12,13,14,15].map(p=>`<label class="check"><input type="checkbox" data-tube-source="${p}" ${p>=13?'checked':''}>${p}口</label>`).join('')}</div>
  <p>将勾选的来源管端浸入对应液体，C端悬空对准废液。样品支管不要预充水。逐口6圈／50 rpm（约7.2秒），各口动作结束后直接进入下一口；最后自动做16口吹扫15圈／60 rpm。自由调试不弹检查，不自动认定已充满。</p>
  <div class="actions"><button id="tubeRefill" class="primary">重新充满所选来源管</button><button id="tubePurge">只吹扫公共管（16口）</button></div>
  <p class="subtle">只勾一个口即可单独重做。6圈是否足够请自行观察，动作完成不代表管内已充满。预充会将该来源液体排到废液；真实母液切换还需验证清洗。</p>
  <h3>② 本次清水组合试验</h3>
  <p>15口0.5 mL → 14口1.0 mL → 13口0.3 mL，全部进入1号瓶。按已保存的清水参考分成小液段，每段吸取不超过45步；追送与16口吹扫逐段完成。目标合计1.8 mL，实际回收量须称重。</p>
  <label class="check"><input type="checkbox" id="tubeWaterOnly">可选备注：13、14、15为清水；该勾选不作为自由调试运行前提。</label>
  <button id="tubeRecipe" class="primary">开始本次清水组合试验</button>
  <p class="subtle">当前默认参考：45步／5 rpm吸取、1000步／15 rpm追送，用户报告单剂0.15 mL。组合计算读取最新保存的参考；换来源口与小剂量余数尚未验证；可选校准仍保留在下方。</p>
  <h3>③ 结束排液，先保留好瓶中的样品</h3>
  <div class="actions">${[1,2,3,4,5,6].map(p=>`<label class="check"><input type="checkbox" data-tube-sample="${p}" ${p===1?'checked':''}>样品${p}管</label>`).join('')}</div>
  <p><b>来源管：</b>点击排液前，把勾选的吸液端提出液面；点击后直接逐口吸空气排向C端废液。<b>样品管：</b>点击前把末端从样品瓶取出，悬空朝向废液；点击后直接逐口送气；不会从样品瓶吸液。</p>
  <div class="actions"><button id="tubeFinish" class="primary">组合收尾：来源管＋所选样品管排液</button><button id="tubeDrainSources">只排所选来源管</button><button id="tubeDrainSamples">只排所选样品管</button></div>
  <p class="note">来源排液15圈／60 rpm；样品管送气1000步／15 rpm。自由调试不弹出搬管或检查提示；请在点击前准备好全部管端，自行观察水柱和悬滴。这里的“排液”指排去可见主体液体，不保证无水膜，也不代替化学清洗。下次使用须把管端放回正确位置并重新预充来源管。</p>
  <p id="tubeStatus" class="note statuschecks" aria-live="polite"></p>`;
  anchor.before(card);
  const selected=(attr)=>Array.from(card.querySelectorAll(`[${attr}]:checked`)).map(e=>Number(e.getAttribute(attr)));
  const actions={tubeRefill:'refill',tubePurge:'purge',tubeDrainSources:'drain_sources',tubeDrainSamples:'drain_samples',tubeFinish:'finish',tubeRecipe:'water_recipe'};
  for(const [id,operation] of Object.entries(actions))$(id).onclick=()=>act(()=>postAction('tubes',{operation,sources:selected('data-tube-source'),samples:selected('data-tube-sample'),water_only:$('tubeWaterOnly').checked}));
  window.renderTubes=()=>{
    const t=state.tubes||{},busy=requestBusy||state.busy||!!state.prompt;
    const reason=busy?'当前步骤尚未结束':!state.connected?'先连接设备':state.pending_plug?'先处理待追送液段':'';
    for(const id of Object.keys(actions))gate(id,reason);
    if(!reason){
      for(const id of ['tubeRefill','tubeDrainSources','tubeFinish'])gate(id,!selected('data-tube-source').length?'至少选择一个来源口':'');
      for(const id of ['tubeDrainSamples','tubeFinish'])if(!selected('data-tube-sample').length)gate(id,'至少选择一个样品口');
      gate('tubeRecipe',!$('tubeWaterOnly').checked?'先确认全部为清水':![13,14,15].every(p=>(t.primed_ports||[]).includes(p))||!t.common_air_seen?'先完成13、14、15预充及吹扫观察':'');
    }
    for(const e of card.querySelectorAll('input'))e.disabled=!!busy;
    const list=v=>v?.length?v.join('、'):'无';
    const r=t.last_run;
    $('tubeStatus').textContent=`本次连接已观察充满的来源口：${list(t.primed_ports)}；公共管无连续水柱：${t.common_air_seen?'已观察':'待观察'}。\n已记录排液观察（非动作就绪条件）：来源${list(t.source_drained)}；样品管${list(t.sample_drained)}。未选管路未知，完全干燥未验证。${r?'\n最近流程：'+({refill:'来源预充',purge:'公共管吹扫',drain_sources:'来源排液',drain_samples:'样品管排液',finish:'组合收尾',water_recipe:'清水组合试验'}[r.operation]||r.operation)+(r.completed?'已完成':'进行中／待观察')+(r.completed_plugs!=null?'，已追送'+r.completed_plugs+'个液段':''):''}`;
  };
  for(const e of card.querySelectorAll('input'))e.onchange=window.renderTubes;
  window.renderTubes();
})();
