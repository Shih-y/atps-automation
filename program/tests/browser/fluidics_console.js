// Real HTTP/controller + isolated simulation workspace; never opens a serial port.
'use strict';
const {spawn}=require('child_process'),path=require('path'),fs=require('fs'),assert=require('assert');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const program=path.resolve(__dirname,'../..'),errors=[],out=path.join(program,'test_output/fluidics_redesign');
let fixture,browser;
const check=(name,value)=>{assert(value,name);console.log('PASS '+name);};
async function until(fn){for(let i=0;i<100;i++){if(await fn())return;await new Promise(r=>setTimeout(r,70));}throw Error('Condition timed out');}
(async()=>{
 fixture=spawn(process.env.PYTHON||'python',['-u',path.join(__dirname,'fixture_server.py')],{cwd:program,windowsHide:true,stdio:['ignore','pipe','pipe']});
 await new Promise((resolve,reject)=>{let output='',error='';const timer=setTimeout(()=>reject(Error(error||'Fixture timeout')),15000);fixture.stdout.on('data',x=>{output+=x;if(output.includes('TEST ONLY injected hardware on 127.0.0.1:8879')){clearTimeout(timer);resolve();}});fixture.stderr.on('data',x=>error+=x);fixture.on('error',reject);fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code+': '+error));});});
 browser=await chromium.launch({headless:true,executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const page=await browser.newPage({viewport:{width:1500,height:1080}});page.setDefaultTimeout(4500);page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8879/simulation/#fluidics');
 await page.waitForSelector('#fluidics-diagram');
 const state=()=>page.evaluate(async()=>await(await fetch('/simulation/api/state')).json());
 await until(()=>page.locator('#fm-run').isEnabled());
 check('mode selector belongs to equipment, not status/header',await page.locator('#page-equipment #modeLink').count()===1 && await page.locator('header #activeMode').count()===0);
 check('status bar contains no simulated mode',!/(模拟|虚拟|实机)/.test(await page.locator('#lab-status-text').innerText()));
 check('five mapped bottles use actual valve ports',JSON.stringify(await page.locator('[data-fm-bottle]').evaluateAll(ns=>ns.map(n=>n.dataset.port)))===JSON.stringify(['1','3','4','6','7']));
 check('sixteen physical ports, common C separately',await page.locator('[data-fm-valve-port]').count()===16 && await page.locator('#fm-common-label').textContent()==='C');
 await page.locator('#fm-port').selectOption('13');await page.locator('#fm-select').click();
 await until(async()=>(await state()).hardware_status.valve_status==='switching');
 await until(async()=>(await page.locator('#fm-valve-state').innerText()).includes('目标 13'));
 check('target is not confirmed during switching',(await page.locator('#fm-valve-state').innerText()).includes('确认 —'));
 await until(async()=>(await state()).hardware_status.valve_port===13 && !(await state()).busy);
 await until(()=>page.locator('#fm-run').isEnabled());
 await page.locator('#fm-rpm').fill('60');await page.locator('#fm-turns').fill('2');await page.locator('#fm-direction').selectOption('cw');
 await page.locator('#fm-run').click();
 await until(async()=>(await state()).hardware_status.pump_status==='running');
 await page.waitForTimeout(1100);
 check('two turns at sixty rpm still running after 1.1 seconds',(await state()).hardware_status.pump_status==='running');
 check('animation follows confirmed motion',await page.locator('#fluidics-diagram').getAttribute('data-flow')==='dispense');
 check('manual controls locked while busy',await page.locator('#fm-run').isDisabled() && await page.locator('#fm-select').isDisabled());
 await until(async()=>!(await state()).busy);await until(async()=>await page.locator('#fluidics-diagram').getAttribute('data-flow')==='');
 await until(()=>page.locator('#fm-run').isEnabled());await page.locator('#fm-turns').fill('30');await page.locator('#fm-run').click();
 await until(async()=>(await state()).hardware_status.pump_status==='running');await page.locator('#fm-stop').click();
 await until(async()=>!(await state()).busy);check('priority stop preserves connection',(await state()).connected && (await state()).stop_status==='confirmed');
 await until(async()=>(await page.locator('#fm-event-list').innerText()).includes('已确认停止'));
 await until(async()=>await page.locator('#fluidics-diagram').getAttribute('data-flow')==='');
 await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(out,'fluidics-wide.png'),fullPage:true});
 await page.setViewportSize({width:820,height:1000});await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(out,'fluidics-narrow.png'),fullPage:true});
 check('narrow viewport has no page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 const formal=await page.evaluate(()=>{
   ATPSLab.state.bottles=[{bottle:2,sample_id:'formal-2'}];
   ATPSLab.state.samples=[{id:'formal-2',bottle:2,status:'settling',execution_state:'settling',frames:[],label:{classification:'single'},exclusions:[{code:'old_problem',reason:'已解除旧原因',active:false}],
     recipe:{plan:{sample:2,items:[{source:14,requested_mass_g:.25}],total_commands:4,completed_commands:0},estimated:{peg_fraction:.1,salt_fraction:.05}},
     composition:{basis:'estimated',peg_fraction:.1,salt_fraction:.05},progress:{sample:2,items:[{source:14,requested_mass_g:.25}],total_commands:4,completed_commands:3}}];
   document.querySelectorAll('.fm-bottle-card')[1].click();
   return document.querySelector('#fm-bottle-detail').textContent;
 });
 check('formal noncurrent bottle uses its own frozen recipe, progress and estimated composition',formal.includes('0.250 g')&&formal.includes('3/4')&&formal.includes('10.00%')&&formal.includes('5.00%')&&formal.includes('估计'));
 check('reviewed single label and corrected exclusions remain accurate',formal.includes('单相')&&!formal.includes('已解除旧原因'));
 const replaced=await page.evaluate(()=>{
   ATPSLab.state.bottles=[{bottle:1,sample_id:null}];ATPSLab.state.samples=[];
   ATPSLab.state.bottle_history=[{bottle:1,confirmed_clean_empty:true,at:new Date().toISOString()}];
   window.dispatchEvent(new CustomEvent('atps:hardware-state',{detail:{available:true,state:{connected:true,busy:false,config:{sample_port_map:{1:1,2:3,3:4,4:6,5:7}},
     recipe_run:{sample:1,started_at:'2020-01-01T00:00:00Z',completed:true,items:[{source:14,requested_ml:9}],completed_commands:4,total_commands:4},
     samples:{1:{last_gas_stop:'2020-01-01T00:00:01Z'}}}}}));
   document.querySelectorAll('.fm-bottle-card')[0].click();
   return document.querySelector('.fm-bottle-cards').textContent+document.querySelector('#fm-bottle-detail').textContent;
 });
 check('replacing a bottle does not reuse historical recipe or old settling clock',!replaced.includes('9.000')&&!replaced.includes('动作完成')&&!replaced.includes('停气'));
 await page.route('**/simulation/api/state',route=>route.abort());await until(async()=>(await page.locator('#fm-pump-state').innerText()).includes('未知'));
 check('lost telemetry stops animation',await page.locator('#fluidics-diagram').getAttribute('data-flow')==='');
 check('real controller never connected',!(await page.evaluate(async()=>await(await fetch('/api/state')).json())).connected);
 check('no frontend runtime errors',errors.length===0);console.log('FLUIDICS BROWSER PASS');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(fixture){fixture.kill();await new Promise(r=>fixture.once('exit',r));}});
