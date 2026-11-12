// Real browser + isolated HTTP boundary; never opens a serial port or a camera.
const fs = require('fs'), path = require('path'), http = require('http'), assert = require('assert');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const program = path.resolve(__dirname, '../..');
const out = path.join(__dirname, 'test_output', 'current_console');
const requests = [], checks = [], errors = [];
let browser, server, holdAction = null, failCapture = false, holdBegin = null, batchNumber = 0;
const state = { connected:false, busy:false, state:'DISCONNECTED', free_debug:true, message:'',
  config:JSON.parse(fs.readFileSync(path.join(program,'config.default.json'),'utf8')), logs:[], samples:{}, sources:{}, imaging:{},
  water_dose_reference:{mean_per_dose_ml:.15,parameters:{sample:1,aspirate_steps:45,aspirate_rpm:5,chase_steps:1000,chase_rpm:15}} };
function reply(res,data,status=200){res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(data));}
function check(name,ok){assert(ok,name);checks.push(name);console.log('PASS '+name);}
async function until(fn){for(let n=0;n<60;n++){if(await fn())return;await new Promise(r=>setTimeout(r,50));}throw Error('Condition timed out');}
const last = endpoint => requests.filter(r=>r.url===endpoint).at(-1)?.body;
(async()=>{
 server=http.createServer(async(req,res)=>{
  const chunks=[];for await(const c of req)chunks.push(c);
  const body=chunks.length?JSON.parse(Buffer.concat(chunks).toString()):null;
  requests.push({url:req.url,method:req.method,body});
  if(req.url==='/api/token')return reply(res,{token:'isolated-browser-fixture'});
  if(req.url==='/api/state')return reply(res,state);
  if(req.url==='/api/serial-ports')return reply(res,{ports:[{device:'COM6',is_ch340:true,description:'TEST ONLY CH340'}],ch340_ports:[{device:'COM6',is_ch340:true}],suggested_port:'COM6'});
  if(req.url==='/api/connect'){state.connected=true;state.state='CONNECTED';state.config.communication.port=body.auto_detect?'COM9':body.pump_port;return reply(res,{ok:true,state});}
  if(req.url==='/api/disconnect'){state.connected=false;return reply(res,{ok:true,state});}
  if(req.url==='/api/action'){if(holdAction){holdAction.res=res;return;}return reply(res,{ok:true,state});}
  if(req.url==='/api/stop'){state.busy=false;return reply(res,{ok:true,state});}
  if(req.url==='/api/reference'){state.water_dose_reference={mean_per_dose_ml:body.mean_per_dose_ml,parameters:body};return reply(res,{ok:true,reference:state.water_dose_reference});}
  if(req.url==='/api/bench/report')return reply(res,{ok:true,report:{mean_per_dose_ml:.16,group_rsd_percent:1.25},saved_file:'fixture-report.json'});
  if(req.url==='/api/imaging/begin'){if(state.imaging.batch_id&&!state.imaging.complete&&!state.imaging.discarded)return reply(res,{error:'Previous batch pending'},400);state.imaging={batch_id:'fixture-batch-'+(++batchNumber),complete:false,targets:body.targets,frames:[],records:[]};if(holdBegin){holdBegin.res=res;return;}return reply(res,{ok:true,batch_id:state.imaging.batch_id,imaging:state.imaging});}
  if(req.url==='/api/capture'){if(failCapture){failCapture=false;return reply(res,{error:'Synthetic capture failure'},400);}state.imaging.complete=true;state.imaging.frames=[{captured_at:body.captured_at,url:'/api/images/fixture.png'}];state.imaging.records=body.rois.map(r=>({sample:r.sample,classification:'unreviewed'}));return reply(res,{ok:true,state});}
  if(req.url==='/api/discard'){state.imaging.discarded=true;return reply(res,{ok:true,state});}
  if(req.url==='/api/label')return reply(res,{ok:true,state});
  const file=path.join(program,'static',req.url==='/'?'index.html':req.url.slice(1));
  if(!req.url.includes('..')&&fs.existsSync(file)){res.writeHead(200,{'Content-Type':file.endsWith('.html')?'text/html; charset=utf-8':file.endsWith('.css')?'text/css':'text/javascript'});return res.end(fs.readFileSync(file));}
  reply(res,{error:'Unexpected fixture endpoint '+req.url},404);
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(8879,'127.0.0.1',resolve);});
 browser=await chromium.launch({executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.cameraRequests=0;navigator.mediaDevices.getUserMedia=async()=>{window.cameraRequests++;throw Error('Camera calls forbidden in tests');};});
 await page.goto('http://127.0.0.1:8879');await page.waitForTimeout(600);
 check('single bottle recipe is directly available on the default page',await page.locator('#recipeStart').count()===1);
 check('page load neither moves hardware nor opens camera nor creates capture task',!requests.some(r=>r.method==='POST')&&await page.evaluate(()=>window.cameraRequests)===0);
 check('no historical prerequisite locks on recipe/manual/cleaning buttons',await page.locator('#recipeStart').isEnabled()&&await page.locator('#manualRun').isEnabled()&&await page.locator('#washBtn').isEnabled());
 await page.locator('#recipeStart').click();await page.waitForTimeout(100);
 check('empty recipe is rejected without action request',!requests.some(r=>r.url==='/api/action')&&(await page.locator('#notice').innerText()).includes('体积'));
 await page.locator('#recipeSample').selectOption('6');await page.fill('#volume11','0.8');await page.fill('#volume13','2.0');await page.locator('#recipeStart').click();
 await until(()=>last('/api/action'));
 check('one recipe request carries all nonzero ingredients and bottle end mixing then purge',JSON.stringify(last('/api/action'))===JSON.stringify({action:'recipe',sample:6,items:[{source:11,volume_ml:.8},{source:13,volume_ml:2}],mix_after:true,purge_after:true,mixing:{steps:20,rpm:5,timeout_s:10}}));
 await page.locator('#mixingPanel summary').click();await page.fill('#mixSteps','40');await page.fill('#mixRpm','10');await page.fill('#mixTimeout','12');await page.locator('#recipeStart').click();await until(()=>last('/api/action')?.mixing?.steps===40);
 check('optional final mixing parameters are editable and submitted with the recipe',JSON.stringify(last('/api/action').mixing)==='{"steps":40,"rpm":10,"timeout_s":12}');
 await page.fill('#mixSteps','20');await page.fill('#mixRpm','5');await page.fill('#mixTimeout','10');await page.locator('#mixingPanel summary').click();
 await page.fill('#volume15','-1');const before=requests.filter(r=>r.url==='/api/action').length;await page.locator('#recipeStart').click();await page.waitForTimeout(100);
 check('invalid later ingredient prevents the whole request',requests.filter(r=>r.url==='/api/action').length===before);
 await page.fill('#volume15','0');await page.locator('#connectBtn').click();await until(()=>last('/api/connect'));
 check('automatic CH340 connection asks for fresh resolution with shared transport',last('/api/connect').auto_detect===true&&!last('/api/connect').pump_port);
 await until(async()=>(await page.locator('#connectionStatus').innerText()).includes('COM9'));
 check('connection shows actual returned port',true);
 await page.locator('#manualPort').selectOption('14');await page.locator('#manualRotation').selectOption('cw');await page.fill('#manualRpm','12');await page.fill('#manualTurns','2');await page.locator('#manualRun').click();await until(()=>last('/api/action')?.action==='manual_motion');
 check('manual direction rpm and integer turns sent independently',JSON.stringify(last('/api/action'))===JSON.stringify({action:'manual_motion',port:14,rotation:'cw',rpm:12,turns:2}));
 await page.fill('#manualTurns','1.5');const beforeBad=requests.filter(r=>r.url==='/api/action').length;await page.locator('#manualRun').click();await page.waitForTimeout(100);
 check('fractional turns fail before hardware request',requests.filter(r=>r.url==='/api/action').length===beforeBad);
 for(const [id,action] of [['washBtn','wash'],['purgeBtn','purge'],['washPurgeBtn','reset']]){await page.locator('#'+id).click();await until(()=>last('/api/action')?.action===action);}
 check('independent cleaning actions work without prior checks',true);
 await page.locator('#refillBtn').click();await until(()=>last('/api/action')?.operation==='refill');
 check('refill forwards only selected sources',JSON.stringify(last('/api/action').sources)==='[13,14,15]');
 await page.locator('#finishBtn').click();await until(()=>last('/api/action')?.operation==='finish');
 check('end drain forwards selected sample lines',JSON.stringify(last('/api/action').samples)==='[1]');
 await page.locator('#calibrationPanel summary').click();await page.locator('#calSingle').click();await until(()=>last('/api/action')?.count===1);
 check('optional single water dose supplies every pulse parameter',last('/api/action').operation==='dose_water'&&last('/api/action').aspirate_steps===45&&last('/api/action').chase_steps===1000&&last('/api/action').chase_rpm===15);
 await page.locator('#calFive').click();await until(()=>last('/api/action')?.count===5);
 await page.fill('#referenceMl','.16');await page.locator('#referenceSave').click();await until(()=>last('/api/reference'));
 check('measured single dose reference saves without hardware motion',last('/api/reference').mean_per_dose_ml===.16&&last('/api/reference').aspirate_steps===45);
 for(let i=1;i<=3;i++)await page.fill('#mass'+i,'.8');await page.locator('#calReport').click();await until(()=>last('/api/bench/report'));
 check('five-dose report preserves measured groups and pulse context',JSON.stringify(last('/api/bench/report').readings_g)==='[0.8,0.8,0.8]'&&last('/api/bench/report').doses_per_group===5);
 holdAction={};await page.locator('#washBtn').click();await until(()=>holdAction.res);
 const busyBefore=requests.filter(r=>r.url==='/api/action').length;await page.locator('#purgeBtn').click();await page.waitForTimeout(100);
 check('concurrent action visibly rejected and never queued',requests.filter(r=>r.url==='/api/action').length===busyBefore&&(await page.locator('#notice').innerText()).includes('忙'));
 await page.locator('#stopBtn').click();await until(()=>last('/api/stop'));
 check('stop bypasses an outstanding normal action',!!last('/api/stop'));
 reply(holdAction.res,{ok:true,state});holdAction=null;await page.waitForTimeout(150);
 state.hardware_status={pump_status:'stop_requested',pump_at:Date.now()/1000,valve_status:'unknown'};await page.waitForTimeout(900);
 check('requested stop never displayed as confirmed', (await page.locator('#pumpStatus').innerText()).includes('未确认'));
 const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=1000;c.height=600;const x=c.getContext('2d');x.fillStyle='#aaa';x.fillRect(0,0,1000,600);return c.toDataURL().split(',')[1];});
 await page.setInputFiles('#imageFile',{name:'synthetic-empty.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});await until(async()=>(await page.locator('#cameraStatus').innerText()).includes('本地'));
 await page.locator('#analyzeBtn').click();await page.waitForTimeout(150);
 check('empty synthetic image remains uncertain without invented bottles',(await page.locator('#analysisSummary').innerText()).includes('不确定')&&await page.locator('#analysisRows tr').count()===0);
 await page.locator('#roiPanel summary').click();await page.locator('#manualRoisBtn').click();await page.locator('#analyzeBtn').click();await page.waitForTimeout(150);
 check('manual ROI numbering is right to left',Number(await page.locator('[data-sample="1"][data-roi="x"]').inputValue())>Number(await page.locator('[data-sample="5"][data-roi="x"]').inputValue()));
 await page.locator('[data-sample="1"][data-roi="width"]').fill('1000');await page.locator('#analyzeBtn').click();
 check('invalid manual ROI remains editable instead of discarding operator input',await page.locator('[data-sample="1"][data-roi="width"]').count()===1&&await page.locator('[data-sample="1"][data-roi="width"]').inputValue()==='1000');
 await page.locator('#manualRoisBtn').click();await page.locator('#analyzeBtn').click();
 failCapture=true;await page.locator('#captureBtn').click();await until(async()=>(await page.locator('#notice').innerText()).includes('Synthetic capture failure'));
 const failedPayload=JSON.stringify(last('/api/capture'));
 await page.locator('#captureBtn').click();await until(async()=>(await page.locator('#archiveStatus').innerText()).includes('已保存'));
 check('failed archive retries the same immutable frame in its existing batch',requests.filter(r=>r.url==='/api/imaging/begin').length===1&&JSON.stringify(last('/api/capture'))===failedPayload);
 check('capture archives unmirrored raw image, exact ROI and separate unvalidated analysis',last('/api/capture').width===1000&&last('/api/capture').analysis.validated===false&&JSON.stringify(last('/api/capture').rois)===JSON.stringify(last('/api/capture').analysis.rois)&&last('/api/capture').alignment_confirmed===false);
 check('imported image never fabricates an acquisition timestamp',last('/api/capture').frame_origin==='import'&&last('/api/capture').captured_at===null&&!!last('/api/capture').loaded_at);
 check('offline imaging batch starts only on archive click',JSON.stringify(last('/api/imaging/begin').targets)==='[1,2,3,4,5]'&&await page.evaluate(()=>window.cameraRequests)===0);
 await page.locator('summary').filter({hasText:'人工复核'}).click();await page.locator('#manualClassification').selectOption('two_phase');await page.fill('#labelNote','人工复核测试记录');await page.locator('#labelSave').click();await until(()=>last('/api/label'));
 check('manual label is saved through separate API',last('/api/label').classification==='two_phase'&&last('/api/label').note==='人工复核测试记录');
 holdBegin={};await page.locator('#captureBtn').click();await until(()=>holdBegin.res);
 await page.setInputFiles('#imageFile',{name:'next-image.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});await until(async()=>(await page.locator('#cameraStatus').innerText()).includes('本地'));
 check('new frame clears the previous manual review confirmation',(await page.locator('#manualLabelStatus').innerText())==='');
 reply(holdBegin.res,{ok:true,batch_id:state.imaging.batch_id,imaging:state.imaging});holdBegin=null;await until(()=>state.imaging.complete);
 const expectedSaved=JSON.parse(failedPayload),currentSaved=last('/api/capture');
 check('in-flight archive preserves original pixels, ROI, analysis and timestamps',currentSaved.png_base64===expectedSaved.png_base64&&JSON.stringify(currentSaved.rois)===JSON.stringify(expectedSaved.rois)&&JSON.stringify(currentSaved.analysis)===JSON.stringify(expectedSaved.analysis)&&currentSaved.loaded_at===expectedSaved.loaded_at);
 check('changing the local frame during saving cannot attach old labels to the new frame',!(await page.locator('#archiveStatus').innerText()).includes('当前帧已保存'));
 const labelsBefore=requests.filter(r=>r.url==='/api/label').length;await page.locator('#labelSave').click();await page.waitForTimeout(100);
 check('a new unsaved frame cannot label the previously saved batch',requests.filter(r=>r.url==='/api/label').length===labelsBefore);
 state.imaging={batch_id:'preexisting-pending',complete:false,discarded:false,targets:[1,2,3,4,5]};await page.waitForTimeout(900);
 await page.locator('#captureBtn').click();await page.waitForTimeout(100);await page.locator('#discardCaptureBtn').click();await until(()=>last('/api/discard'));
 check('preexisting failed capture has explicit discard recovery',last('/api/discard').batch_id==='preexisting-pending');
 await page.setViewportSize({width:390,height:844});check('phone layout has no horizontal page overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 check('browser has no JavaScript errors',errors.length===0);
 fs.mkdirSync(out,{recursive:true});await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
 await page.reload();await page.waitForTimeout(300);await page.screenshot({path:path.join(out,'desktop-default.png'),fullPage:true});
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,errors,cameraCalls:await page.evaluate(()=>window.cameraRequests),fixture:'fake HTTP only; no serial or camera'},null,2));
 console.log(JSON.stringify({passed:checks.length,errors}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(holdAction?.res)holdAction.res.end();if(holdBegin?.res)holdBegin.res.end();if(server)await new Promise(resolve=>server.close(resolve));});
