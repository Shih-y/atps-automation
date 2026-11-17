'use strict';
// One server-owned batch per explicit click; polling only displays state.
window.ATPSManualBatch = ({api,action,bind,notice,getState}) => {
  const $=id=>document.getElementById(id),sources=[10,11,12,13,14,15],bottles=[1,2,3,4,5];
  let pending=null,ambiguous=false,submitting=false,previewing=false;
  const fallback={1:1,2:2,3:3,4:4,5:5,6:6};
  const mapping=state=>state.config?.sample_port_map||fallback;
  const label=(sample,map)=>`${sample}号瓶（阀${map[sample]??'未知'}口）`;
  for(const sample of bottles){
    const row=document.createElement('tr'),cell=document.createElement('td'),select=document.createElement('select');
    select.setAttribute('aria-label',`第${sample}行目标瓶`);
    for(const bottle of bottles){const option=document.createElement('option');option.value=bottle;option.textContent=label(bottle,fallback);select.append(option);}select.value=sample;cell.append(select);row.append(cell);
    for(const source of sources){const td=document.createElement('td'),input=document.createElement('input');Object.assign(input,{type:'number',min:0,max:10,step:'.001',value:0});input.dataset.source=source;input.setAttribute('aria-label',`第${sample}行 ${source}口体积mL`);td.append(input);row.append(td);}
    $('manualBatchRows').append(row);
  }
  function number(element,name,min,max,integer=false){const raw=element.value.trim(),value=Number(raw);if(!raw||!Number.isFinite(value)||value<min||value>max||(integer&&!Number.isInteger(value)))throw Error(`${name}须为${min}—${max}之间的${integer?'整数':'数值'}`);return value;}
  function payload(){
    const recipes=[],seen=new Set();
    for(const row of $('manualBatchRows').rows){const sample=Number(row.querySelector('select').value),items=[...row.querySelectorAll('input')].map(input=>({source:Number(input.dataset.source),volume_ml:number(input,`${sample}号瓶 ${input.dataset.source}口体积`,0,10)})).filter(item=>item.volume_ml>0);if(!items.length)continue;
      if(seen.has(sample))throw Error('同一批不能重复选择同一瓶，请合并该瓶毫升数');seen.add(sample);
      if(items.reduce((sum,item)=>sum+item.volume_ml,0)>10.000000001)throw Error(`${sample}号瓶本次加入体积不能超过10 mL，请扣除瓶内已有液体`);recipes.push({sample,items});
    }
    if(!recipes.length)throw Error('请至少填写一瓶大于0的体积');
    return {recipes,mixing:{steps:number($('mixSteps'),'混合步数',1,20000,true),rpm:number($('mixRpm'),'混合转速',.1,100),timeout_s:number($('mixTimeout'),'混合超时',1,120)}};
  }
  const fingerprint=()=>JSON.stringify([...$('manualBatchRows').querySelectorAll('input,select'),...['mixSteps','mixRpm','mixTimeout'].map($)].map(input=>input.value));
  const status=r=>r.completed?'动作完成':r.cancelled?'已中止':r.error?'动作中断':'等待执行';
  function describe(plan,preview=false){const map=plan.sample_port_map||mapping(getState());return (plan.recipes||[]).map((r,index)=>{
    const active=!preview&&!plan.completed&&!plan.cancelled&&!plan.error&&plan.current_index===index;
    const recipeStatus=preview?'预览':active?'执行中':(plan.cancelled||plan.error)&&!r.completed&&!r.error?'未执行（本批已中断）':status(r);
    return `${label(r.sample,r.sample_port_map||map)} · ${recipeStatus}；请求 ${r.requested_ml} mL，参考估算 ${r.estimated_ml==null?'未知':Number(Number(r.estimated_ml).toFixed(4))} mL，实际体积待测。\n${preview?(r.items||[]).map(item=>`${item.source}口请求 ${item.requested_ml} mL / 估算 ${item.estimated_ml} mL；吸取 ${item.total_steps} 步；${item.uncertainty_note||'比例试用，实际体积待测'}`).join('；'):`指令 ${r.completed_commands??0}/${r.total_commands??'?'}；混合${r.mixing_performed?'已完成':'未完成'}；清水朝废液清洗${r.rinse_performed?'动作完成，公共管仍湿':'未完成'}`}。${r.error||''}`;
  }).join('\n');}
  function render(state){
    const map=mapping(state);for(const select of $('manualBatchRows').querySelectorAll('select'))for(const option of select.options)option.textContent=label(option.value,map);
    for(const input of $('manualBatchRows').querySelectorAll('input,select'))input.disabled=!!pending;
    for(const id of ['mixSteps','mixRpm','mixTimeout'])$(id).disabled=!!pending;
    $('manualBatchStart').disabled=submitting||!!state.busy;
    $('manualBatchStart').textContent=pending?'重试原批请求（不会重复加料）':'开始多瓶配方';
    $('manualBatchPreview').disabled=previewing;
    const r=state.manual_batch_run;if(r)$('manualBatchProgress').textContent=`${r.cancelled?'已中止':r.completed?'动作完成':r.error?'动作中断':'执行中'} · 已完成 ${r.completed_bottles??0}/${r.total_bottles??'?'} 瓶；指令 ${r.completed_commands??0}/${r.total_commands??'?'}。\n${describe(r)}${r.error?'\n'+r.error:''}`;
  }
  function edited(){ $('manualBatchPlan').textContent='参数已改变，请重新预览；也可直接开始。'; }
  $('manualBatchRows').addEventListener('input',edited);$('manualBatchRows').addEventListener('change',edited);for(const id of ['mixSteps','mixRpm','mixTimeout'])$(id).addEventListener('input',edited);
  bind('manualBatchPreview',async()=>{const input=payload(),version=fingerprint();previewing=true;render(getState());try{const result=await api('/api/batch-recipe-preview',input);if(version===fingerprint())$('manualBatchPlan').textContent=describe(result.plan,true);return result;}catch(error){if(version===fingerprint())$('manualBatchPlan').textContent='预览失败：'+error.message;throw error;}finally{previewing=false;render(getState());}},'多瓶预览已更新；未启动泵。',false);
  bind('manualBatchStart',async()=>{
    if(submitting)return;
    if(!pending){pending={...payload(),request_id:crypto.randomUUID()};ambiguous=false;}
    submitting=true;render(getState());
    try{const result=await action('batch_recipe',pending);pending=null;ambiguous=false;return result;}
    catch(error){
      // A later rejection cannot disprove acceptance of an earlier lost response.
      if(!ambiguous&&error.status>=400&&error.status<500){pending=null;throw error;}
      ambiguous=true;throw Error('请求结果尚未确认：'+error.message+'。请重试原批请求核对记录；表格与混合参数暂时锁定，按原参数重试不会重复加料。');
    }
    finally{submitting=false;render(getState());}
  },'多瓶请求已接受；请查看各瓶动作进度。');
  return {render};
};
