// Smoke-test the actual packaged Windows executable with an isolated data dir.
// Camera input is a canvas stream; real serial connection is never requested.
const {spawn,execFileSync}=require('child_process');
const fs=require('fs'),path=require('path'),assert=require('assert'),net=require('net');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'../..');
const output=path.join(__dirname,'test_output','desktop');fs.mkdirSync(output,{recursive:true});
const data=fs.mkdtempSync(path.join(output,'data-'));
let child,browser;
let childOutput='';
const freePort=()=>new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
async function until(fn){for(let n=0;n<300;n++){try{const v=await fn();if(v)return v;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('desktop readiness timeout');}
async function launch(){
  const port=await freePort(),debug=await freePort();
  const runtimeTemp=process.env.ATPS_TEST_TEMP_ROOT
    ?fs.mkdtempSync(path.join(process.env.ATPS_TEST_TEMP_ROOT,'atps-desktop-'))
    :path.join(data,'runtime-temp');fs.mkdirSync(runtimeTemp,{recursive:true});
  child=spawn(process.env.ATPS_EXE||path.join(root,'dist','ATPS-Automation.exe'),['--port',String(port),'--debug-port',String(debug),'--data-dir',data],{windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,TEMP:runtimeTemp,TMP:runtimeTemp}});
  child.stdout.on('data',v=>childOutput+=v);child.stderr.on('data',v=>childOutput+=v);
  child.on('error',e=>{throw e;});
  const endpoint=await until(async()=>{const r=await fetch(`http://127.0.0.1:${debug}/json/version`);return(await r.json()).webSocketDebuggerUrl;});
  browser=await chromium.connectOverCDP(endpoint);
  const context=browser.contexts()[0];const page=await until(()=>context.pages().find(p=>p.url().includes(`:${port}`)));
  await page.waitForSelector('nav button');
  return {context,page,port};
}
async function closeNativeWindow(){
  const log=fs.readFileSync(path.join(data,'desktop.log'),'utf8');
  const matches=[...log.matchAll(/desktop start pid=(\d+)/g)];
  assert(matches.length,'owned desktop process must be logged');
  const pid=Number(matches.at(-1)[1]);assert(Number.isInteger(pid)&&pid>0);
  // Sends WM_CLOSE to the logged test instance, exercising desktop finally.
  execFileSync(process.env.PYTHON||'python',[path.join(__dirname,'desktop_close.py'),data],{windowsHide:true,stdio:'pipe'});
  await until(()=>child.exitCode!==null);
  assert.equal(child.exitCode,0,'native close must exit cleanly');
  await browser.close().catch(()=>{});browser=null;
}
(async()=>{
  let {context,page,port}=await launch();
  const state=await page.evaluate(async()=>await(await fetch('/api/state')).json());assert.equal(state.connected,false);
  for(const title of ['设备与校准','实验设计','运行监控','图像判相与复核','相图与主动学习','数据与报告']){
    await page.getByRole('button',{name:title,exact:true}).click();
    await page.screenshot({path:path.join(output,title+'.png'),fullPage:true});
  }
  await page.evaluate(()=>{
    navigator.mediaDevices.enumerateDevices=async()=>[];
    navigator.mediaDevices.getUserMedia=async()=>{const c=document.createElement('canvas');c.width=1000;c.height=600;const x=c.getContext('2d');window.smokeTimer=setInterval(()=>{x.fillStyle='#ddd';x.fillRect(0,0,c.width,c.height);},100);return c.captureStream(10);};
  });
  await page.getByRole('button',{name:'图像判相与复核',exact:true}).click();await page.locator('#cameraOpen').click();
  await page.waitForFunction(()=>document.querySelector('#cameraVideo').readyState>=2);
  await page.locator('#freezeBtn').click();await page.locator('#roiPanel').evaluate(e=>e.open=true);await page.locator('#manualRoisBtn').click();
  assert(await page.evaluate(()=>window.ATPSCamera.status().ready));await page.locator('#cameraClose').click();
  const exportResponse=await context.request.get(`http://127.0.0.1:${port}/api/lab/export`);assert(exportResponse.ok());assert((await exportResponse.body()).subarray(0,2).toString()==='PK');
  const token=(await(await context.request.get(`http://127.0.0.1:${port}/api/token`)).json()).token;
  const response=await context.request.post(`http://127.0.0.1:${port}/api/lab/project`,{headers:{'X-Control-Token':token},data:{name:'PACKAGED TEST ONLY',salt_port:13,total_mass_g:1,safe_mass_limit_g:1}});assert(response.ok(),await response.text());
  assert(fs.existsSync(path.join(data,'experiments','experiments.sqlite3')));
  await closeNativeWindow();
  execFileSync(process.env.PYTHON||'python',[path.join(__dirname,'desktop_legacy_fixture.py'),data],{windowsHide:true,stdio:'pipe'});
  ({context,page,port}=await launch());
  const migrated=await(await context.request.get(`http://127.0.0.1:${port}/api/lab/state`)).json();
  assert.equal(migrated.schema_version,3);assert.equal(migrated.running,false);
  assert.equal(migrated.samples[0].execution_state,'interrupted');assert.equal(migrated.samples[0].training_eligibility.eligible,false);
  assert(fs.readdirSync(path.join(data,'experiments','backups')).some(n=>n.startsWith('before-schema-')));
  const restarted=await(await context.request.get(`http://127.0.0.1:${port}/api/state`)).json();assert.equal(restarted.connected,false);assert.equal(restarted.busy,false);
  await closeNativeWindow();
  console.log('PASS packaged EXE: isolated first launch, six pages, synthetic camera, data directory, export, native close, legacy migration backup and no auto-resume; no hardware');
})().catch(e=>{console.error(e,childOutput);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close().catch(()=>{});
  if(child&&child.exitCode===null){
    // PyInstaller onefile has a parent and an extracted child. Own PID only.
    try{execFileSync('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});}catch{child.kill();}
  }
});
