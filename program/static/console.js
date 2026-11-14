'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const samples = [1,2,3,4,5,6], sources = [10,11,12,13,14,15];
  let state = {}, token = '', requesting = false, polling = false, initialized = false, epoch = 0;
  let calibrationBinding = null, stream = null, cameraGeneration = 0, cameraOpening = false;
  let frame = null, capturedAt = null, frameOrigin = null, loadedAt = null, rois = [], manualRois = false, analysis = null, previous = null, batchId = null;
  let frameVersion = 0, pendingCapture = null;
  const phaseNames = {single:'单相候选（未见明显界面）',two_phase:'两相候选',uncertain:'不确定'};
  const node = (tag, text, attrs = {}) => { const n = document.createElement(tag); if(text !== undefined)n.textContent=text; for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v); return n; };
  function notice(text, error=false) { $('notice').textContent=text; $('notice').dataset.error=String(error); }
  function numeric(id, label, min, max, integer=false) {
    const raw=$(id).value.trim(), n=Number(raw);
    if(!raw || !Number.isFinite(n) || n<min || n>max || (integer && !Number.isInteger(n)))throw Error(`${label}须为${min}—${max}之间的${integer?'整数':'数值'}`);
    return n;
  }
  async function api(path, body) {
    if(body !== undefined && !token) { const r=await api('/api/token');token=r.token;if(!token)throw Error('未获取控制令牌，请刷新页面'); }
    const response=await fetch(path,body === undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':token},body:JSON.stringify(body)});
    let data;try { data=await response.json(); } catch { throw Error('服务返回格式异常'); }
    if(!response.ok || data.ok === false || data.error)throw Error(data.error || `服务请求失败 (${response.status})`);
    return data;
  }
  async function operate(work, message='请求已接受，请查看设备读回与运行记录。', checkHardware=true) {
    if(requesting || (checkHardware && state.busy)) { notice('设备或请求正忙，请等当前动作结束后重新点击；未加入队列。',true);return; }
    requesting=true;const current=epoch;
    try { const result=await work();if(result?.state)state=result.state;if(current===epoch)notice(message);render(); }
    catch(e) { if(current===epoch)notice(e.message,true); }
    finally { requesting=false;await poll(); }
  }
  const action = (name,payload={}) => api('/api/action',{action:name,...payload});
  function bind(id, work, message, serial=true) { $(id).addEventListener('click',()=>operate(work,message,serial)); }
  function local(id, work) { $(id).addEventListener('click',async()=>{try{await work();}catch(e){notice(e.message,true);}}); }
  for(const select of document.querySelectorAll('[data-samples], [data-five-samples]')) {
    for(const n of select.hasAttribute('data-five-samples')?samples.slice(0,5):samples)select.append(node('option',`${n}号瓶`,{value:n}));
  }
  for(const p of [...samples,...sources,16])$('manualPort').append(node('option',`${p}口 · ${p<7?'样品'+p:p===16?'空气':p===15?'清水':'来源'}`,{value:p}));
  $('manualPort').value='16';
  for(const p of sources) {
    const tr=node('tr'),td=node('td');td.append(node('input',undefined,{id:`volume${p}`,type:'number',min:0,max:10,step:.001,value:0,'aria-label':`${p}口加入体积mL`}));tr.append(node('td',`${p}口${p===15?' · 清水':''}`),td);$('recipeRows').append(tr);
    $('volume'+p).addEventListener('input',preview);
  }
  function checks(id, values, attr, defaults) { for(const p of values){const label=node('label',undefined,{class:'check'}),input=node('input',undefined,{type:'checkbox',[attr]:p});input.checked=defaults.includes(p);label.append(input,document.createTextNode(`${p}口`));$(id).append(label);} }
  checks('sourceChecks',sources,'data-source',[13,14,15]);checks('sampleChecks',samples,'data-sample-line',[1]);
  const selected = attr => [...document.querySelectorAll(`[${attr}]:checked`)].map(n=>Number(n.getAttribute(attr)));
  async function scanPorts() {
    const data=await api('/api/serial-ports');$('serialOptions').replaceChildren();
    for(const p of data.ports || [])$('serialOptions').append(node('option',`${p.description || ''}${p.is_ch340?' · CH340':''}`,{value:p.device}));
    if(!$('serialPort').value && data.suggested_port)$('serialPort').value=data.suggested_port;
    const list=(data.ch340_ports || []).map(p=>p.device);
    $('serialStatus').textContent=state.connected?`实际连接 ${state.config?.communication?.port || '未知串口'}；泵阀共用 RS485。`:`检测到 CH340：${list.join('、') || '无'}。${list.length>1?'多个设备请取消自动识别并选择串口。':'自动连接时会重新识别端口。'}`;
  }
  bind('scanPortsBtn',scanPorts,'串口列表已更新；扫描不会打开设备。',false);
  bind('connectBtn',()=>{
    const automatic=$('autoDetectPort').checked,port=$('serialPort').value.trim();
    if(!automatic && !port)throw Error('请填写或选择共用串口');
    return api('/api/connect',automatic?{mode:'real',auto_detect:true}:{mode:'real',auto_detect:false,pump_port:port,valve_port:port});
  },'连接请求已完成；实际串口见设备连接状态。');
  bind('disconnectBtn',()=>api('/api/disconnect',{}),'已请求断开，设备状态以服务读回为准。');
  $('stopBtn').addEventListener('click',async()=>{
    ++epoch;
    const announce=status=>{window.dispatchEvent(new CustomEvent('atps:stop-status',{detail:status}));$('runStatus').textContent=({requested:'已请求停止',confirmed:'已确认停止',unconfirmed:'无法确认停止'})[status];};
    announce('requested');notice('已请求停止；等待设备读回确认。');
    try {
      const result=await api('/api/stop',{});if(result.state)state=result.state;render();
      const status=state.stop_status==='confirmed'?'confirmed':state.stop_status==='requested'?'requested':'unconfirmed';
      announce(status);notice(status==='confirmed'?'已确认停止；设备连接保留。':status==='requested'?'已请求停止，尚未确认。':'无法确认停止；请核对设备读回与实体急停。',status==='unconfirmed');
    } catch(e) {announce('unconfirmed');notice('无法确认停止：'+e.message,true);}
  });
  bind('manualSelect',()=>action('select_port',{port:Number($('manualPort').value)}));
  bind('manualRead',()=>action('read_status'));
  bind('manualRun',()=>action('manual_motion',{port:Number($('manualPort').value),rotation:$('manualRotation').value,rpm:numeric('manualRpm','转速',.1,100),turns:numeric('manualTurns','圈数',1,100,true)}));
  for(const [id,name] of [['washBtn','wash'],['purgeBtn','purge'],['washPurgeBtn','reset']])bind(id,()=>action(name));
  bind('recipeStart',()=>{
    const items=sources.map(source=>({source,volume_ml:numeric('volume'+source,`${source}口体积`,0,10)})).filter(r=>r.volume_ml>0);
    if(!items.length)throw Error('请至少填写一项大于 0 的体积');
    if(items.reduce((sum,i)=>sum+i.volume_ml,0)>10.000000001)throw Error('单瓶本次配方总体积不能超过 10 mL；请扣除瓶内已有液体');
    return action('recipe',{sample:Number($('recipeSample').value),items,mix_after:true,purge_after:true,mixing:{steps:numeric('mixSteps','混合步数',1,20000,true),rpm:numeric('mixRpm','混合转速',.1,100),timeout_s:numeric('mixTimeout','混合超时',1,120)}});
  });
  for(const [id,operation] of [['refillBtn','refill'],['drainSourcesBtn','drain_sources'],['drainSamplesBtn','drain_samples'],['finishBtn','finish']])bind(id,()=>{
    const src=selected('data-source'),dst=selected('data-sample-line');
    if(operation!=='drain_samples' && !src.length)throw Error('请至少选择一个来源口');
    if(['drain_samples','finish'].includes(operation) && !dst.length)throw Error('请至少选择一条样品管');
    return action('tubes',{operation,sources:src,samples:dst});
  });
  function calibration() { return {sample:Number($('calSample').value),aspirate_steps:numeric('calAspirateSteps','吸取步数',1,100,true),aspirate_rpm:numeric('calAspirateRpm','吸取转速',.1,100),chase_steps:numeric('calChaseSteps','追送步数',1,3000,true),chase_rpm:numeric('calChaseRpm','追送转速',.1,100)}; }
  function calFingerprint() { return JSON.stringify(['calSample','calAspirateSteps','calAspirateRpm','calChaseSteps','calChaseRpm','density'].map(id=>$(id).value)); }
  for(const [id,count] of [['calSingle',1],['calFive',5]])bind(id,()=>action('bench',{operation:'dose_water',...calibration(),count}));
  bind('referenceSave',async()=>{const r=await api('/api/reference',{mean_per_dose_ml:numeric('referenceMl','每剂实测体积',.01,1),...calibration()});state.water_dose_reference=r.reference;return r;},'已保存实测每剂体积参考；未启动泵。');
  for(const id of ['mass1','mass2','mass3'])$(id).addEventListener('input',()=>{if(!calibrationBinding)calibrationBinding=calFingerprint();});
  local('clearMasses',()=>{for(const id of ['mass1','mass2','mass3'])$(id).value='';calibrationBinding=null;notice('已清空当前表单称重读数；已保存报告保持原样。');});
  bind('calReport',async()=>{
    if(calibrationBinding && calibrationBinding!==calFingerprint())throw Error('称重读数对应的参数已改变；请恢复本轮参数，或清空读数重新测量');
    const r=await api('/api/bench/report',{method:'batch_water',readings_g:[1,2,3].map(i=>numeric('mass'+i,`第${i}组净质量`,.000001,1000)),density_g_ml:numeric('density','清水密度',.95,1.05),balance_resolution_g:.01,turns:5,doses_per_group:5,...calibration(),note:$('calNote').value.trim()+'；清水5剂一组，组前倒空并去皮。'});
    if(!r.report)throw Error('未收到报告，请保留原始读数');
    $('calReportResult').textContent=`平均每剂 ${r.report.mean_per_dose_ml} mL；组间 RSD ${r.report.group_rsd_percent==null?'尚不能计算':r.report.group_rsd_percent+'%'}。\n单剂重复性、母液等效量与交叉污染未验证。\n已保存：${r.saved_file || '服务报告文件'}`;
    $('referenceMl').value=r.report.mean_per_dose_ml;return r;
  },'称重报告已保存；后续换算读取最新参考。');
  function preview() {
    const values=sources.map(p=>Number($('volume'+p).value));
    const total=values.reduce((sum,n)=>sum+n,0),ref=state.water_dose_reference,p=ref?.parameters;
    $('recipePreview').textContent=values.every(n=>Number.isFinite(n)&&n>=0)?`本次合计 ${Number(total.toFixed(4))} mL（请求量）。整数步数会取整；实际体积待测。`:'请检查各来源体积。';
    $('referenceSummary').textContent=p&&ref.mean_per_dose_ml?`当前保存参考：${p.aspirate_steps}步 ≈ ${ref.mean_per_dose_ml} mL；吸取 ${p.aspirate_rpm} rpm，基础追送 ${p.chase_steps}步 / ${p.chase_rpm} rpm。`:'尚未读取保存参考；等待服务。';
  }
  function renderTelemetry(available=true) {
    const h=state.hardware_status;
    if(!available || !state.connected || !h){$('pumpStatus').textContent=!available?'通信中断 · 状态未知':h?.pump_status==='stop_requested'?'已请求停止 · 未确认':'状态未知';$('valveStatus').textContent='阀位未知';$('flowStatus').textContent='等待有效设备读回';return;}
    const fresh=h.pump_at!=null && Date.now()/1000-h.pump_at<3;
    const direction=h.direction==='cw'?'CW 正转':h.direction==='ccw'?'CCW 反转':'转向未知';
    $('pumpStatus').textContent=h.pump_status==='running'?(fresh?`${direction} · 运行中`:'运行状态待核对 · 读回已过期'):h.pump_status==='stopped'?'已查询确认停止':h.pump_status==='stop_requested'?'已请求停止 · 未确认':h.pump_status==='starting'?`${direction} · 待运行确认`:'状态未知';
    const confirmed=h.valve_status==='confirmed'&&h.valve_port!=null;
    $('valveStatus').textContent=confirmed?`${h.valve_port}口 · 已读回`:h.valve_status==='switching'?`正在切换 → ${h.target_port ?? '未知'}口`:'阀位未知';
    $('flowStatus').textContent=fresh&&h.pump_status==='running'?(h.flow==='aspirate'?`${confirmed?h.valve_port+'口':'所选口'} → 公共管 → C端废液`:h.flow==='dispense'?`C端空气 → 公共管 → ${confirmed?h.valve_port+'口':'所选口'}`:'流向待核对'):'无已确认的进行中流向';
    $('telemetryDetail').textContent=`末次泵查询：${h.pump_at?new Date(h.pump_at*1000).toLocaleTimeString('zh-CN',{hour12:false}):'未知'}${h.rpm!=null?'；读回转速 '+h.rpm+' rpm':''}${h.remaining!=null?'；剩余 '+h.remaining+(h.unit==='turns'?'圈':'步'):''}。空闲保留末次读回；面板手动动作请点击刷新读回。`;
  }
  function render() {
    $('connectionStatus').textContent=state.connected?`${state.config?.communication?.port || '串口未知'} · 泵阀共用 RS485`:'未连接';
    $('runStatus').textContent=state.state==='STOPPING'?'已请求停止':state.state==='STOPPED'&&state.stop_status==='confirmed'?'已确认停止':state.stop_status==='unconfirmed'&&state.state==='FAULT'?'无法确认停止':state.busy?'执行中':state.state==='FAULT'?'操作异常':state.connected?'已连接 · 待操作':'未连接';
    $('serverMessage').textContent=state.message || '';
    if(state.connected)$('serialStatus').textContent=`实际连接 ${state.config?.communication?.port || '未知串口'}；连接与泵运动状态分别显示。`;
    renderTelemetry();preview();
    if(!initialized && state.water_dose_reference?.parameters) {
      initialized=true;const p=state.water_dose_reference.parameters;
      for(const [id,key] of [['calSample','sample'],['calAspirateSteps','aspirate_steps'],['calAspirateRpm','aspirate_rpm'],['calChaseSteps','chase_steps'],['calChaseRpm','chase_rpm']])if(p[key]!=null)$(id).value=p[key];
      $('referenceMl').value=state.water_dose_reference.mean_per_dose_ml;
    }
    const r=state.recipe_run;
    if(r)$('recipeProgress').textContent=`${r.sample}号瓶 · ${r.cancelled?'已中止':r.completed?'动作完成':r.error?'动作中断':'执行中'}\n液段 ${r.completed_plugs ?? 0}/${r.total_plugs ?? '?'}；指令 ${r.completed_commands ?? 0}/${r.total_commands ?? '?'}。请求 ${r.requested_ml} mL，参考估算 ${r.estimated_ml==null?'未知':Number(r.estimated_ml.toFixed(4))} mL，实际体积待测。\n混合 ${r.mixing_performed?'已完成':'未完成'}；瓶末16口吹扫 ${r.purge_performed?'已完成':'未完成'}。${r.error?'\n'+r.error:''}`;
    const t=state.tubes?.last_run;if(t)$('tubeProgress').textContent=`最近管路流程：${({refill:'来源预充',drain_sources:'来源排液',drain_samples:'样品管排液',finish:'组合结束排液'})[t.operation] || t.operation} · ${t.completed?'动作完成':'进行中或中断'}。实际充满或排空情况需观察。`;
    $('logs').textContent=(state.logs || []).slice(-80).map(x=>typeof x==='string'?x:JSON.stringify(x)).join('\n') || '尚无日志';
  }
  async function poll() {
    if(polling)return;polling=true;
    try { state=await api('/api/state');render(); }
    catch(e){renderTelemetry(false);$('runStatus').textContent='服务不可用';$('serverMessage').textContent=e.message;}
    finally{polling=false;}
  }
  async function listCameras() {
    if(!navigator.mediaDevices?.enumerateDevices)throw Error('当前浏览器不支持本地摄像头枚举');
    const chosen=$('cameraSelect').value,devices=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='videoinput');
    $('cameraSelect').replaceChildren(node('option','系统默认摄像头',{value:''}));
    devices.forEach((d,i)=>$('cameraSelect').append(node('option',d.label || `本地摄像头 ${i+1}（授权后显示名称）`,{value:d.deviceId})));
    if(devices.some(d=>d.deviceId===chosen))$('cameraSelect').value=chosen;
  }
  function closeCamera() { ++cameraGeneration;cameraOpening=false;if(stream)stream.getTracks().forEach(t=>t.stop());stream=null;$('cameraVideo').srcObject=null;$('cameraVideo').hidden=true;$('framePlaceholder').hidden=!!frame;$('cameraStatus').textContent='摄像头已关闭；已取帧仍可分析。'; }
  local('cameraList',listCameras);
  local('cameraOpen',async()=>{
    if(cameraOpening || stream)throw Error('摄像头正在打开或已打开；更换设备请先关闭');
    if(!navigator.mediaDevices?.getUserMedia)throw Error('当前浏览器不支持本地摄像头');
    cameraOpening=true;const generation=++cameraGeneration;
    try {
      const deviceId=$('cameraSelect').value;
      const opened=await navigator.mediaDevices.getUserMedia({audio:false,video:deviceId?{deviceId:{exact:deviceId}}:true});
      if(generation!==cameraGeneration){opened.getTracks().forEach(t=>t.stop());return;}
      stream=opened;$('cameraVideo').srcObject=stream;await $('cameraVideo').play();
      $('cameraVideo').hidden=false;$('imageCanvas').hidden=true;$('framePlaceholder').hidden=true;
      $('cameraStatus').textContent='摄像头已打开 · 未镜像预览；点击“取当前帧”后分析。';await listCameras();
    } finally { if(generation===cameraGeneration)cameraOpening=false; }
  });
  local('cameraClose',()=>{closeCamera();if(frame){$('imageCanvas').hidden=false;drawFrame();}});
  function setFrame(source,width,height,time,keepPrevious) {
    previous=keepPrevious&&frame?.width===width&&frame?.height===height?analysis:null;
    frame=document.createElement('canvas');frame.width=width;frame.height=height;frame.getContext('2d').drawImage(source,0,0,width,height);
    capturedAt=time;frameOrigin=keepPrevious?'camera':'import';loadedAt=keepPrevious?null:new Date().toISOString();analysis=null;batchId=null;rois=[];manualRois=false;++frameVersion;
    $('imageCanvas').width=width;$('imageCanvas').height=height;$('imageCanvas').hidden=false;$('cameraVideo').hidden=true;$('framePlaceholder').hidden=true;
    $('archiveStatus').textContent='当前帧尚未保存。';$('manualLabelStatus').textContent='';$('analysisSummary').textContent='当前帧尚未分析。';$('analysisRows').replaceChildren();drawRois();drawFrame();
  }
  local('freezeBtn',()=>{
    const video=$('cameraVideo');if(!stream || !video.videoWidth)throw Error('请先打开摄像头并等待画面');
    setFrame(video,video.videoWidth,video.videoHeight,new Date().toISOString(),true);
    $('cameraStatus').textContent='已取当前帧（未镜像）；摄像头仍开启，可再次取帧比较。';
  });
  $('imageFile').addEventListener('change',async()=>{
    const file=$('imageFile').files[0];if(!file)return;
    try {
      closeCamera();const url=URL.createObjectURL(file),img=new Image();
      try{await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=()=>reject(Error('图像无法读取'));img.src=url;});if(img.naturalWidth>8192 || img.naturalHeight>8192 || img.naturalWidth*img.naturalHeight>16000000)throw Error('图像须不超过1600万像素，单边不超过8192像素，请先缩小');setFrame(img,img.naturalWidth,img.naturalHeight,null,false);}finally{URL.revokeObjectURL(url);}
      $('cameraStatus').textContent='已导入本地图像 · 摄像头关闭；真实拍摄时间未知，单独记录载入时间。';
    }catch(e){notice(e.message,true);}finally{$('imageFile').value='';}
  });
  function needFrame() { if(!frame)throw Error('请先取一帧或导入本地图像'); }
  function drawFrame() {
    if(!frame)return;const c=$('imageCanvas'),ctx=c.getContext('2d');ctx.drawImage(frame,0,0);
    const scale=Math.max(1,frame.width/700);ctx.lineWidth=2*scale;ctx.font=`bold ${16*scale}px Arial`;
    for(const r of rois){ctx.strokeStyle='#111';ctx.strokeRect(r.x,r.y,r.width,r.height);ctx.fillStyle='#fff';ctx.fillRect(r.x,r.y,34*scale,24*scale);ctx.fillStyle='#111';ctx.fillText(String(r.sample),r.x+8*scale,r.y+18*scale);}
    for(const b of analysis?.bottles || [])if(b.classification==='two_phase' && b.interface_y!=null){const r=rois.find(r=>r.sample===b.sample);if(r){ctx.setLineDash([5*scale,4*scale]);ctx.strokeStyle='#111';ctx.beginPath();ctx.moveTo(r.x,b.interface_y);ctx.lineTo(r.x+r.width,b.interface_y);ctx.stroke();ctx.setLineDash([]);}}
  }
  function invalidateAnalysis() { analysis=null;previous=null;batchId=null;++frameVersion;$('manualLabelStatus').textContent='';$('analysisRows').replaceChildren();$('analysisSummary').textContent='区域已修改，请重新分析。';$('archiveStatus').textContent='当前区域变更尚未保存。';drawFrame(); }
  function drawRois() {
    $('roiRows').replaceChildren();
    for(const r of rois){const tr=node('tr');tr.append(node('td',r.sample));for(const key of ['x','y','width','height']){const td=node('td'),input=node('input',undefined,{type:'number',min:key==='x'||key==='y'?0:1,step:1,'data-sample':r.sample,'data-roi':key,'aria-label':`${r.sample}号瓶 ${key}`});input.value=r[key];input.addEventListener('change',()=>{const n=Number(input.value);if(!input.value.trim() || !Number.isInteger(n) || n<0){notice('区域坐标须为非负整数，宽高须为正数。',true);input.value=r[key];return;}r[key]=n;manualRois=true;invalidateAnalysis();});td.append(input);tr.append(td);}$('roiRows').append(tr);}
  }
  function analyze(auto=false) {
    needFrame();if(!window.PhaseAnalysis)throw Error('本地判相脚本未加载，请刷新页面');
    ++frameVersion;batchId=null;
    if(auto)manualRois=false;
    analysis=window.PhaseAnalysis.analyzeFrame(frame.getContext('2d',{willReadFrequently:true}).getImageData(0,0,frame.width,frame.height),{...(manualRois?{rois}:{}),...(previous?{previous}: {})});
    if(analysis.detection?.status!=='invalid')rois=analysis.rois || [];
    drawRois();drawFrame();$('analysisRows').replaceChildren();
    const detection=analysis.detection || {};
    $('analysisSummary').textContent=`${detection.status==='ok'?'区域已定位；结论为光学候选':'不确定：区域检测不足或无效'}。${detection.reason || ''}\n${analysis.limitation || '未经真实实验准确率验证；反光、气泡、低对比及不稳定帧应人工复核。'}`;
    for(const b of analysis.bottles || []){const tr=node('tr');tr.append(node('td',`${b.sample}号`),node('td',`${phaseNames[b.classification] || '不确定'}\n${b.reason || ''}`));$('analysisRows').append(tr);}
    return analysis;
  }
  local('analyzeBtn',()=>analyze());local('autoRoisBtn',()=>analyze(true));
  local('manualRoisBtn',()=>{
    needFrame();manualRois=true;const w=Math.floor(frame.width/7),h=Math.floor(frame.height*.7),gap=frame.width/5;
    rois=[1,2,3,4,5].map(sample=>({sample,x:Math.round((5-sample)*gap+(gap-w)/2),y:Math.floor(frame.height*.15),width:w,height:h}));
    drawRois();invalidateAnalysis();
  });
  let drag=null;
  const point=e=>{const r=$('imageCanvas').getBoundingClientRect(),scale=Math.min(r.width/frame.width,r.height/frame.height),left=r.left+(r.width-frame.width*scale)/2,top=r.top+(r.height-frame.height*scale)/2;return {x:Math.max(0,Math.min(frame.width-1,Math.round((e.clientX-left)/scale))),y:Math.max(0,Math.min(frame.height-1,Math.round((e.clientY-top)/scale)))};};
  $('imageCanvas').addEventListener('pointerdown',e=>{if(!frame || !$('roiPanel').open)return;drag=point(e);$('imageCanvas').setPointerCapture(e.pointerId);});
  $('imageCanvas').addEventListener('pointerup',e=>{
    if(!drag)return;const end=point(e),start=drag;drag=null;const sample=Number($('roiSample').value),r={sample,x:Math.min(start.x,end.x),y:Math.min(start.y,end.y),width:Math.abs(end.x-start.x),height:Math.abs(end.y-start.y)};
    if(r.width<20 || r.height<40){notice('区域至少需要 20×40 像素，请沿瓶身重新拖画。',true);return;}
    rois=rois.filter(r=>r.sample!==sample).concat(r).sort((a,b)=>a.sample-b.sample);manualRois=true;drawRois();invalidateAnalysis();
  });
  const unfinishedBatch = () => state.imaging?.batch_id && !state.imaging.complete && !state.imaging.discarded && !state.imaging.historical ? state.imaging.batch_id : null;
  bind('captureBtn',async()=>{
    needFrame();
    if(pendingCapture && pendingCapture.version!==frameVersion)throw Error('有一份未完成的旧帧存档；请先放弃未完成存档批次，再保存当前图像');
    if(!pendingCapture){
      if(unfinishedBatch())throw Error('服务中有未完成存档批次；请先点击“放弃未完成存档批次”，再保存当前图像');
      if(!analysis)analyze();
      if(analysis.detection?.status==='invalid')throw Error('手动区域无效，请调整区域后重新分析再保存');
      // Freeze pixels and every associated field before the first network await.
      pendingCapture={version:frameVersion,batch:null,payload:{png_base64:frame.toDataURL('image/png').split(',')[1],width:frame.width,height:frame.height,frame_origin:frameOrigin,captured_at:capturedAt,...(loadedAt?{loaded_at:loadedAt}:{}),rois:structuredClone(rois),alignment_confirmed:false,allow_partial_rois:true,analysis:structuredClone(analysis)}};
    }
    const draft=pendingCapture;
    if(!draft.batch){
      try{const begin=await api('/api/imaging/begin',{targets:[1,2,3,4,5]});draft.batch=begin.batch_id || begin.imaging?.batch_id || begin.state?.imaging?.batch_id;if(!draft.batch)throw Error('服务未返回采集批次');}
      catch(e){pendingCapture=null;throw e;}
    }
    let result;
    try{result=await api('/api/capture',{batch_id:draft.batch,...draft.payload});}
    catch(e){$('archiveStatus').textContent='存档失败；同一帧可点击保存重试，或放弃未完成存档批次。';throw e;}
    pendingCapture=null;
    if(frameVersion===draft.version){batchId=draft.batch;$('archiveStatus').textContent=`当前帧已保存；区域 ${draft.payload.rois.length}/5，批次 ${batchId}。未定位的瓶保持未分配。`;}
    else{$('archiveStatus').textContent=`先前图像已保存（${draft.batch}）；当前图像或区域已改变，尚未保存。`;}
    return result;
  },'原图、区域与自动候选存档请求已完成；当前帧状态见图像下方。');
  bind('discardCaptureBtn',async()=>{
    const pending=pendingCapture?.batch || unfinishedBatch();
    if(!pending)throw Error('没有可放弃的未完成存档批次');
    const result=await api('/api/discard',{batch_id:pending});pendingCapture=null;
    $('archiveStatus').textContent='已放弃未完成存档批次；可重新保存当前图像。';return result;
  },'未完成存档批次已放弃；当前图像仍保留在页面。');
  bind('labelSave',async()=>{
    if(!batchId)throw Error('请先保存当前帧的原图与分析记录');
    const sample=Number($('labelSample').value);if(!rois.some(r=>r.sample===sample))throw Error('该瓶没有已保存的区域，先补充区域并保存图像');
    const savedVersion=frameVersion,savedBatch=batchId,labelText=$('manualClassification').selectedOptions[0].textContent;
    const result=await api('/api/label',{batch_id:batchId,sample,classification:$('manualClassification').value,note:$('labelNote').value.trim()});
    if(savedVersion===frameVersion&&savedBatch===batchId)$('manualLabelStatus').textContent=`${sample}号瓶人工复核已保存：${labelText}。自动候选保持独立。`;return result;
  },'人工复核记录已保存。');
  window.addEventListener('pagehide',closeCamera);
  preview();
  (async()=>{try{const data=await api('/api/token');token=data.token;await poll();await scanPorts();}catch(e){notice(e.message,true);}setInterval(poll,800);})();
window.ATPSCamera={status(){const v=$('cameraVideo');const ready=!!stream&&v.readyState>=2&&!v.paused&&frameOrigin==='camera'&&frame&&frame.width===v.videoWidth&&frame.height===v.videoHeight&&rois.length===5;return {ready,rois:ready?rois.map(r=>({...r})):[],width:ready?v.videoWidth:0,height:ready?v.videoHeight:0};},capture(){const v=$('cameraVideo');if(!this.status().ready)throw Error('实时相机离线');const c=document.createElement('canvas');c.width=v.videoWidth;c.height=v.videoHeight;const x=c.getContext('2d',{willReadFrequently:true});x.drawImage(v,0,0);return {...this.status(),png_base64:c.toDataURL('image/png').split(',')[1],captured_at:new Date().toISOString(),analysis:window.PhaseAnalysis.analyzeFrame(x.getImageData(0,0,c.width,c.height),{rois})};}};})();
