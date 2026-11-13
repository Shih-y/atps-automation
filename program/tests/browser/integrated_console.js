// Full current UI -> production HTTP/controller -> injected test hardware only.
// Starts and owns fixture_server.py on 8879; never contacts a real instrument.
const {spawn}=require('child_process');
const fs=require('fs'),path=require('path'),assert=require('assert');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const program=path.resolve(__dirname,'../..'),out=path.join(__dirname,'test_output','integrated_console');
let fixture,browser;
const checks=[],errors=[];
const check=(name,value)=>{assert(value,name);checks.push(name);console.log('PASS '+name);};
async function until(fn){for(let i=0;i<100;i++){if(await fn())return;await new Promise(r=>setTimeout(r,100));}throw Error('Timed out waiting for actual backend');}
(async()=>{
  fixture=spawn(process.env.PYTHON||'python',['-u',path.join(__dirname,'fixture_server.py')],
    {cwd:program,windowsHide:true,stdio:['ignore','pipe','pipe']});
  await new Promise((resolve,reject)=>{
    let stdout='',stderr='';const timer=setTimeout(()=>reject(Error('Fixture timeout: '+stderr)),15000);
    fixture.stdout.on('data',x=>{stdout+=x;if(stdout.includes('TEST ONLY injected hardware on 127.0.0.1:8879')){clearTimeout(timer);resolve();}});
    fixture.stderr.on('data',x=>{stderr+=x;});
    fixture.once('error',e=>{clearTimeout(timer);reject(e);});
    fixture.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exited '+code+': '+stderr));});
  });
  browser=await chromium.launch({executablePath:process.env.EDGE_PATH||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.cameraCalls=0;navigator.mediaDevices.getUserMedia=async()=>{window.cameraCalls++;throw Error('Camera forbidden in software test');};});
  await page.goto('http://127.0.0.1:8879');
  const state=()=>page.evaluate(async()=>await(await fetch('/api/state')).json());
  await until(async()=>(await page.locator('#referenceSummary').innerText()).includes('0.15'));
  check('production server starts disconnected without requesting camera',!(await state()).connected&&await page.evaluate(()=>cameraCalls)===0);

  const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=1000;c.height=600;const x=c.getContext('2d');x.fillStyle='#aaa';x.fillRect(0,0,c.width,c.height);return c.toDataURL('image/png').split(',')[1];});
  await page.getByRole('button',{name:'图像判相与复核',exact:true}).click();await page.setInputFiles('#imageFile',{name:'synthetic.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
  await until(async()=>(await page.locator('#cameraStatus').innerText()).includes('本地'));
  await page.locator('#analyzeBtn').click();await page.locator('#captureBtn').click();
  await until(async()=>(await state()).imaging.complete);
  let s=await state(),f=s.imaging.frames.at(-1);
  check('offline import archives actual PNG with unknown acquisition time',!s.connected&&f.frame_origin==='import'&&f.captured_at===null&&!!f.loaded_at);
  check('empty image keeps all bottles unassigned',s.imaging.records.length===0&&f.unassigned_targets.length===5&&f.analysis.validated===false);
  await page.locator('#roiPanel summary').click();
  const bounds=await page.locator('#imageCanvas').boundingBox(),scale=Math.min(bounds.width/1000,bounds.height/600);
  const px=x=>bounds.x+(bounds.width-1000*scale)/2+x*scale,py=y=>bounds.y+(bounds.height-600*scale)/2+y*scale;
  await page.mouse.move(px(800),py(80));await page.mouse.down();await page.mouse.move(px(920),py(520));await page.mouse.up();
  await page.locator('#analyzeBtn').click();const emptyBatch=s.imaging.batch_id;await page.locator('#captureBtn').click();
  await until(async()=>{const x=(await state()).imaging;return x.complete&&x.batch_id!==emptyBatch;});
  s=await state();
  check('one explicitly drawn bottle archives without inventing the other four',s.imaging.records.length===1&&s.imaging.records[0].sample===1&&JSON.stringify(s.imaging.frames.at(-1).unassigned_targets)==='[2,3,4,5]');
  await page.locator('#manualRoisBtn').click();await page.locator('#analyzeBtn').click();
  const previous=s.imaging.batch_id;await page.locator('#captureBtn').click();
  await until(async()=>{const x=(await state()).imaging;return x.complete&&x.batch_id!==previous;});
  s=await state();
  check('manual ROI archive records unreviewed bottles and no fabricated settling time',s.imaging.records.length===5&&s.imaging.records.every(r=>r.classification==='unreviewed'&&r.elapsed_since_stop_s===null));
  await page.locator('summary').filter({hasText:'人工复核'}).click();await page.locator('#manualClassification').selectOption('two_phase');await page.fill('#labelNote','Isolated review test');await page.locator('#labelSave').click();
  await until(async()=>(await state()).imaging.records[0].classification==='two_phase');
  s=await state();
  check('human label does not overwrite algorithm candidate',s.imaging.records[0].classification_method==='human_review'&&s.imaging.frames.at(-1).analysis.bottles[0].classification==='uncertain');

  await page.getByRole('button',{name:'设备与校准',exact:true}).click();await page.locator('#connectBtn').click();await until(async()=>(await state()).connected);
  for(const [port,ml] of [[15,'.5'],[14,'1'],[13,'.3']])await page.fill('#volume'+port,ml);
  await page.locator('#recipeStart').click();await until(async()=>(await state()).recipe_run?.completed);
  s=await state();
  check('real controller sees four liquid segments then auto mixing and one end purge',s.recipe_run.total_plugs===4&&s.recipe_run.completed_commands===10&&s.recipe_run.mixing_performed&&s.recipe_run.purge_performed&&s.selected_port===16);
  await until(async()=>(await page.locator('#runStatus').innerText()).includes('待操作'));
  await page.locator('#calibrationPanel summary').click();await page.fill('#referenceMl','.16');await page.locator('#referenceSave').click();
  await until(async()=>(await state()).water_dose_reference.mean_per_dose_ml===.16);
  await page.reload();await until(async()=>(await page.locator('#referenceSummary').innerText()).includes('0.16'));
  check('UI reference survives reload with no invented repeatability',(await state()).water_dose_reference.individual_repeatability_verified===false);
  await page.fill('#volume15','.32');await page.locator('#recipeStart').click();
  await until(async()=>{const r=(await state()).recipe_run;return r?.completed&&r.requested_ml===.32;});
  check('next recipe uses updated endpoint reference',(await state()).recipe_run.items[0].total_steps===90);
  await page.locator('#stopBtn').click();await until(async()=>(await state()).state==='STOPPED');
  check('UI stop preserves actual fake shared connection',(await state()).connected===true);
  check('no browser errors or camera calls',errors.length===0&&await page.evaluate(()=>cameraCalls)===0);
  console.log(JSON.stringify({passed:checks.length,errors}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();
  if(fixture){fixture.kill();console.log('Stopped only owned fake fixture PID '+fixture.pid);}
  fs.mkdirSync(out,{recursive:true});fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,errors,test_only:true},null,2));
});
