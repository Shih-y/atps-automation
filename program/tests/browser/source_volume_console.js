// Isolated HTTP fixture only: never calls a live controller or hardware.
'use strict';
const http=require('http'),fs=require('fs'),path=require('path'),assert=require('assert');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..'),calls=[],errors=[],checks=[];
let server,browser,stale=false,raceReturnedId=null;
const refs=[];
const hardware={connected:true,busy:false,state:'CONNECTED',config:JSON.parse(fs.readFileSync(path.join(root,'config.default.json'))),logs:[],samples:{},sources:{},imaging:{},source_volume_calibrations:refs,water_dose_reference:{mean_per_dose_ml:.15,parameters:{sample:1,aspirate_steps:45,aspirate_rpm:5,chase_steps:2000,chase_rpm:30}}};
const lab={stocks:[{port:13,version:1,batch:'A',temperature_c:25}],samples:[],projects:[],rounds:[],bottles:[],photo_jobs:[],camera:{mapping_version:1}};
const reply=(r,x,status=200)=>{r.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});r.end(JSON.stringify(x));};
const last=u=>calls.filter(c=>c.url===u).at(-1)?.body;
const check=(n,v)=>{assert(v,n);checks.push(n);console.log('PASS '+n);};
async function until(fn){for(let i=0;i<80;i++){if(await fn())return;await new Promise(r=>setTimeout(r,50));}throw Error('Timed out');}
(async()=>{
 server=http.createServer(async(req,res)=>{
  let raw='';for await(const c of req)raw+=c;const body=raw?JSON.parse(raw):{};if(req.method==='POST')calls.push({url:req.url,body});
  if(req.url==='/api/token')return reply(res,{token:'isolated-test'});
  if(req.url==='/api/state')return reply(res,hardware);
  if(req.url==='/api/lab/state')return reply(res,lab);
  if(req.url==='/api/serial-ports')return reply(res,{ports:[],ch340_ports:[]});
  if(req.url==='/api/source-volume-calibration'){
   const r={...body,id:'ref-'+(refs.length+1),version:refs.length+1,groups_ml:body.groups_ml||[1],stock_batch:'batch-A',stock_version:2,mean_per_dose_ml:.2,ml_per_step:.2/45,status:'trial',range_basis:'single_dose_proportional_estimate',volume_verified:false,repeatability_verified:false};refs.push(r);return reply(res,{ok:true,reference:r});
  }
  if(req.url==='/api/source-volume-preview'){
   if(stale)return reply(res,{error:'来源批次已改变；标定不适用，须重新标定'},400);
   return reply(res,{ok:true,plan:{source:body.source,sample:body.sample,requested_ml:body.volume_ml,estimated_ml:body.volume_ml,total_steps:90,chunks:[45,45],parameters:[{aspirate:{steps:45,rpm:5},chase:{steps:2000,rpm:30}},{aspirate:{steps:45,rpm:5},chase:{steps:2000,rpm:30}}],reference:{id:'ref-1'},calibration_basis:'source_endpoint_trial',uncertainty_note:'单剂端点实测；较大剂量仅按比例估计，实际体积待测'}});
  }
  if(req.url==='/api/action'){
   if(body.action==='stock_calibration'){
    const number=calls.filter(c=>c.url==='/api/action').length;
    const next={id:'collection-'+number,method:'sample_endpoint_collection',completed:true};
    if(number===2){
     // The asynchronous action response still contains completed collection A.
     // Only the subsequent hardware-state read publishes new collection B.
     raceReturnedId=hardware.recipe_run.id;
     reply(res,{ok:true,state:JSON.parse(JSON.stringify(hardware))});
     hardware.recipe_run=next;return;
    }
    hardware.recipe_run=next;
   }
   return reply(res,{ok:true,state:hardware});
  }
  if(req.url.startsWith('/api/'))return reply(res,{ok:true});
  const file=path.join(root,'static',req.url==='/'?'index.html':req.url.slice(1));if(req.url.includes('..')||!fs.existsSync(file))return reply(res,{error:'missing'},404);
  res.writeHead(200,{'Content-Type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});res.end(fs.readFileSync(file));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 browser=await chromium.launch({headless:true,executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(5000);page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForSelector('#source-volume-form');
 check('load makes no motion',!calls.some(c=>c.url==='/api/action'));
 const f=page.locator('#source-volume-form'),fill=(n,v)=>f.locator('[name='+n+']').fill(v);
 await f.locator('[name=source]').selectOption('13');await f.locator('[name=sample]').selectOption('6');await fill('doses','5');
 await page.locator('#source-volume-collect').click();await until(()=>last('/api/action'));
 check('collection explicitly carries source bottle doses and finite pulses',last('/api/action').action==='stock_calibration'&&last('/api/action').source===13&&last('/api/action').sample===6&&last('/api/action').doses===5&&last('/api/action').aspirate.steps===45&&last('/api/action').aspirate.timeout_s>0&&last('/api/action').chase.steps===2000);
 const count=calls.filter(c=>c.url==='/api/action').length;
 await fill('aspirate_steps','46');await fill('groups','1');await f.locator('button[type=submit]').click();await until(async()=>(await page.locator('#lab-message').innerText()).includes('参数'));
 check('changing collection settings blocks saving old readings',!last('/api/source-volume-calibration'));
 await fill('aspirate_steps','45');await fill('groups','');
 // Explicitly clear the old reading before collecting again.
 await page.locator('#source-volume-collect').click();await until(()=>calls.filter(c=>c.url==='/api/action').length===count+1);
 await fill('groups','1,1.02,0.98');await f.locator('button[type=submit]').click();await until(()=>last('/api/source-volume-calibration'));
 check('second collection ignores previous completed response A and binds saved readings to B',raceReturnedId==='collection-1'&&hardware.recipe_run.id==='collection-2'&&last('/api/source-volume-calibration').collection_run_id==='collection-2');
 check('save preserves raw group total volumes without motion',last('/api/source-volume-calibration').collection_run_id===hardware.recipe_run.id&&JSON.stringify(last('/api/source-volume-calibration').groups_ml)==='[1,1.02,0.98]'&&last('/api/source-volume-calibration').doses===5&&calls.filter(c=>c.url==='/api/action').length===count+1);
 await until(async()=>(await page.locator('#source-volume-records').innerText()).includes('1.02'));
 check('readings and uncertainty remain visible',(await page.locator('#source-volume-records').innerText()).includes('单剂重复性未验证'));
 await fill('groups','');await f.locator('[name=reading_unit]').selectOption('g');await fill('groups','1,1.02,0.98');await f.locator('button[type=submit]').click();await until(async()=>(await page.locator('#lab-message').innerText()).includes('密度'));
 check('grams require explicitly entered density',calls.filter(c=>c.url==='/api/source-volume-calibration').length===1);
 await fill('groups','');await fill('density_g_ml','1.2');await fill('density_evidence','本批次实测密度');await fill('groups','1,1.02,0.98');await f.locator('[name=range_mode]').selectOption('proportional');await fill('min_steps','1');await fill('max_steps','100');await f.locator('button[type=submit]').click();await until(()=>calls.filter(c=>c.url==='/api/source-volume-calibration').length===2);
 check('proportional range requires explicit selection',last('/api/source-volume-calibration').min_steps===1&&last('/api/source-volume-calibration').max_steps===100);
 check('grams submit raw mass with explicit density evidence',last('/api/source-volume-calibration').density_g_ml===1.2&&last('/api/source-volume-calibration').density_evidence==='本批次实测密度'&&last('/api/source-volume-calibration').groups_g[1]===1.02&&!last('/api/source-volume-calibration').groups_ml);
 await fill('groups','');await fill('groups','1,1.02,0.98');
 const beforeStockChange=calls.filter(c=>c.url==='/api/source-volume-calibration').length;
 lab.stocks[0]={...lab.stocks[0],version:2,batch:'B'};
 await page.evaluate(()=>window.ATPSLab.refresh());
 await f.locator('button[type=submit]').click();await page.waitForTimeout(100);
 check('manually entered old readings cannot be saved after source stock version changes',calls.filter(c=>c.url==='/api/source-volume-calibration').length===beforeStockChange&&(await page.locator('#lab-message').innerText()).includes('母液'));
 await fill('groups','');
 await fill('aspirate_rpm','Infinity');await page.locator('#source-volume-collect').click();await page.waitForTimeout(100);
 check('nonfinite motion cannot be submitted',calls.filter(c=>c.url==='/api/action').length===count+1);await fill('aspirate_rpm','5');
 const p=page.locator('#source-volume-preview-form');await p.locator('[name=source]').selectOption('13');await p.locator('[name=sample]').selectOption('6');await p.locator('[name=volume_ml]').fill('.4');await p.locator('button[type=submit]').click();await until(()=>last('/api/source-volume-preview'));
 await until(async()=>(await page.locator('#source-volume-plan').innerText()).includes('比例'));
 check('preview displays measured basis versus proportional estimate without motion',calls.filter(c=>c.url==='/api/action').length===count+1&&(await page.locator('#source-volume-plan').innerText()).includes('90')&&(await page.locator('#source-volume-plan').innerText()).includes('第2段：吸取 45步 / 5 rpm，追送 2000步 / 30 rpm')&&(await page.locator('#source-volume-plan').innerText()).includes('ref-1'));
 await page.locator('#source-volume-run').click();await until(()=>last('/api/action').action==='volume');
 check('manual single volume uses backend planner',JSON.stringify(last('/api/action'))===JSON.stringify({action:'volume',source:13,sample:6,volume_ml:.4}));
 stale=true;await p.locator('button[type=submit]').click();await until(async()=>(await page.locator('#source-volume-plan').innerText()).includes('批次已改变'));
 check('inapplicable calibration shows server reason and blocks single run',await page.locator('#source-volume-run').isDisabled());
 await page.fill('#volume13','.4');await page.locator('#recipePreviewBtn').click();await until(async()=>(await page.locator('#recipeCalibrationPreview').innerText()).includes('批次已改变'));
 check('recipe preview reports calibration applicability without motion',last('/api/action').action==='volume');
 check('formal g per step calibration and legacy water controls retained',await page.locator('#mass-calibration-form').count()===1&&await page.locator('#calSingle').count()===1);
 await page.setViewportSize({width:390,height:844});check('mobile layout fits',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 check('no browser errors',errors.length===0);
 const oldPage=await browser.newPage(),oldErrors=[];
 oldPage.on('pageerror',e=>oldErrors.push(e.message));
 await oldPage.route('**/lab-source-volume.js',route=>route.fulfill({status:200,contentType:'application/javascript',body:''}));
 await oldPage.goto('http://127.0.0.1:'+server.address().port);
 await oldPage.waitForSelector('nav button');
 check('old running server can retain six pages and stop when new module is unavailable',oldErrors.length===0&&await oldPage.locator('#source-volume-unavailable').count()===1&&await oldPage.locator('#lab-stop').count()===1);
 await oldPage.close();
 console.log(JSON.stringify({passed:checks.length,errors,fixture:'random owned port; fake HTTP only'}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(server)await new Promise(r=>server.close(r));});
