// Six-page UI acceptance against isolated HTTP fixtures. No physical camera or serial.
'use strict';
const http=require('http'),fs=require('fs'),path=require('path'),assert=require('assert');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..'),calls=[],errors=[];
const hardware={connected:false,busy:false,state:'DISCONNECTED',config:JSON.parse(fs.readFileSync(path.join(root,'config.default.json'))),logs:[],samples:{},sources:{},imaging:{},water_dose_reference:{mean_per_dose_ml:.15,parameters:{sample:1,aspirate_steps:45,aspirate_rpm:5,chase_steps:1000,chase_rpm:15}}};
const state={stocks:[],calibrations:[],compatible_calibrations:{},samples:[],projects:[],rounds:[],bottles:[],photo_jobs:[],camera:{mapping_version:1},running:false};
function reply(res,data,status=200){res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));}
const sample=(id,project,bottle)=>({id,project_id:project,bottle,execution_state:'queued',status:'queued',target:{peg_fraction:.05,salt_fraction:.05},composition:{basis:'estimated',peg_fraction:.04,salt_fraction:.04,valid:true},review_state:'unreviewed',training_eligibility:{eligible:false,reasons:['未复核']},frames:[],generation:0,exclusions:[]});
let server,browser,failNextCapture=false,loseEnqueueResponse=true,loseApprovalResponse=true;
const acceptedEnqueues=new Set();
async function until(fn){for(let i=0;i<100;i++){if(await fn())return;await new Promise(r=>setTimeout(r,50));}throw Error('Timed out');}
const check=(name,value)=>{assert(value,name);console.log('PASS '+name);};
(async()=>{
  server=http.createServer(async(req,res)=>{
    if(req.url.startsWith('/api/')){
      let raw='';for await(const chunk of req)raw+=chunk;const body=raw?JSON.parse(raw):{};if(req.method==='POST')calls.push({url:req.url,body});
      if(req.url==='/api/token')return reply(res,{token:'test'});
      if(req.url==='/api/state')return reply(res,hardware);
      if(req.url==='/api/serial-ports')return reply(res,{ports:[],ch340_ports:[]});
      if(req.url==='/api/stop'){state.stop_status='confirmed';return reply(res,{ok:true,state:hardware});}
      if(req.url==='/api/lab/state')return reply(res,state);
      const op=req.url.split('/').pop();let out={ok:true};
      if(op==='stock')state.stocks.push(body);
      if(op==='calibration')state.calibrations.push(body);
      if(op==='project'){out.project={...body,id:'project-'+(state.projects.length+1)};state.projects.push(out.project);}
      if(op==='preview')out.preview={executable:true,issues:[],target:body,estimated:{peg_fraction:.04,salt_fraction:.04,total_mass_g:1},plan:{items:[]}};
      if(op==='enqueue'){
        if(!acceptedEnqueues.has(body.request_id)){state.samples.push(sample('sample-'+(state.samples.length+1),body.project_id,state.samples.length+1));acceptedEnqueues.add(body.request_id);}
        if(loseEnqueueResponse){loseEnqueueResponse=false;return reply(res,{error:'Saved successfully; response lost in fixture'},502);}
      }
      if(op==='approve'&&loseApprovalResponse){loseApprovalResponse=false;return reply(res,{error:'Approval response lost in fixture'},502);}
      if(op==='start'){state.running=true;state.active_project_id=body.project_id;state.samples.filter(s=>s.project_id===body.project_id).forEach(s=>s.execution_state='settling');}
      if(op==='camera')out.mapping_version=1;
      if(op==='capture'){
        if(failNextCapture){failNextCapture=false;return reply(res,{error:'injected image write error'},500);}
        const job=state.photo_jobs.find(j=>j.id===body.job_id);job.status='captured';
      }
      if(op==='photo_failure'){state.photo_jobs.find(j=>j.id===body.job_id).status='failed';}
      if(op==='review'){const s=state.samples.find(s=>s.id===body.sample_id);s.review_state=body.classification;s.training_eligibility={eligible:true,reasons:[]};}
      if(op==='suggest'){out.round={id:'round-'+(state.rounds.length+1),project_id:body.project_id,model:{kind:'rbf',two_class_boundary:false},summary:{training_count:0,method:body.method},grid:[],suggestions:[{peg_fraction:.12,salt_fraction:.05,target:{peg_fraction:.12,salt_fraction:.05},composition:{basis:'estimated',peg_fraction:.118,salt_fraction:.049,valid:true},reason:'uniform_coverage'}]};state.rounds.push(out.round);}
      if(op==='backup')out.backup={id:'backup-fixture',created_at:'test',download_url:'/api/lab/export?project_id='+body.project_id};
      return reply(res,out);
    }
    const file=path.join(root,'static',req.url==='/'?'index.html':req.url.slice(1));fs.readFile(file,(e,buffer)=>{res.writeHead(e?404:200,{'Content-Type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});res.end(buffer);});
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({headless:true,executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(8000);page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForSelector('#lab-nav');
  const tab=name=>page.getByRole('button',{name,exact:true}).click();
  const submit=async(id,url)=>{const count=calls.filter(c=>c.url===url).length;await page.locator('#'+id+' button[type=submit]').click();await until(()=>calls.filter(c=>c.url===url).length>count);await until(async()=>await page.locator('#'+id+' button[type=submit]').isEnabled());};
  check('six pages; manual bottle 6 retained',await page.locator('#lab-nav button').count()===6&&await page.locator('#recipeSample option').count()===6);
  await tab('数据与报告');check('no selected project cannot export all data implicitly',await page.locator('#export-project').getAttribute('href')===null&&await page.locator('#export-project').getAttribute('aria-disabled')==='true');await tab('设备与校准');
  await submit('stock-form','/api/lab/stock');await page.locator('#mass-calibration-form [name=groups]').fill('.75,.76,.75');await submit('mass-calibration-form','/api/lab/calibration');
  await tab('实验设计');await page.locator('#project-form [name=safe_mass_limit_g]').fill('1.2');await submit('project-form','/api/lab/project');
  await submit('composition-form','/api/lab/preview');await page.locator('#enqueue-preview').click();await until(()=>state.samples.length===1);
  await until(async()=>await page.locator('#enqueue-preview').isEnabled());await page.locator('#enqueue-preview').click();await until(()=>calls.filter(c=>c.url==='/api/lab/enqueue').length===2);
  check('enqueue lost-response retry reuses request ID and creates one sample',calls.filter(c=>c.url==='/api/lab/enqueue')[0].body.request_id===calls.filter(c=>c.url==='/api/lab/enqueue')[1].body.request_id&&state.samples.length===1);
  check('composition preview shows target and integer-step deviation',await page.locator('#composition-preview').innerText().then(s=>s.includes('整数步估计')&&s.includes('偏差')));
  await page.locator('#project-form [name=name]').fill('Project B');await submit('project-form','/api/lab/project');await submit('composition-form','/api/lab/preview');await page.locator('#enqueue-preview').click();await until(()=>state.samples.length===2);
  await tab('运行监控');await page.locator('#start-current-project').click();await until(()=>state.running);
  check('start B explicitly scopes request and leaves A queued',calls.find(c=>c.url==='/api/lab/start').body.project_id==='project-2'&&state.samples[0].execution_state==='queued');
  check('scope preview shows project and bottle',await page.locator('#start-preview').innerText().then(t=>t.includes('Project B')&&t.includes('预约瓶位')));
  const frame={id:'frame-1',job_id:'old-job',sample_id:'sample-2',captured_at:'2026-01-01T00:10:00Z',settle_s:600,generation:0,lateness_s:0,rois:[],analysis:null,url:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6PAAAAABJRU5ErkJggg=='};
  state.samples[1].frames=[frame];await page.evaluate(()=>ATPSLab.refresh());await tab('图像判相与复核');
  await page.waitForSelector('#review-form [name=frame_id] option[value="frame-1"]',{state:'attached'});
  await page.locator('#review-form [name=classification]').selectOption('single');await page.locator('#review-form [name=clear]').check();await submit('review-form','/api/lab/review');
  check('late human review submits stored on-time frame without client expiry',calls.find(c=>c.url==='/api/lab/review').body.frame_id==='frame-1');
  state.photo_jobs=[{id:'photo-fail',sample_id:'sample-2',status:'due',due_at:0},{id:'photo-ok',sample_id:'sample-2',status:'due',due_at:0}];failNextCapture=true;
  await page.evaluate(async()=>{window.ATPSCamera={status:()=>({ready:true}),capture:()=>({png_base64:'synthetic',captured_at:new Date().toISOString()})};await ATPSLab.refresh();});
  check('failed capture recorded and next due task still saved',state.photo_jobs[0].status==='failed'&&state.photo_jobs[1].status==='captured');
  await page.evaluate(()=>{window.ATPSCamera={status:()=>({ready:false})};});
  await tab('相图与主动学习');await submit('suggest-form','/api/lab/suggest');await page.locator('#approve-form input').check();await submit('approve-form','/api/lab/approve');
  await submit('approve-form','/api/lab/approve');check('approval lost-response retry reuses request ID',calls.filter(c=>c.url==='/api/lab/approve')[0].body.request_id===calls.filter(c=>c.url==='/api/lab/approve')[1].body.request_id);
  const points=JSON.parse(await page.locator('#phase-diagram').getAttribute('data-points'));
  check('plot uses 4% adopted estimate instead of 5% target',points[0].peg_fraction===.04&&points[0].basis==='estimated');
  const candidatePoints=JSON.parse(await page.locator('#phase-diagram').getAttribute('data-candidates'));check('candidate plot uses adopted estimate and list keeps target plus deviation',candidatePoints[0].peg_fraction===.118&&await page.locator('.candidate-list').innerText().then(t=>t.includes('12.000%')&&t.includes('11.800%')&&t.includes('-0.200')));
  await page.locator('#lab-project-select').selectOption('project-1');check('changing project clears old candidate approval',await page.locator('#approve-form input').count()===0);
  await tab('数据与报告');check('current-project ZIP excludes other project by query',await page.locator('#export-project').getAttribute('href')==='/api/lab/export?project_id=project-1');
  await page.locator('#data-scope').selectOption('all');check('all-data export explicitly drops project scope',await page.locator('#export-project').getAttribute('href')==='/api/lab/export');
  const stops=calls.filter(c=>c.url==='/api/stop').length;await page.locator('#lab-stop').click();await until(()=>calls.filter(c=>c.url==='/api/stop').length===stops+1);check('bottom stop uses one priority device stop request',true);
  const out=path.join(__dirname,'test_output','experiment_console');fs.mkdirSync(out,{recursive:true});
  for(const [i,name] of ['设备与校准','实验设计','运行监控','图像判相与复核','相图与主动学习','数据与报告'].entries()){await tab(name);await page.screenshot({path:path.join(out,'page-'+(i+1)+'.png'),fullPage:true});}
  assert.deepEqual(errors,[]);console.log('PASS six-page regression completed without physical devices');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(server){server.closeAllConnections();await new Promise(r=>server.close(r));}});
