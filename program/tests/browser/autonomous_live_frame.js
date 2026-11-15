// Full browser -> PNG/optical algorithm -> real API -> fake-motion two-round loop.
// Never opens a real camera or serial port. Uses only the isolated 8879 fixture.
const {spawn}=require('child_process');
const path=require('path'),assert=require('assert');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const program=path.resolve(__dirname,'../..');
let fixture,browser;
async function until(fn){for(let n=0;n<250;n++){if(await fn())return;await new Promise(r=>setTimeout(r,100));}throw Error('autonomous fixture timed out');}
(async()=>{
 fixture=spawn(process.env.PYTHON||'python',['-u',path.join(__dirname,'fixture_server.py')],{cwd:program,windowsHide:true,stdio:['ignore','pipe','pipe']});
 await new Promise((resolve,reject)=>{let errors='';const timer=setTimeout(()=>reject(Error(errors||'fixture timeout')),15000);fixture.stderr.on('data',d=>errors+=d);fixture.stdout.on('data',d=>{if(String(d).includes('TEST ONLY')){clearTimeout(timer);resolve();}});fixture.on('error',reject);});
 browser=await chromium.launch({executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8879');
 await page.evaluate(()=>{
   const canvas=document.createElement('canvas');canvas.width=600;canvas.height=300;
   const x=canvas.getContext('2d',{willReadFrequently:true});x.fillStyle='rgb(210,210,210)';x.fillRect(0,0,600,300);
   const rois=[];
   for(let i=0;i<5;i++){
     const left=35+110*i;x.fillStyle='rgb(65,65,65)';x.fillRect(left,30,60,240);
     x.fillStyle='rgb(155,175,190)';x.fillRect(left+3,34,54,232);
     x.fillStyle='rgb(115,145,155)';x.fillRect(left+3,160,54,106);
     rois.push({sample:5-i,x:left,y:30,width:60,height:240});
   }
   window.syntheticCaptureCount=0;
   window.ATPSCamera={status:()=>({ready:true,width:600,height:300,rois}),capture:()=>{
     window.syntheticCaptureCount++;
     return {png_base64:canvas.toDataURL('image/png').split(',')[1],captured_at:new Date().toISOString(),
       analysis:PhaseAnalysis.analyzeFrame(x.getImageData(0,0,600,300),{rois})};
   }};
 });
 const post=(op,body)=>page.evaluate(async({op,body})=>{
   const {token}=await(await fetch('/api/token')).json();const r=await fetch(op,{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':token},body:JSON.stringify(body)});
   const v=await r.json();if(!r.ok)throw Error(v.error);return v;
 },{op,body});
 const state=()=>page.evaluate(async()=>await(await fetch('/api/lab/state')).json());
 await until(async()=>(await state()).camera.ready);
 for(const source of [13,14,15])await post('/api/lab/calibration',{source,groups_g:[.75,.75,.75],doses_per_group:5,
   aspirate_steps:45,aspirate_rpm:5,chase_steps:1000,chase_rpm:15,status:'validated',min_steps:1,max_steps:45,
   sample_bottles:[1],validation_evidence:'SYNTHETIC TEST ONLY',bottle_scope_evidence:'SYNTHETIC TEST ONLY'});
 const p=(await post('/api/lab/project',{name:'Automatic synthetic two rounds',salt_port:13,total_mass_g:.15,
   safe_mass_limit_g:1,photo_offsets_s:[0,1.1],min_settle_s:0,max_observe_s:30,composition_tolerance_fraction:.005})).project;
 await post('/api/lab/release',{bottle:1,confirmed_empty:true});
 const run=(await post('/api/lab/continuation_run',{project_id:p.id,bottles:[1],peg_min:.04,peg_max:.1,
   salt_min:.04,salt_max:.1,step:.02,round_budget:2,added_mass_budget_g:2,time_budget_s:600,reserve_mass_g:.1,
   observation_policy:{mode:'automatic',max_auto_retries:2,retry_interval_s:1,stable_frames:2,min_frame_spacing_s:1},request_id:'auto-live-create'})).run;
 await post('/api/connect',{});
 // Explicit test setup: establish the existing air-purge workflow. Startup alone
 // must not certify the common path. This runs only the fixture hardware double.
 await post('/api/action',{action:'purge'});
 await until(async()=>await page.evaluate(async()=>!(await(await fetch('/api/state')).json()).busy));
 await post('/api/lab/continuation_start',{run_id:run.id,request_id:'auto-live-start'});
 await until(async()=>{const s=await state();return s.samples.length===2&&s.samples.every(v=>v.label?.source==='automatic_optical');});
 const s=await state(),a=s.samples[0],b=s.samples[1];
 assert.equal(b.parent_sample_id,a.id);assert.equal(a.label.classification,'two_phase');assert.equal(b.label.classification,'two_phase');
 assert(a.training_eligibility.eligible);assert(b.training_eligibility.eligible);
 assert(a.frames.length>=2&&b.frames.length>=2);assert(a.label.evidence_frame_ids.every(id=>a.frames.some(f=>f.id===id)));
 assert(b.inventory_after.total_mass_g>a.inventory_after.total_mass_g);assert.equal(b.is_independent_repeat,false);
 assert.equal(s.continuation_runs[0].used.rounds,2);assert.equal(s.continuation_runs[0].status,'budget_complete');
 const before=s.samples.length;await post('/api/lab/continuation_start',{run_id:run.id,request_id:'auto-live-start'});
 assert.equal((await state()).samples.length,before);assert.equal(errors.length,0,errors.join('\n'));
 await post('/api/stop',{});
 console.log('PASS autonomous two-round loop: actual PNG and optical algorithm, stable evidence, no human label, cumulative mass, budget stop, idempotent start; fake hardware only');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(fixture&&!fixture.killed)fixture.kill();});
