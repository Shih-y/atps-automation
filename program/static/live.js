'use strict';
(() => {
  const rotor=document.getElementById('livePumpRotor');
  if(!rotor)return;
  let freshUntil=0;
  const name=p=>p>=1&&p<=6?`${p}号样品瓶`:p===15?'15口清水':p===16?'16口空气':`${p}口母液`;
  function unavailable(text){
    rotor.classList.remove('turning');
    $('livePump').textContent=text;
    $('liveValve').textContent='阀位未知';
    $('liveFlow').textContent='等待有效设备状态';
    $('liveDetail').textContent='当前显示不能证明设备已停止；必要时使用设备本地停止。';
    document.querySelectorAll('#portMap>div').forEach(e=>e.classList.remove('live-port'));
  }
  window.renderHardwareStatus=(s,available=true)=>{
    freshUntil=Date.now()+3000;
    if(!available){unavailable('通信中断 · 状态未知');return;}
    const h=s.hardware_status;
    if(!s.connected){unavailable(h?.pump_status==='stop_requested'?'已请求停止 · 未确认':'未连接 · 状态未知');return;}
    if(!h){unavailable('尚无设备查询结果');return;}
    const running=h.pump_status==='running';
    const recent=h.pump_at!=null&&Date.now()/1000-h.pump_at<3;
    const direction=h.direction==='cw'?'正转 CW ↻':h.direction==='ccw'?'反转 CCW ↺':'转向未知';
    rotor.classList.toggle('turning',running&&recent);
    rotor.dataset.direction=h.direction||'';
    let pump='状态未知';
    if(running)pump=recent?`${direction} · 运行中`:'查询未更新 · 运行状态待核对';
    else if(h.pump_status==='starting')pump=`${direction} · 指令已接受，待运行确认`;
    else if(h.pump_status==='stopped')pump='停止 · 已查询确认';
    else if(h.pump_status==='stop_requested')pump='已请求停止 · 未确认';
    $('livePump').textContent=pump;
    const confirmed=h.valve_status==='confirmed'&&h.valve_port!=null;
    $('liveValve').textContent=h.valve_status==='switching'?`正在切换 → ${h.target_port}口`:confirmed?`${h.valve_port}口 · ${name(h.valve_port)}（已读回）`:'阀位未知';
    let flow='泵无已确认的进行中动作';
    if(running||h.pump_status==='starting')flow=h.flow==='aspirate'?`${confirmed?name(h.valve_port):'所选口'} → 公共管 → 泵 → C端废液`:h.flow==='dispense'?`C端空气 → 泵 → 公共管 → ${confirmed?name(h.valve_port):'样品口'}`:'流向待核对';
    $('liveFlow').textContent=flow;
    const remaining=h.remaining==null?'':` · 剩余${h.remaining}${h.unit==='turns'?'圈':'步'}`;
    const rpm=(running||h.pump_status==='starting')&&h.rpm!=null?`已读回设定转速 ${h.rpm} rpm`:'泵未显示运行';
    const at=h.pump_at?new Date(h.pump_at*1000).toLocaleTimeString('zh-CN',{hour12:false}):'尚无';
    $('liveDetail').textContent=`${rpm}${remaining} · 泵状态读回 ${at}。运行状态来自设备查询，转向来自已接受指令；空闲保留末次读回，不监测面板手动改动。`;
    document.querySelectorAll('#portMap>div').forEach(e=>e.classList.toggle('live-port',confirmed&&Number(e.querySelector('b')?.textContent)===h.valve_port));
    if($('selectedPortText'))$('selectedPortText').textContent=$('liveValve').textContent;
  };
  setInterval(()=>{if(freshUntil&&Date.now()>freshUntil)unavailable('页面未收到更新 · 状态未知');},500);
})();
