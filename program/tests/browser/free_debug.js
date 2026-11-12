// Isolated fake hardware fixture at 8879 only. No production endpoint.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),assert=require('assert'),path=require('path');
const OUT=path.join(__dirname,'test_output/free_debug');fs.mkdirSync(OUT,{recursive:true});
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1050}}),checks=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const check=(name,v)=>{assert(v,name);checks.push(name);console.log('PASS '+name)};
 const state=()=>page.evaluate(async()=>await(await fetch('/api/state')).json());
 async function idle(){for(let i=0;i<150;i++){await page.waitForTimeout(60);const s=await state();assert(!s.prompt,'Unexpected workflow prompt');if(!s.busy){assert(s.state!=='FAULT',s.message);assert(!s.message.startsWith('操作未执行'),s.message);await page.evaluate(()=>poll());return s;}}throw Error('Fake motion timeout');}
 async function go(id){await page.locator('#'+id).click();return idle();}
 await page.goto('http://127.0.0.1:8879');await page.waitForFunction(()=>window.renderFreeDebug&&document.querySelector('#connectBtn')?.disabled===false);
 check('断开时运动仍禁用',await page.locator('#chaseBtn').isDisabled());
 await go('connectBtn');await page.waitForTimeout(400);
 check('指定毫升按钮无需预充即可用',await page.locator('#volumeSend').isEnabled());
 await page.locator('#volumeSource').selectOption('14');await page.locator('#volumeSample').selectOption('3');await page.fill('#volumeMl','0.5');
 check('0.5mL预览一次吸取150步',(await page.locator('#volumePreview').innerText()).includes('一次吸取150步'));
 let v=await go('volumeSend');
 check('指定体积14到3一次加料且无额外混合',v.volume_run.completed&&v.volume_run.total_steps===150&&v.volume_run.source===14&&v.volume_run.sample===3&&v.volume_run.chunks.join(',')==='150'&&!v.volume_run.mixing_performed);
 check('不把比例估算写成实测体积',v.volume_run.actual_ml===null&&!v.volume_run.volume_verified);
 v=await go('volumeMix');check('最后手动混合只向所选3号瓶吹气',v.logs.filter(x=>x.event==='pulse_completed').at(-1).port===3);
 await page.locator('#manualPort').selectOption('15');await go('manualSelect');check('手动阀口切换可单独运行',(await state()).selected_port===15);
 await page.locator('#manualRotation').selectOption('cw');await page.fill('#manualRpm','20');await page.fill('#manualTurns','2');v=await go('manualRun');let m=v.logs.filter(x=>x.event==='pulse_completed').at(-1);check('手动CW20rpm两圈可向来源运行',m.port===15&&m.direction==='dispense'&&m.turns===2&&m.rpm===20);
 await page.locator('#manualRotation').selectOption('ccw');v=await go('manualRun');check('CCW反转可独立执行',v.logs.filter(x=>x.event==='pulse_completed').at(-1).direction==='aspirate');
 await go('manualRead');check('状态和手动控制在同卡片',await page.locator('#manualConsole #manualReadback').isVisible());
 await go('manualStop');v=await state();check('立即停止后保持连接且不恢复旧动作',v.connected&&!v.busy&&v.state==='STOPPED');check('停后可直接继续操作',await page.locator('#manualRun').isEnabled());
 for(const id of ['aspirateBtn','chaseBtn','doseBtn','bubbleBtn','primeBtn','benchCollectBtn','benchTestBtn','benchGroupBtn','tubeRecipe','addQueueBtn','prepareCaptureBtn'])check(id+'无需前序步骤即可使用',await page.locator('#'+id).isEnabled());
 check('预充文案6圈且直接吹扫',(await page.locator('#tubeWorkflows').innerText()).includes('6圈／50 rpm'));
 let s=await go('tubeRefill');let moves=s.logs.filter(x=>x.event==='pulse_completed').slice(-4);
 check('无弹窗执行13/14/15各6圈再16口15圈',moves.map(x=>x.port).join(',')==='13,14,15,16'&&moves.map(x=>x.turns).join(',')==='6,6,6,15');
 check('完成不伪造充满观察',!s.tubes.common_air_seen&&s.tubes.primed_ports.length===0&&!s.sources['13'].primed);
 await page.locator('#quickSource').selectOption('14');await page.locator('#quickSample').selectOption('2');
 s=await go('chaseBtn');check('未吸取可直接向所选2号追送',s.logs.filter(x=>x.event==='pulse_completed').at(-1).port===2);
 s=await go('doseBtn');check('无档案组合吸取追送正确路由',s.logs.filter(x=>x.event==='pulse_completed').slice(-2).map(x=>x.port).join(',')==='14,2');
 await page.locator('#addQueueBtn').click();s=await go('runBtn');check('当前参数可直接加入并运行队列',s.logs.filter(x=>x.event==='pulse_completed').slice(-4).map(x=>x.port).join(',')==='14,2,15,16');
 await page.locator('#doseRecalibration').evaluate(e=>e.open=true);
 s=await go('benchGroupBtn');check('五剂不用先单剂和观察',s.bench.last_run.completed_doses===5);
 s=await go('tubeFinish');check('收尾无搬管或观察弹窗',s.tubes.last_run.completed&&!s.tubes.fully_dry_verified);
 s=await go('prepareCaptureBtn');check('未静置可建立调试成像且时间仍未知',s.imaging.timing_prerequisites_bypassed&&s.imaging.sample_timing['1'].clear_at===null);
 check('0.15参考保持',(await page.locator('#benchSavedDose').innerText()).includes('0.15'));
 await page.locator('#manualConsole').screenshot({path:path.join(OUT,'UI泵阀手动控制台.png')});await page.locator('#volumeDebug').screenshot({path:path.join(OUT,'UI单次体积加料.png')});
 await page.setViewportSize({width:390,height:844});check('手机无水平溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 check('无脚本错误',errors.length===0);
 fs.writeFileSync(path.join(OUT,'FREE_DEBUG_BROWSER_TEST_REPORT.txt'),new Date().toISOString()+'\n'+checks.map((x,i)=>`${i+1}. PASS ${x}`).join('\n')+'\n仅隔离测试替身；未连接仪器。\n');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
