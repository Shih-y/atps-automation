const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),assert=require('assert');
const OUT=require('path').resolve(__dirname,'../../..');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050}}),checks=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const check=(name,ok)=>{assert(ok,name);checks.push(name);console.log('PASS '+name)};
 const state=()=>page.evaluate(async()=>await(await fetch('/api/state')).json());
 const api=(path,body)=>page.evaluate(async({path,body})=>{const {token}=await(await fetch('/api/token')).json();return await(await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':token},body:JSON.stringify(body)})).json()},{path,body});
 async function idle(){for(let i=0;i<100;i++){await page.waitForTimeout(80);const s=await state();if(!s.busy){await page.evaluate(()=>poll());await page.waitForTimeout(100);assert(!s.message.startsWith('操作未执行'),s.message);assert(s.state!=='FAULT',s.message);return s;}assert(!s.prompt,'Bench should not ask duplicate confirmation');}throw Error('Timeout');}
 async function go(id){await page.locator('#'+id).click();return idle();}
 await page.goto('http://127.0.0.1:8879');await page.waitForFunction(()=>window.renderBench&&document.querySelector('#benchFillBtn'));
 await api('/api/disconnect',{});await api('/api/config',{config:JSON.parse(fs.readFileSync(OUT+'/program/config.default.json','utf8'))});await page.reload();await page.waitForTimeout(900);
 check('称重向导直接可见且断开时不开泵',await page.locator('#benchWizard').isVisible()&&await page.locator('#benchFillBtn').isDisabled());
 check('内置三次净质量且默认折叠可选重校准',(await page.locator('#benchBulkReference').innerText()).includes('4.61、4.55、4.57')&&(await page.locator('#benchBulkReference').innerText()).includes('0.915333')&&!(await page.locator('#bulkRecalibration').evaluate(e=>e.open)));
 check('更新为0.15mL后保留原动作参数',await page.locator('#benchAspirateSteps').inputValue()==='45'&&await page.locator('#benchChaseSteps').inputValue()==='1000'&&await page.locator('#benchAspirateRpm').inputValue()==='5'&&await page.locator('#benchChaseRpm').inputValue()==='15');check('显示0.15mL新参考、0.75mL组目标且校准折叠',(await page.locator('#benchSavedDose').innerText()).includes('0.15')&&(await page.locator('#benchSavedDose').innerText()).includes('0.75')&&(await page.locator('#benchSavedDose').innerText()).includes('尚未完成3组')&&!(await page.locator('#doseRecalibration').evaluate(e=>e.open)));await go('connectBtn');const beforeSkip=(await state()).logs.filter(x=>x.event==='pulse_completed').at(-1)?.at;await go('benchSkipFillBtn');await go('benchSkipPurgeBtn');let skipped=await state();check('第1及3步可跳过且不发送运动',skipped.bench.water_ready&&skipped.bench.air_ready&&skipped.common==='air_unverified'&&skipped.logs.filter(x=>x.event==='pulse_completed').at(-1)?.at===beforeSkip);await go('benchFillBtn');let s=await state();check('20圈50rpm后仍需实际出水观察',!s.bench.water_ready&&s.bench.last_run.completed&&s.logs.some(x=>x.event==='pulse_completed'&&x.turns===20&&x.rpm===50));
 await go('benchWaterSeenBtn');check('默认允许跳过五圈称重直接吹扫',await page.locator('#benchPurgeBtn').isEnabled()&&(await page.locator('#benchStatus').innerText()).includes('直接到第3步'));await page.locator('#bulkRecalibration').evaluate(e=>e.open=true);for(let i=1;i<=3;i++){await go('benchCollectBtn');await page.fill('#benchBulk'+i,String([4.5,4.51,4.49][i-1]));}
 await go('benchBulkReportBtn');s=await state();check('三次整圈称重得0.9mL每圈且保持连接',(await page.locator('#benchBulkReport').innerText()).includes('0.9')&&s.connected&&s.bench.reports.length===1&&!s.config.reset.validated);
 await go('benchPurgeBtn');await go('benchAirSeenBtn');await page.fill('#benchChaseSteps','3000');await page.fill('#benchAspirateRpm','8');await page.fill('#benchChaseRpm','30');await go('benchTestBtn');s=await state();check('3000步可调30rpm追送且46秒超时',s.logs.some(x=>x.event==='pulse_completed'&&x.port===1&&x.steps===3000&&x.rpm===30&&x.timeout_s===46)&&s.bench.last_run.aspirate_rpm===8&&s.bench.last_run.count===1&&s.bench.last_run.completed&&!s.profiles.length&&s.samples['1'].condition==='unknown');
 await page.locator('#doseRecalibration').evaluate(e=>e.open=true);await go('benchDoseSeenBtn');await go('benchAfterDoseAirSeenBtn');await page.fill('#benchChaseRpm','40');check('改变追送转速撤销旧到瓶观察',await page.locator('#benchGroupBtn').isDisabled());await page.fill('#benchChaseRpm','30');await go('benchGroupBtn');s=await state();check('一次点击完成5剂并记录累计6剂',s.bench.last_run.completed_doses===5&&s.bench.sample_counts['1']===6&&!s.bench.air_ready);
 check('组结束后要求观察而非假设空气复位',await page.locator('#benchGroupBtn').isDisabled());
 for(let i=1;i<=3;i++)await page.fill('#benchBatch'+i,String([.9,.92,.91][i-1]));
 await go('benchBatchReportBtn');check('0.01g按0.15mL目标计算建议37步',(await page.locator('#benchBatchReport').innerText()).includes('0.182')&&(await page.locator('#benchBatchReport').innerText()).includes('37')&&(await page.locator('#benchBatchReport').innerText()).includes('单剂重复性：未验证'));
 check('重新测量保存会更新页面参考',(await page.locator('#benchSavedDose').innerText()).includes('0.182'));const before=(await state()).logs.filter(x=>x.event==='pulse_completed').length;await page.locator('#benchApplyBtn').click();await page.waitForTimeout(200);s=await state();check('采用建议只改输入不启动泵',await page.locator('#benchAspirateSteps').inputValue()==='37'&&s.logs.filter(x=>x.event==='pulse_completed').length===before&&await page.locator('#benchBatch1').inputValue()==='');
 await go('benchAfterDoseAirSeenBtn');await page.fill('#benchChaseSteps','350');check('更改追送后禁用成组直到重新观察',await page.locator('#benchGroupBtn').isDisabled());
 await page.fill('#benchBatch1','1.0');await page.fill('#benchAspirateSteps','50');check('改参数保留原始读数并阻止混用',await page.locator('#benchBatch1').inputValue()==='1.0'&&await page.locator('#benchBatchReportBtn').isDisabled());
 await page.locator('#benchWizard').evaluate(e=>{const p=document.createElement('p');p.className='note';p.textContent='软件隔离测试预览：页面读数为合成测试数据，不是实验结果。';e.prepend(p)});
 await page.locator('#benchWizard').screenshot({path:OUT+'/UI清水称重向导.png'});
 await page.setViewportSize({width:390,height:844});check('新向导手机无水平溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 check('新向导无JavaScript错误',errors.length===0);
 await api('/api/disconnect',{});
 fs.writeFileSync(OUT+'/program/BENCH_BROWSER_TEST_REPORT.txt',new Date().toISOString()+'\n'+checks.map((v,i)=>`${i+1}. PASS ${v}`).join('\n')+'\n隔离测试替身和合成称重数据，无COM6硬件动作，不是实测校准。\n');
 console.log(JSON.stringify({checks:checks.length,errors}));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
