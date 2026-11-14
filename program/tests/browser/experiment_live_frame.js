// Real HTTP/controller, injected fake hardware and a canvas video stream only.
// No real camera API or serial port is opened.
const {spawn}=require('child_process');
const path=require('path'),assert=require('assert'),fs=require('fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const program=path.resolve(__dirname,'../..');
let fixture,browser;
async function waitFor(fn){for(let n=0;n<150;n++){if(await fn())return;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out');}
(async()=>{
  fixture=spawn(process.env.PYTHON||'python',['-u',path.join(__dirname,'fixture_server.py')],{cwd:program,windowsHide:true,stdio:['ignore','pipe','pipe']});
  await new Promise((resolve,reject)=>{let errors='';const timer=setTimeout(()=>reject(Error(errors||'Fixture timeout')),15000);fixture.stderr.on('data',d=>errors+=d);fixture.stdout.on('data',d=>{if(String(d).includes('TEST ONLY')){clearTimeout(timer);resolve();}});fixture.on('error',reject);});
  browser=await chromium.launch({executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage();page.setDefaultTimeout(10000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{
    window.syntheticCameraCalls=0;
    navigator.mediaDevices.enumerateDevices=async()=>[];
    navigator.mediaDevices.getUserMedia=async()=>{
      window.syntheticCameraCalls++;
      const canvas=document.createElement('canvas');canvas.width=1000;canvas.height=600;
      const ctx=canvas.getContext('2d');ctx.fillStyle='#ddd';ctx.fillRect(0,0,1000,600);
      // Entirely synthetic video, unrelated to the user's actual camera.
      window.syntheticStream=canvas.captureStream(10);window.syntheticCanvas=canvas;
      window.syntheticTicker=setInterval(()=>{ctx.fillStyle='#ddd';ctx.fillRect(0,0,1000,600);},100);
      return window.syntheticStream;
    };
  });
  await page.goto('http://127.0.0.1:8879');
  const post=(op,body)=>page.evaluate(async({op,body})=>{const {token}=await(await fetch('/api/token')).json();const r=await fetch(op,{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':token},body:JSON.stringify(body)});const value=await r.json();if(!r.ok)throw Error(value.error);return value;},{op,body});
  const state=()=>page.evaluate(async()=>await(await fetch('/api/lab/state')).json());
  await page.getByRole('button',{name:'图像判相与复核',exact:true}).click();
  await page.locator('#cameraOpen').click();await page.waitForFunction(()=>document.querySelector('#cameraVideo').readyState>=2);
  await page.locator('#freezeBtn').click();await page.locator('#roiPanel').evaluate(e=>e.open=true);await page.locator('#manualRoisBtn').click();
  await waitFor(async()=>(await state()).camera.ready);
  for(const source of [13,14,15])await post('/api/lab/calibration',{source,groups_g:[.75,.74,.76],doses_per_group:5,aspirate_steps:45,aspirate_rpm:5,chase_steps:1000,chase_rpm:15,status:'validated',min_steps:1,max_steps:45,sample_bottles:[1,2],validation_evidence:'Synthetic fixture validates prescribed range; not physical evidence',bottle_scope_evidence:'Synthetic test hardware bottles 1 and 2 only',note:'SYNTHETIC TEST ONLY'});
  await page.getByRole('button',{name:'实验设计',exact:true}).click();
  for(const [name,value] of Object.entries({name:'SYNTHETIC TEST ONLY',total_mass_g:'.15',safe_mass_limit_g:'1',photo_offsets_s:'0',min_settle_s:'0',max_observe_s:'60',composition_tolerance:'1'}))await page.locator('#project-form [name='+name+']').fill(value);
  await page.locator('#project-form button[type=submit]').click();await waitFor(async()=>(await state()).projects.length===1);
  const project=(await state()).projects[0];assert.equal(project.composition_tolerance_fraction,.01);
  for(const [name,value] of Object.entries({peg:'4',salt:'4',mass:'.15',replicates:'2'}))await page.locator('#composition-form [name='+name+']').fill(value);
  await page.locator('#composition-form button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#lab-message').textContent.includes('可加入队列'));
  await page.locator('#enqueue-preview').click();await waitFor(async()=>(await state()).samples.length===2);
  await post('/api/connect',{});await page.getByRole('button',{name:'运行监控',exact:true}).click();await page.locator('#start-current-project').click();
  await waitFor(async()=>(await state()).samples.every(s=>s.frames.length===1));
  const saved=await state();assert.equal(saved.samples.length,2);
  for(const sample of saved.samples){const f=sample.frames[0];assert.equal(f.sample_id,sample.id);assert.equal(f.bottle,sample.bottle);assert(f.settle_s>=0);assert.equal(f.analysis.validated,false);assert.equal(f.rois.length,5);assert(sample.progress.completed);}
  await page.getByRole('button',{name:'图像判相与复核',exact:true}).click();
  await page.evaluate(()=>ATPSLab.refresh());
  await page.locator('#review-form [name=classification]').selectOption('single');await page.locator('#review-form [name=clear]').check();
  await page.locator('#review-form button[type=submit]').click();await waitFor(async()=>(await state()).samples.some(s=>s.review_state==='single'));
  assert((await state()).samples.find(s=>s.review_state==='single').training_eligibility.eligible);
  await page.waitForFunction(()=>document.querySelector('#sample-gallery canvas')?.width>0);
  const screenshots=path.join(__dirname,'test_output','experiment_live_frame');fs.mkdirSync(screenshots,{recursive:true});
  await page.screenshot({path:path.join(screenshots,'review-roi.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>syntheticCameraCalls),1);assert.deepEqual(errors,[]);
  await post('/api/lab/pause',{});await post('/api/stop',{});
  console.log('PASS actual browser live-frame bridge -> real lab API -> two fake-hardware samples -> timed PNG/ROI archive; no real camera or serial');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(fixture&&!fixture.killed)fixture.kill();});
