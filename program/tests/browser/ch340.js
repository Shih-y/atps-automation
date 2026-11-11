// Browser UI contract tests use a fake HTTP boundary; no serial drivers or production server.
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const program=path.resolve(__dirname,'../..'),out=path.join(__dirname,'test_output','ch340');fs.mkdirSync(out,{recursive:true});
const checks=[];let browser,server,state,ports,requests=[],scanFailure=false,connectPort='COM5',scanDelay=0;
const adapter=device=>({device,description:'USB-SERIAL CH340',hwid:'USB VID:PID=1A86:7523',vid:6790,pid:29987,is_ch340:true});
const bluetooth={device:'COM3',description:'Bluetooth Serial Port',hwid:'BTHENUM',vid:null,pid:null,is_ch340:false};
function reset(items){ports=items;requests=[];scanFailure=false;scanDelay=0;connectPort='COM5';state={connected:false,busy:false,mode:'real',state:'DISCONNECTED',common:'unknown',profiles:[],logs:[],samples:{},sources:{},imaging:{},geometry_signature:'fixture:COM6',config:JSON.parse(fs.readFileSync(path.join(program,'config.default.json'),'utf8'))};state.config.communication.port='COM6';}
function check(name,condition=true){assert(condition,name);checks.push(name);console.log('PASS '+name);}
function respond(res,body,status=200){res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body));}
(async()=>{
 reset([adapter('COM5'),bluetooth]);
 server=http.createServer(async(req,res)=>{
  const chunks=[];for await(const chunk of req)chunks.push(chunk);const data=chunks.length?JSON.parse(Buffer.concat(chunks).toString()):undefined;
  requests.push({path:req.url,method:req.method,data});
  if(req.url==='/api/token')return respond(res,{token:'isolated-ui-test'});
  if(req.url==='/api/state')return respond(res,state);
  if(req.url==='/api/serial-ports'){
   if(scanDelay)await new Promise(resolve=>setTimeout(resolve,scanDelay));
   if(scanFailure)return respond(res,{error:'枚举暂时失败'},500);
   const found=ports.filter(p=>p.is_ch340);return respond(res,{ports,ch340_ports:found,suggested_port:found.length===1?found[0].device:null,message:'仅枚举系统端口'});
  }
  if(req.url==='/api/connect'){
   if(data.auto_detect&&ports.filter(p=>p.is_ch340).length!==1)return respond(res,{error:'检测到多个 CH340，请手动选择串口'},400);
   const port=data.auto_detect?connectPort:data.pump_port;if(port!==state.config.communication.port)state.config.reset.validated=false;state.connected=true;state.state='CONNECTED';state.config.communication.port=port;state.geometry_signature='fixture:'+port;return respond(res,{ok:true,state});
  }
  if(req.url==='/api/disconnect'){state.connected=false;state.state='DISCONNECTED';return respond(res,{ok:true,state});}
  if(req.url==='/api/config'&&req.method==='POST'){state.config=data.config;return respond(res,{ok:true,state});}
  if(req.url==='/'||req.url==='/bench.js'||req.url==='/live.js'){res.writeHead(200,{'Content-Type':req.url==='/'?'text/html; charset=utf-8':'text/javascript; charset=utf-8'});return res.end(fs.readFileSync(path.join(program,'static',req.url==='/'?'index.html':req.url.slice(1))));}
  respond(res,{error:'Unexpected test request '+req.url},404);
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 assert.notEqual(server.address().port,8765);
 browser=await chromium.launch({executablePath:process.env.EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const loaded=async()=>{await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>document.getElementById('configEditor').value.length>50);};
 const scanned=async()=>page.waitForFunction(()=>document.getElementById('scanPortsBtn')&&!document.getElementById('scanPortsBtn').disabled);
 const reconnectReady=async()=>{await page.locator('#disconnectBtn').click();await page.waitForFunction(()=>!document.getElementById('autoDetectPort').disabled);};
 await loaded();
 // Catches stale COM6 presentation and missing automatic OS enumeration.
 check('page load enumerates ports without issuing any POST',requests.some(r=>r.path==='/api/serial-ports'&&r.method==='GET')&&!requests.some(r=>r.method==='POST'));
 await scanned();
 check('unique CH340 defaults to automatic mode and displays detected COM5',await page.locator('#autoDetectPort').isChecked()&&(await page.locator('#serialStatus').innerText()).includes('COM5')&&await page.locator('#serialPort').isDisabled());
 check('heading and disconnected instructions do not prescribe stale COM6',!(await page.locator('h1').innerText()).includes('COM6')&&!(await page.locator('#nextStep').innerText()).includes('COM6'));
 // Catches sending an old port after an unplug/replug, and displaying the request instead of the connected result.
 connectPort='COM9';ports=[adapter('COM9'),bluetooth];await page.locator('#connectBtn').click();
 await page.waitForFunction(()=>document.getElementById('modeBadge').textContent.includes('COM9'));
 const autoConnect=requests.find(r=>r.path==='/api/connect');
 check('connect requests fresh automatic resolution and displays actual connected COM9',autoConnect.data.auto_detect===true&&!autoConnect.data.pump_port&&!autoConnect.data.valve_port&&(await page.locator('#serialStatus').innerText()).includes('COM9'));
 check('connected scan hint does not advertise the previous COM5 result',(await page.locator('#serialScanStatus').innerText()).includes('COM9')&&!(await page.locator('#serialScanStatus').innerText()).includes('COM5'));
 check('connected state locks rescan, mode and manual port',await page.locator('#scanPortsBtn').isDisabled()&&await page.locator('#autoDetectPort').isDisabled()&&await page.locator('#serialPort').isDisabled());
 check('connection does not request any pump or valve actions',!requests.some(r=>r.path==='/api/action'));

 state.hardware_status={pump_status:'running',direction:'cw',flow:'dispense',rpm:25,remaining:120,unit:'steps',pump_at:Date.now()/1000,valve_status:'confirmed',valve_port:1,target_port:null,valve_at:Date.now()/1000};await page.evaluate(()=>poll());
 check('live panel shows CW delivery and confirmed sample valve',(await page.locator('#livePump').innerText()).includes('正转 CW')&&(await page.locator('#liveValve').innerText()).includes('1口')&&(await page.locator('#liveFlow').innerText()).includes('1号样品瓶')&&await page.locator('#livePumpRotor').evaluate(e=>e.classList.contains('turning')));
 state.hardware_status={...state.hardware_status,direction:'ccw',flow:'aspirate',valve_port:15,pump_at:Date.now()/1000};await page.evaluate(()=>poll());
 check('live panel shows CCW aspiration and highlights valve15',(await page.locator('#livePump').innerText()).includes('反转 CCW')&&(await page.locator('#liveFlow').innerText()).includes('C端废液')&&await page.locator('#portMap .live-port b').innerText()==='15');
 state.hardware_status={...state.hardware_status,pump_status:'stopped',rpm:0,remaining:0,valve_status:'switching',valve_port:null,target_port:16};await page.evaluate(()=>poll());
 check('switching valve never appears already confirmed',(await page.locator('#liveValve').innerText()).includes('正在切换')&&!await page.locator('#livePumpRotor').evaluate(e=>e.classList.contains('turning'))&&await page.locator('#portMap .live-port').count()===0);
 state.hardware_status={...state.hardware_status,pump_status:'running',pump_at:Date.now()/1000-10};await page.evaluate(()=>poll());
 check('stale pump readback does not animate as live running',!await page.locator('#livePumpRotor').evaluate(e=>e.classList.contains('turning'))&&(await page.locator('#livePump').innerText()).includes('待核对'));
 await page.evaluate(()=>renderHardwareStatus(state,false));
 check('communication failure clears direction and valve claims',(await page.locator('#livePump').innerText()).includes('状态未知')&&(await page.locator('#liveValve').innerText()).includes('未知'));
 state.hardware_status={...state.hardware_status,pump_status:'stop_requested',pump_at:Date.now()/1000,valve_status:'unknown'};await page.evaluate(()=>poll());
 check('stop request is not displayed as confirmed stop',(await page.locator('#livePump').innerText()).includes('未确认'));
 await reconnectReady();
 // Catches re-signing an invalidated reset from a stale checkbox after changing ports.
 reset([adapter('COM5')]);state.config.reset.validated=true;state.config.reset.covers_initial_air_filled_water_branch=true;state.config.reset.evidence='TEST ONLY old COM6 reset evidence';await loaded();await scanned();
 assert(await page.locator('#resetValidated').isChecked());
 await page.locator('#setupDetails').evaluate(e=>e.open=true);await page.locator('#statusNote').fill('保留用户尚未保存的状态码说明草稿');
 await page.evaluate(()=>{latestReport={screening_pass:true};reportBinding={geometry:state.geometry_signature};$('calValidated').checked=true;$('calBubbleValidated').checked=true;});
 await page.locator('#connectBtn').click();await page.waitForFunction(()=>document.getElementById('modeBadge').textContent.includes('COM5'));
 check('changed connection signature clears stale reset and calibration signoffs',!await page.locator('#resetValidated').isChecked()&&!await page.locator('#calValidated').isChecked()&&!await page.locator('#calBubbleValidated').isChecked()&&await page.locator('#calValidated').isDisabled());
 check('connection signature sync preserves unrelated manual edits',await page.locator('#statusNote').inputValue()==='保留用户尚未保存的状态码说明草稿');
 await reconnectReady();await page.locator('#saveSetupBtn').click();await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('参数设置已保存'));
 const savedReset=requests.filter(r=>r.path==='/api/config'&&r.method==='POST').at(-1);
 check('saving settings after COM6 to COM5 reconnection keeps reset unvalidated',savedReset.data.config.communication.port==='COM5'&&savedReset.data.config.reset.validated===false);
 reset([adapter('COM5'),adapter('COM7'),bluetooth]);await loaded();await scanned();
 check('multiple adapters require manual choice and disable ambiguous automatic connect',await page.locator('#connectBtn').isDisabled()&&(await page.locator('#serialScanStatus').innerText()).includes('手动')&&await page.locator('#serialPorts option').count()===3);
 await page.locator('#autoDetectPort').uncheck();await page.locator('#serialPort').fill('COM7');
 await page.evaluate(()=>poll());
 check('polling preserves a manual port edit',await page.locator('#serialPort').inputValue()==='COM7');
 await page.locator('#scanPortsBtn').click();await scanned();
 check('rescanning preserves a manual port edit',await page.locator('#serialPort').inputValue()==='COM7');
 await page.locator('#connectBtn').click();await page.waitForFunction(()=>document.getElementById('modeBadge').textContent.includes('COM7'));
 const manualConnect=requests.filter(r=>r.path==='/api/connect').at(-1);
 check('manual connect sends explicit shared pump and valve port',manualConnect.data.auto_detect===false&&manualConnect.data.pump_port==='COM7'&&manualConnect.data.valve_port==='COM7');
 reset([bluetooth]);await loaded();await scanned();
 check('Bluetooth-only enumeration does not select a port or enable automatic connect',await page.locator('#serialPort').inputValue()===''&&await page.locator('#connectBtn').isDisabled()&&(await page.locator('#serialScanStatus').innerText()).includes('未发现'));
 await page.locator('#autoDetectPort').uncheck();check('empty manual port cannot connect',await page.locator('#connectBtn').isDisabled());
 await page.locator('#serialPort').fill('COM12');check('explicit manually typed port can connect',!await page.locator('#connectBtn').isDisabled());
 state.busy=true;await page.evaluate(()=>poll());check('busy controller locks mode, scan and manual input',await page.locator('#scanPortsBtn').isDisabled()&&await page.locator('#autoDetectPort').isDisabled()&&await page.locator('#serialPort').isDisabled());
 state.busy=false;await page.evaluate(()=>poll());
 ports=[adapter('COM5')];await page.locator('#autoDetectPort').check();await page.locator('#scanPortsBtn').click();await scanned();
 ports=[adapter('COM5'),adapter('COM7')];await page.locator('#connectBtn').click();
 await page.waitForFunction(()=>!document.getElementById('errorBox').classList.contains('hidden'));
 await scanned();check('adapter changes at connection show backend error and refresh ambiguity',!state.connected&&await page.locator('#connectBtn').isDisabled()&&(await page.locator('#serialScanStatus').innerText()).includes('手动'));
 scanFailure=true;await page.locator('#scanPortsBtn').click();await scanned();
 check('failed rescan clears stale automatic candidate and offers retry/manual',await page.locator('#connectBtn').isDisabled()&&(await page.locator('#serialScanStatus').innerText()).includes('失败'));
 scanFailure=false;ports=[adapter('COM5')];scanDelay=250;await page.locator('#scanPortsBtn').click();
 check('scan in progress disables connect and mode changes',await page.locator('#connectBtn').isDisabled()&&await page.locator('#autoDetectPort').isDisabled());
 await scanned();check('retry restores automatic connection',!await page.locator('#connectBtn').isDisabled());
 reset([adapter('COM5'),bluetooth]);await loaded();await scanned();
 await page.screenshot({path:path.join(out,'desktop.png'),fullPage:false});
 await page.setViewportSize({width:390,height:844});check('connection controls fit mobile viewport',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(out,'mobile.png'),fullPage:false});
 check('real page and bench script have no JavaScript errors',errors.length===0);
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({checks,errors,port:server.address().port,note:'Isolated fake HTTP only. No production server or hardware was used.'},null,2));
 console.log(JSON.stringify({passed:checks.length,errors},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(server)await new Promise(resolve=>server.close(resolve));});
