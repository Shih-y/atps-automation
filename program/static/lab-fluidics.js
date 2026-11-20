'use strict';
window.ATPSLabPages.fluidics = (ctx, page) => {
  const $ = id => page.querySelector('#' + id);
  const make = (tag, text, parent, attrs) => ctx.node(tag, text, parent, attrs);
  const sourceNames = {10:'柠檬酸三钠',11:'硫酸铵',12:'硫酸钠',13:'磷酸氢二钾',14:'PEG4000',15:'清水',16:'空气入口'};
  const actionNames = {manual_motion:'手动有限运行',select_port:'手动切阀',read_status:'刷新读回',recipe:'单瓶配方',batch_recipe:'多瓶配方',experiment_recipe:'自动实验配方',volume:'指定毫升加料',wash:'公共管清洗',purge:'空气吹扫',reset:'清洗与吹扫',tubes:'管路预充 / 排液',bench:'试送与校准',mix:'瓶内混合',load:'吸取液段',chase:'追送液段'};
  const stages = {aspirate:'吸取来源液',chase:'反向追送',mixing:'瓶内混合',rinse:'清水洗向废液',purge:'空气吹扫向废液'};
  const states = {queued:'等待配液',preparing:'配液中',settling:'静置观察',observing:'观察中',observation_complete:'观察完成',completed:'已完成',interrupted:'中断待处理',cancelled:'已取消',assigned:'已分配',occupied:'占用中',free:'空闲',needs_replacement:'待换瓶'};
  const fmt = (value, places=2) => Number.isFinite(Number(value)) && value != null ? Number(value).toFixed(places) : '—';
  const head = make('div', null, page, {className:'fm-heading'});
  make('div', 'FLUIDICS / LIVE', head, {className:'fm-eyebrow'});
  make('h2', '液路控制与实时监视', head);
  make('p', '先看接管，再执行动作。泵阀共用一条液路，五瓶分别观察。', head);
  const layout = make('div', null, page, {className:'fm-layout'});
  const diagramCard = make('section', null, layout, {className:'fm-diagram-card'});
  const caption = make('div', null, diagramCard, {className:'fm-card-heading'});
  make('h3', '液路拓扑', caption);
  make('span', '连通关系示意 · 非摆放尺寸', caption);
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.id='fluidics-diagram';svg.setAttribute('viewBox','0 0 1000 560');svg.setAttribute('role','img');svg.setAttribute('aria-label','十六路阀公共口经暂存软管、单台泵通向废液；来源与五瓶分别连接阀外围端口');diagramCard.append(svg);
  function shape(tag, attrs, parent=svg, text) {
    const n=document.createElementNS(svgNS,tag);
    for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);
    if(text!=null)n.textContent=text;parent.append(n);return n;
  }
  const pipes=shape('g',{class:'fm-pipes'}),overlay=shape('g',{}),nodes=shape('g',{});
  const center={x:490,y:235},radius=112,positions={};
  for(let p=1;p<=16;p++){
    const a=(p-1)*Math.PI/8-Math.PI/2;
    positions[p]={x:center.x+radius*Math.cos(a),y:center.y+radius*Math.sin(a)};
  }
  const pipeNodes={},sourceCards={},bottleSvg={};
  function pipe(key,d){
    const base=shape('path',{d,class:'fm-pipe'},pipes);
    const flow=shape('path',{d,class:'fm-flow'},overlay);
    pipeNodes[key]={base,flow};return pipeNodes[key];
  }
  for(let p=10;p<=16;p++){
    const y=57+(16-p)*48,pt=positions[p];
    pipe('source'+p,`M 246 ${y} C 305 ${y}, 315 ${pt.y}, ${pt.x} ${pt.y}`);
    const g=shape('g',{'data-source-port':p,class:'fm-source'},nodes);
    shape('rect',{x:24,y:y-20,width:222,height:40,rx:7},g);
    shape('text',{x:41,y:y+5,class:'fm-source-number'},g,String(p).padStart(2,'0'));
    shape('text',{x:76,y:y+5},g,sourceNames[p]);sourceCards[p]=g;
  }
  // A single continuous common tube. Crossings have no junction markers.
  const commonPath='M 490 235 L 630 328 Q 650 345 650 390 L 650 433 Q 650 453 670 453 H 690';
  pipe('common',commonPath);
  pipe('outlet','M 754 453 H 912 V 477');
  const dial=shape('g',{},nodes);
  shape('circle',{cx:490,cy:235,r:99,class:'fm-valve-body'},dial);
  shape('path',{d:commonPath,fill:'none',stroke:'white','stroke-width':9},dial);
  dial.append(pipeNodes.common.base,pipeNodes.common.flow);
  const spoke=shape('line',{x1:490,y1:235,x2:490,y2:235,class:'fm-spoke'},dial);
  const targetSpoke=shape('line',{x1:490,y1:235,x2:490,y2:235,class:'fm-target-spoke',visibility:'hidden'},dial);
  const portNodes={};
  for(let p=1;p<=16;p++){
    const pt=positions[p],g=shape('g',{'data-fm-valve-port':p,class:'fm-valve-port'},dial);
    shape('circle',{cx:pt.x,cy:pt.y,r:14},g);shape('text',{x:pt.x,y:pt.y+5,'text-anchor':'middle'},g,p);portNodes[p]=g;
  }
  shape('circle',{cx:490,cy:235,r:24,class:'fm-common'},dial);
  shape('text',{x:490,y:242,'text-anchor':'middle',id:'fm-common-label'},dial,'C');
  shape('text',{x:490,y:35,'text-anchor':'middle',class:'fm-label'},nodes,'SV07M · 16路切换阀');
  shape('text',{x:490,y:60,'text-anchor':'middle',class:'fm-small'},nodes,'每次仅连通一个外围口');
  const dialStatus=shape('text',{x:490,y:385,'text-anchor':'middle',class:'fm-small'},nodes,'等待阀读回');
  shape('text',{x:535,y:462,class:'fm-label'},nodes,'T1 · 公共暂存管');
  shape('text',{x:535,y:482,class:'fm-small'},nodes,'A → B泵头 → C端');
  const pump=shape('g',{id:'fm-pump'},nodes);
  shape('rect',{x:690,y:418,width:64,height:70,rx:12,class:'fm-pump-body'},pump);
  const rotor=shape('g',{id:'fm-rotor'},pump);
  shape('circle',{cx:722,cy:453,r:23,class:'fm-rotor-ring'},rotor);
  for(let i=0;i<3;i++){const a=i*Math.PI*2/3;shape('circle',{cx:722+13*Math.cos(a),cy:453+13*Math.sin(a),r:5},rotor);}
  shape('text',{x:722,y:515,'text-anchor':'middle',class:'fm-label'},nodes,'LM60A / YZ1515');
  const pumpLabel=shape('text',{x:722,y:538,'text-anchor':'middle',class:'fm-small'},nodes,'等待泵读回');
  shape('path',{d:'M 880 487 V 524 Q 880 534 891 534 H 934 Q 946 534 946 524 V 487',class:'fm-waste'},nodes);
  shape('text',{x:913,y:515,'text-anchor':'middle',class:'fm-small'},nodes,'废液杯');
  shape('text',{x:900,y:404,'text-anchor':'middle',class:'fm-small'},nodes,'悬空末端 / 反向吸气');
  shape('text',{x:913,y:554,'text-anchor':'middle',class:'fm-small'},nodes,'不浸液 · 避开飞溅');
  for(let b=1;b<=5;b++){
    const y=65+(b-1)*65,g=shape('g',{'data-fm-bottle':b,class:'fm-bottle'},nodes);
    shape('rect',{x:807,y:y-25,width:165,height:52,rx:7},g);
    shape('path',{d:`M 817 ${y-14} h 17 v 5 h 5 v 25 h -27 v -25 h 5 Z`,class:'fm-bottle-outline'},g);
    shape('text',{x:850,y:y-3},g,`${b}号瓶`);
    const label=shape('text',{x:850,y:y+16,class:'fm-small'},g,'阀口待读取');
    pipe('sample'+b,`M 600 ${y} H 807`);bottleSvg[b]={g,label,y};
  }
  make('p','实线为管路；移动短线仅表示估计流向。无液位传感器，不能由动画确认液段位置、交付量或管路已干燥。',diagramCard,{className:'fm-diagram-note'});
  const side=make('aside',null,layout,{className:'fm-controls'});
  make('h3','当前动作',side);
  const task=make('p','等待状态',side,{id:'fm-task',className:'fm-task'});
  const taskDetail=make('p','',side,{id:'fm-task-detail',className:'fm-subtle'});
  const telemetry=make('dl',null,side,{className:'fm-readouts'});
  function readout(label,id){make('dt',label,telemetry);return make('dd','—',telemetry,{id});}
  readout('阀位置','fm-valve-state');readout('泵状态','fm-pump-state');readout('剩余量','fm-remaining');readout('指令时间','fm-time');
  const progress=make('progress',null,side,{id:'fm-progress',max:1,value:0});
  make('h3','手动控制',side);
  const form=make('div',null,side,{className:'fm-fields'});
  function field(title,id,type,value){const label=make('label',title,form);return make(type==='select'?'select':'input',null,label,{id,...(type==='select'?{}:{type:'number'}),value});}
  const port=field('阀口','fm-port','select','16');
  for(const p of [1,2,3,4,5,6,7,10,11,12,13,14,15,16])make('option',`${p}口 · ${sourceNames[p]||'样品 / 调试'}`,port,{value:p});port.value='16';
  const direction=field('泵转向','fm-direction','select','ccw');
  make('option','CCW 反转',direction,{value:'ccw'});make('option','CW 正转',direction,{value:'cw'});
  const rpm=field('转速 / rpm','fm-rpm','number',60);rpm.min=.1;rpm.max=100;rpm.step=.1;
  const turns=field('圈数 / 整圈','fm-turns','number',30);turns.min=1;turns.max=100;turns.step=1;
  const preview=make('p','',side,{id:'fm-manual-preview',className:'fm-subtle'});
  const buttons=make('div',null,side,{className:'fm-buttons'});
  let hardware={},available=false,inflight=false,selectedBottle=1,lastLog='',lastMapping='';
  const manual=[];
  async function run(action,fields={}){
    if(inflight||hardware.busy||ctx.state.running)throw Error('当前任务占用泵阀，请等待完成或停止当前任务。');
    inflight=true;render();
    try{const response=await ctx.request('/api/action',{action,...fields});hardware=response.state||hardware;ctx.tell('已提交动作；以设备读回为准。');}
    finally{inflight=false;render();}
  }
  manual.push(ctx.button(buttons,'仅切阀',()=>run('select_port',{port:Number(port.value)}),'fm-select'));
  manual.push(ctx.button(buttons,'按参数运行',()=>{
    if(!rpm.checkValidity()||!turns.checkValidity()||!rpm.value||!turns.value)throw Error('请输入0.1—100 rpm及1—100整数圈。');
    return run('manual_motion',{port:Number(port.value),rotation:direction.value,rpm:Number(rpm.value),turns:Number(turns.value)});
  },'fm-run'));
  manual.push(ctx.button(buttons,'刷新读回',()=>run('read_status'),'fm-read'));
  manual.forEach(button=>{const click=button.onclick;button.onclick=async()=>{await click();render();};});
  const stop=ctx.button(side,'立即停止 · 保留连接',()=>ctx.stop(),'fm-stop');stop.className='fm-stop';
  make('p','修改参数不启动。运行时先停泵、选阀，再执行有限圈数。软件停止与设备实体急停分别独立。',side,{className:'fm-subtle'});
  const quick=make('div',null,side,{className:'fm-shortcuts'});
  ctx.button(quick,'单瓶 / 多瓶配方 →',()=>ctx.showPage('equipment'));
  ctx.button(quick,'批次与观察 →',()=>ctx.showPage('monitor'));
  const queue=make('section',null,page,{className:'fm-queue'});
  const qtitle=make('div',null,queue,{className:'fm-card-heading'});make('h3','五瓶任务',qtitle);make('span','点击查看配方 · 泵阀串行 / 观察并行',qtitle);
  const cards=make('div',null,queue,{className:'fm-bottle-cards'}),bottleCards={};
  for(let b=1;b<=5;b++){
    const card=make('button',null,cards,{type:'button',className:'fm-bottle-card'});
    const title=make('strong',`${b}号瓶`,card),state=make('span','等待状态',card),desc=make('small','',card);
    card.onclick=()=>{selectedBottle=b;render();};bottleCards[b]={card,title,state,desc};
    bottleSvg[b].g.addEventListener('click',()=>{selectedBottle=b;render();});
  }
  const bottleDetail=make('div',null,queue,{id:'fm-bottle-detail',className:'fm-bottle-detail'});
  const lower=make('div',null,page,{className:'fm-bottom'});
  const timelineCard=make('section',null,lower,{className:'fm-timeline-card'});make('h3','动作时间线',timelineCard);
  const timeline=make('ol',null,timelineCard,{className:'fm-timeline'});
  const steps=['切来源口','吸取','停泵','切样品口','反向追送','记录交付估计'];
  const marks=steps.map(s=>make('li',s,timeline));
  const timelineNote=make('p','',timelineCard,{className:'fm-subtle'});
  const eventCard=make('section',null,lower,{className:'fm-events'});make('h3','最近事件',eventCard);
  const eventList=make('ol',null,eventCard,{id:'fm-event-list'});
  const timeText=s=>s==null?'—':`${fmt(s,1)} s`;
  const seconds=value=>typeof value==='number'?value:Date.parse(value)/1000;
  function replacedAt(b){return Math.max(0,...(ctx.state.bottle_history||[]).filter(h=>h.bottle===b&&h.confirmed_clean_empty).map(h=>seconds(h.at)).filter(Number.isFinite));}
  function afterReplacement(value,b){const changed=replacedAt(b);return !changed||(Number.isFinite(seconds(value))&&seconds(value)>=changed);}
  function currentRecipe(){return ['recipe','batch_recipe','experiment_recipe','volume'].includes(hardware.active_action?.action)?hardware.recipe_run:null;}
  function recipeForBottle(b){
    const sample=sampleForBottle(b);
    if(sample){
      if(hardware.active_action?.experiment_sample_id===sample.id && hardware.recipe_run?.sample===b)return hardware.recipe_run;
      return sample.progress||sample.recipe?.plan||null;
    }
    if(hardware.recipe_run?.sample===b&&afterReplacement(hardware.recipe_run.started_at,b))return hardware.recipe_run;
    return hardware.manual_batch_run?.recipes?.find(r=>r.sample===b&&afterReplacement(r.started_at,b));
  }
  function sampleForBottle(b){
    const bottle=(ctx.state.bottles||[]).find(x=>Number(x.bottle||x.id)===b);
    return (ctx.state.samples||[]).find(s=>s.id===bottle?.sample_id);
  }
  function renderBottles(mapping){
    for(let b=1;b<=5;b++){
      const {card,title,state,desc}=bottleCards[b],r=recipeForBottle(b),sample=sampleForBottle(b);
      const active=hardware.busy&&Number(currentRecipe()?.sample||hardware.active_action?.sample)===b;
      const status=sample?(states[sample.execution_state||sample.status]||sample.execution_state||sample.status):r?(r.error||r.cancelled?'中断待处理':r.completed?'动作完成':active?'配液中':'等待配液'):'无已记录配方';
      card.dataset.active=String(active);card.setAttribute('aria-pressed',String(selectedBottle===b));title.textContent=`${b}号瓶 · 阀${mapping[b]??'—'}口`;state.textContent=status;
      const gas=hardware.samples?.[String(b)]?.last_gas_stop;
      const lastGas=afterReplacement(gas,b)?gas:null;
      const mixedAt=sample?.mixed_at||lastGas;
      const stamp=typeof mixedAt==='number'?mixedAt:Date.parse(mixedAt)/1000;
      const next=(ctx.state.photo_jobs||[]).filter(j=>j.sample_id===sample?.id&&j.status==='due').sort((a,b)=>a.due_at-b.due_at)[0];
      desc.textContent=Number.isFinite(stamp)?`停气 ${Math.max(0,Math.floor(Date.now()/1000-stamp))} s${next?` · 待拍 ${Math.max(0,Math.ceil(next.due_at-Date.now()/1000))} s`:''}`:r?`动作 ${r.completed_commands||0}/${r.total_commands||'—'} · 交付未实测`:'点击查看本瓶记录';
      bottleSvg[b].g.classList.toggle('selected',selectedBottle===b);
    }
    const b=selectedBottle,r=recipeForBottle(b),sample=sampleForBottle(b);
    bottleDetail.replaceChildren();make('strong',`${b}号瓶 · 阀${mapping[b]??'—'}口`,bottleDetail);
    if(r){
      make('span',(r.items||[]).map(i=>`${i.source}口 ${i.requested_mass_g!=null?fmt(i.requested_mass_g,3)+' g':fmt(i.requested_ml??i.volume_ml,3)+' mL'}`).join(' / ')||'质量配方，详见运行监控',bottleDetail);
      make('span',`指令完成 ${r.completed_commands||0}/${r.total_commands||'—'}；${sample?'按本样品保存的方法执行':r.rinse_after?'末尾清洗及吹扫':'未安排末尾清洗'}；实际交付量待测。`,bottleDetail);
      if(r.error)make('span',r.error,bottleDetail,{className:'fm-error'});
    }else make('span','未记录手动配方；现有液量不能由页面推断为空。',bottleDetail);
    if(sample){
      make('span',`样品 ${sample.name||sample.id} · ${{single:'单相',single_phase:'单相',two_phase:'双相',uncertain:'待确认'}[sample.label?.classification]||'未复核'}`,bottleDetail);
      const c=sample.composition||sample.recipe?.estimated;
      if(c)make('span',`${c.basis==='measured'?'实测':c.basis==='unavailable'?'不可用':'估计'}组成：PEG ${fmt(c.peg_fraction*100)}% / 盐 ${fmt(c.salt_fraction*100)}%（质量分数）`,bottleDetail);
      const why=Array.isArray(sample.exclusions)?sample.exclusions.filter(e=>e.active===true).map(e=>e.reason||e.code).filter(Boolean).join('；'):sample.exclusion;
      if(why)make('span',`待处理原因：${why}`,bottleDetail,{className:'fm-error'});
    }
  }
  function renderLogs(){
    const names={pulse_started:'开始有限动作',pulse_completed:'有限动作完成',port_selected_without_pump_motion:'阀口确认',fault:'动作异常',stop:'停止',stopped:'停止结果',user_stop:'请求停止',stop_confirmed:'已确认停止',stop_unconfirmed:'停止未确认',task_cancelled_by_user_stop:'任务已中断',manual_batch_started:'多瓶配方开始',manual_batch_completed:'多瓶配方完成',action_rejected_before_motion:'参数未通过，未动作',recipe_completed:'整瓶配方完成'};
    names.user_stop_result='停止结果';names.stop_requested_physical_stop_unconfirmed='已请求停止，无法确认';
    const logs=(hardware.logs||[]).filter(e=>names[e.event]).slice(-20).reverse();
    const key=JSON.stringify(logs);if(key===lastLog)return;lastLog=key;eventList.replaceChildren();
    if(!logs.length)make('li','尚无动作记录',eventList);
    for(const e of logs){const li=make('li',null,eventList);make('time',new Date(e.at).toLocaleTimeString('zh-CN',{hour12:false}),li);make('span',`${names[e.event]}${e.stop_status?' · '+({confirmed:'已确认停止',unconfirmed:'无法确认停止',requested:'已请求停止'}[e.stop_status]||'结果未知'):''}${e.action?' · '+(actionNames[e.action]||e.action):''}${e.port!=null?' · 阀'+e.port+'口':''}${e.rotation?' · '+e.rotation.toUpperCase():''}${e.direction?' · '+(e.direction==='aspirate'?'向公共管':'向所选口'):''}${e.turns!=null?' · '+e.turns+'圈':e.steps!=null?' · '+e.steps+'步':''}${e.rpm!=null?' / '+e.rpm+' rpm':''}${e.error?' · '+e.error:''}`,li);}
  }
  function render(){
    const h=hardware.hardware_status||{},command=hardware.active_command;
    const connected=available&&hardware.connected,confirmed=connected&&h.valve_status==='confirmed'?Number(h.valve_port):null;
    const target=connected&&h.valve_status==='switching'?Number(h.target_port)||null:null;
    const fresh=connected&&h.pump_at!=null&&Date.now()/1000-h.pump_at<3;
    const fault=['FAULT','STOPPING'].includes(hardware.state)||['unknown','fault','stop_requested'].includes(h.pump_status);
    const moving=fresh&&!fault&&h.pump_status==='running'&&confirmed!=null&&command?.phase==='moving'&&command.port===confirmed;
    const flow=moving&&['aspirate','dispense'].includes(h.flow)?h.flow:'';
    svg.dataset.flow=flow;rotor.dataset.direction=flow?h.direction:'';rotor.style.setProperty('--rotation-seconds',`${60/(h.rpm||60)}s`);
    const mapping=hardware.config?.sample_port_map||{};
    if(JSON.stringify(mapping)!==lastMapping){
      lastMapping=JSON.stringify(mapping);
      for(let b=1;b<=5;b++){
        const view=bottleSvg[b],p=mapping[b],pt=positions[p];view.g.dataset.port=p??'';view.label.textContent=`阀${p??'—'}口`;
        if(pt){const d=`M ${pt.x} ${pt.y} C 680 ${pt.y}, 715 ${view.y}, 807 ${view.y}`;pipeNodes['sample'+b].base.setAttribute('d',d);pipeNodes['sample'+b].flow.setAttribute('d',d);}
      }
    }
    const selectedBottleForPort=Object.entries(mapping).find(([,p])=>p===confirmed)?.[0];
    const selectedPipe=confirmed>=10?'source'+confirmed:selectedBottleForPort?'sample'+selectedBottleForPort:null;
    for(const [key,{base,flow:line}] of Object.entries(pipeNodes)){
      const selected=confirmed!=null&&(key===selectedPipe||key==='common'||key==='outlet');base.classList.toggle('active',selected);
      const reverse=key.startsWith('sample')?flow==='aspirate':flow==='dispense';
      line.dataset.moving=String(Boolean(flow&&selected));line.dataset.reverse=String(reverse);
      line.classList.toggle('fm-air',confirmed===16||currentRecipe()?.current?.phase==='mixing');
    }
    for(const [p,n] of Object.entries(portNodes)){n.classList.toggle('confirmed',Number(p)===confirmed);n.classList.toggle('target',Number(p)===target);}
    for(const [p,n] of Object.entries(sourceCards))n.classList.toggle('selected',Number(p)===confirmed);
    spoke.setAttribute('visibility',positions[confirmed]?'visible':'hidden');if(positions[confirmed]){spoke.setAttribute('x2',positions[confirmed].x);spoke.setAttribute('y2',positions[confirmed].y);}
    targetSpoke.setAttribute('visibility',positions[target]?'visible':'hidden');if(positions[target]){targetSpoke.setAttribute('x2',positions[target].x);targetSpoke.setAttribute('y2',positions[target].y);}
    const valveText=`确认 ${confirmed??'—'}${confirmed?'口':''} · 目标 ${target??'—'}${target?'口（切换中）':''}`;
    $('fm-valve-state').textContent=valveText;dialStatus.textContent=valveText;
    const pumpText=!connected?'未知 / 未连接':!fresh&&h.pump_status==='running'?'未知 / 读回过期':h.pump_status==='stopped'?'已确认停止':h.pump_status==='running'?`${h.direction?.toUpperCase()||'—'} ${h.direction==='cw'?'正转':'反转'} · ${fmt(h.rpm,1)} rpm`:h.pump_status==='starting'?'启动中 / 待读回':'未知 / 待核对';
    $('fm-pump-state').textContent=pumpText;pumpLabel.textContent=pumpText;
    $('fm-remaining').textContent=fresh&&h.remaining!=null?`${h.remaining} ${h.unit==='turns'?'圈':h.unit==='steps'?'步':''}${h.pump_status==='running'?' · 读回':''}`:'—';
    $('fm-time').textContent=command?`标称 ${timeText(command.nominal_duration_s)}${command.started_at?' / 已过 '+timeText(Math.max(0,Date.now()/1000-command.started_at)):''}`:'无进行中指令';
    progress.value=moving&&h.unit===command.unit?Math.max(0,Math.min(1,1-h.remaining/command.amount)):0;
    const active=hardware.active_action,recipe=currentRecipe(),phase=recipe?.current?.phase;
    task.textContent=!available?'服务不可用':active?(actionNames[active.action]||active.action):hardware.busy?'任务执行中':hardware.state==='FAULT'?'异常待处理':'空闲 · 等待操作';
    const source=recipe?.items?.[recipe.current?.item_index]?.source||active?.source;
    const sample=recipe?.sample||active?.sample;
    taskDetail.textContent=[sample?`${sample}号瓶 / 阀${mapping[sample]??'—'}口`:null,source?`${source}口 ${sourceNames[source]||''}`:null,stages[phase],recipe?.requested_ml!=null?`本瓶目标 ${fmt(recipe.requested_ml,3)} mL（实际待测）`:null,!active?hardware.message:null].filter(Boolean).join(' · ');
    const blocked=!available||!hardware.connected||hardware.busy||ctx.state.running||inflight;
    manual.forEach(b=>b.disabled=Boolean(blocked));[port,direction,rpm,turns].forEach(n=>n.disabled=Boolean(blocked));
    const aspirateDirection=hardware.config?.pump?.aspirate_direction||'ccw';
    preview.textContent=`${direction.value===aspirateDirection?'所选口 → 公共管 → C端':'C端 → 公共管 → 所选口'}；标称 ${timeText(Number(turns.value)*60/Number(rpm.value))}（不含切阀与通信）。`;
    let index=-1;
    const recipeTimeline=recipe||(!hardware.busy&&hardware.recipe_run?.completed);
    const labels=recipeTimeline?steps:['停泵','切换所选口','有限动作运行','结束 / 保留记录'];
    if(command){const toBottle=Object.values(mapping).includes(command.port);index=recipeTimeline?(command.phase==='selecting'?(toBottle?3:0):(toBottle?4:1)):(command.phase==='selecting'?1:2);}
    if(!hardware.busy&&hardware.recipe_run?.completed)index=5;
    marks.forEach((mark,i)=>{mark.hidden=i>=labels.length;mark.textContent=labels[i]||'';mark.dataset.current=String(i===index);mark.dataset.done=String(index>=0&&i<index);});
    timelineNote.textContent=phase&&['mixing','rinse','purge'].includes(phase)?`正在${stages[phase]}。各来源加完后混合一次；清洗与吹扫是否执行，以本次配方设置为准。`:command?`当前仅执行阀${command.port}口有限动作；时间线表示当前阶段，不代表液体已实测送达。`:'每种来源吸取后反向追送；整瓶完成后的混合、清洗和吹扫均保留事件记录。';
    renderBottles(mapping);renderLogs();
  }
  [port,direction,rpm,turns].forEach(n=>n.addEventListener('input',render));
  window.addEventListener('atps:hardware-state',e=>{hardware=e.detail.state||hardware;available=e.detail.available;render();});
  setInterval(()=>{if(!page.hidden)render();},250);
  return {render};
};
