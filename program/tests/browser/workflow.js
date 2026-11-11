const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),assert=require('assert');
const OUT=require('path').resolve(__dirname,'../../..');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.EDGE_PATH || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[],checks=[];
 page.on('pageerror',e=>errors.push(e.message));
 const check=(name,condition)=>{assert(condition,name);checks.push(name);console.log('PASS '+name)};
 const snapshot=()=>page.evaluate(async()=>await(await fetch('/api/state')).json());
 const wait=ms=>page.waitForTimeout(ms);
 async function api(path,data){return page.evaluate(async({path,data})=>{const t=await(await fetch('/api/token')).json();return await(await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':t.token},body:JSON.stringify(data)})).json()},{path,data});}
 async function finish(){let last='';for(let i=0;i<180;i++){await wait(130);const s=await snapshot();if(!s.busy){await page.evaluate(()=>poll());await wait(120);return s;}if(s.state==='FAULT')throw Error('Controller FAULT: '+s.message);if(s.prompt&&s.prompt.id!==last){await page.evaluate(()=>poll());await page.waitForFunction(id=>promptId===id,s.prompt.id);await wait(100);const b=s.prompt.kind==='prime_front'?page.getByRole('button',{name:'液头已到A段标记，停止引液'}):page.getByRole('button',{name:'已按提示现场确认，继续'});await b.click();last=s.prompt.id;}}throw Error('Action did not finish');}
 async function go(id){await page.locator('#'+id).click();await wait(180);return finish();}
 async function choose(id,val){await page.selectOption('#'+id,String(val));await wait(100);}
 async function fill(id,val){await page.fill('#'+id,String(val));}
 await page.goto('http://127.0.0.1:8879');await page.waitForFunction(()=>document.querySelector('#configEditor').value.length>50);
 await api('/api/disconnect',{});await wait(1000);const original=JSON.parse(fs.readFileSync(OUT+'/program/config.default.json','utf8'));await api('/api/config',{config:original});await page.reload();await wait(1000);
 check('只有实机连接且无模拟入口',await page.locator('#mode').count()===0&&await page.locator('#demoFrameBtn').count()===0&&!(await page.locator('body').innerText()).includes('模拟'));
 check('启动无需检查勾选',await page.locator('#geometryMeasured').count()===0&&await page.locator('#setupPreflight').count()===0);
 check('自动识别唯一测试CH340共用COM6',await page.locator('#autoDetectPort').isChecked()&&(await page.locator('#serialStatus').innerText()).includes('COM6')&&await page.locator('#serialPort').isDisabled()&&await page.locator('#pumpPort').count()===0&&await page.locator('#valvePort').count()===0);
 check('ABC管长10/10/71共91cm',await page.locator('#aCm').inputValue()==='10'&&await page.locator('#bCm').inputValue()==='10'&&await page.locator('#cCm').inputValue()==='71'&&(await page.locator('#lengthSummary').textContent()).includes('91 cm'));
 check('来源11至15全部80cm样品50cm',await page.locator('#branch11').inputValue()==='80'&&await page.locator('#branch15').inputValue()==='80'&&await page.locator('#branch1').inputValue()==='50');
 check('共线地址默认泵1阀2',await page.locator('#pumpAddress').inputValue()==='1'&&await page.locator('#valveAddress').inputValue()==='2');
 check('禁用按钮提供原因',await page.locator('#aspirateBtn').isDisabled()&&(await page.locator('#doseReason').innerText()).includes('连接'));
 check('API拒绝旧模拟模式',!(await api('/api/connect',{mode:'demo'})).ok);
 await go('connectBtn');check('无启动表可直接连接且未自动开泵',(await snapshot()).connected&&(await snapshot()).mode==='real'&&!(await snapshot()).logs.some(x=>x.event==='pulse_completed'));
 check('未校准清洗按钮明确显示试验及有限参数',(await page.locator('#washBtn').innerText()).includes('试洗')&&/16\s*(圈|转)/.test(await page.locator('#cleaningModeInfo').innerText()));
 await go('washBtn');let trial=await snapshot();check('无校准可单独15口试洗且不假定水管已满',trial.selected_port===15&&trial.reset_sequence==='washed_unverified'&&!trial.sources['15'].primed&&!trial.config.reset.validated);
 await go('purgeBtn');trial=await snapshot();check('无校准可单独16口试吹扫但不解锁定量',trial.selected_port===16&&trial.reset_sequence==='purged_unverified'&&trial.common==='air_unverified'&&await page.locator('#aspirateBtn').isDisabled());
 await go('resetBtn');trial=await snapshot();check('无校准组合试洗试吹扫依序完成仍保留未验证状态',trial.logs.filter(x=>x.event==='pulse_completed').slice(-2).map(x=>x.port).join(',')==='15,16'&&trial.common==='air_unverified'&&!trial.config.reset.validated);
 await page.locator('#commissionDetails').evaluate(e=>e.open=true);await choose('commissionPort',16);await choose('commissionDirection','aspirate');await fill('commissionSteps',500);await fill('commissionRpm',10);await fill('commissionTimeout',30);await page.locator('#commissionCheck').check();await go('commissionBtn');check('16空气口网页允许10rpm有界调试',(await snapshot()).logs.some(x=>x.event==='pulse_completed'&&x.port===16&&x.rpm===10&&x.steps===500));await fill('commissionSteps',1);await fill('commissionRpm',1);await fill('commissionTimeout',10);
 await go('disconnectBtn');const fixture=JSON.parse(fs.readFileSync(require('path').join(__dirname,'test_output/fixture_config.json'),'utf8'));await api('/api/config',{config:fixture});await page.reload();await wait(900);await go('connectBtn');await go('newSamplesBtn');
 const priorPulse=(await snapshot()).logs.filter(x=>x.event==='pulse_completed').at(-1)?.at;await choose('selectPort',11);await go('selectPortBtn');check('单独选阀不启动泵',(await snapshot()).selected_port===11&&(await snapshot()).logs.filter(x=>x.event==='pulse_completed').at(-1)?.at===priorPulse);
 await go('purgeBtn');check('单独吹扫不能证明复位',(await snapshot()).common==='air_unverified'&&await page.locator('#aspirateBtn').isDisabled());
 await go('washBtn');check('单独15口洗保持湿状态',(await snapshot()).reset_sequence==='washed'&&(await snapshot()).common==='wet');
 check('当前步骤指向16口吹扫',(await page.locator('#nextStep').innerText()).includes('下一步点击'));
 await go('purgeBtn');check('按顺序洗吹并确认后就绪',(await snapshot()).common==='air_reset_verified');
 await go('primeBtn');check('母液引液须观察确认',(await snapshot()).sources['11'].primed);
 await go('resetBtn');await choose('profileSelect','demo-11-1-first');
 await go('aspirateBtn');let s=await snapshot();check('只吸取产生锁定液段不计交付',s.pending_plug.source===11&&s.pending_plug.target===1&&s.samples['1'].dose_count===0);
 check('待送液段显示验证等待',(await page.locator('#pendingPlug').innerText()).includes('验证等待剩余'));
 check('待送期间禁止再吸取洗管自动队列',await page.locator('#aspirateBtn').isDisabled()&&await page.locator('#washBtn').isDisabled()&&await page.locator('#runBtn').isDisabled());
 await choose('selectPort',2);check('待送禁止错瓶切阀',await page.locator('#selectPortBtn').isDisabled());await choose('selectPort',1);await go('selectPortBtn');check('只允许预选锁定目标',(await snapshot()).selected_port===1);
 await go('chaseBtn');s=await snapshot();check('只追送完成一次且清空待送',!s.pending_plug&&s.samples['1'].dose_count===1);
 await go('bubbleBtn');check('独立鼓泡记录停气时间',!!(await snapshot()).samples['1'].last_gas_stop);
 await page.locator('#sampleRows tr').nth(0).getByRole('button').click();await finish();
 await go('prepareCaptureBtn');
 const bytes=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=1200;c.height=600;const x=c.getContext('2d');x.fillStyle='#eee';x.fillRect(0,0,1200,600);x.fillStyle='#222';x.font='24px sans-serif';x.fillText('SOFTWARE TEST IMAGE - NOT AN EXPERIMENT',30,45);return c.toDataURL('image/png').split(',')[1]});
 await page.locator('#imageFile').setInputFiles({name:'TEST_ONLY.png',mimeType:'image/png',buffer:Buffer.from(bytes,'base64')});await wait(700);await fill('fileTime',new Date().toISOString());
 check('仅相机或文件导入实图入口',await page.locator('#cameraBtn').count()===1&&await page.locator('#imageFile').count()===1);

 const roiValues=await page.locator('#roiRows tr').evaluateAll(rows=>rows.map(row=>[...row.querySelectorAll('input')].map(x=>Number(x.value))));
 check('六ROI同排左至右且不重叠',roiValues.length===6&&new Set(roiValues.map(r=>r[1])).size===1&&roiValues.every((r,i)=>i===0||r[0]>roiValues[i-1][0]+roiValues[i-1][2]));
 await page.locator('#roiCanvas').screenshot({path:OUT+'/UI单排成像预览.png'});
 await page.locator('#alignment').check();await go('captureBtn');s=await snapshot();check('保存全帧PNG及逐瓶时间',s.imaging.complete&&s.imaging.records.length===1&&typeof s.imaging.records[0].elapsed_since_stop_s==='number');
 check('元数据为正面单排',s.imaging.view_direction==='fixed_front_row'||JSON.stringify(s.imaging).includes('fixed_front_row'));
 await page.getByText('人工判相记录',{exact:true}).click();await choose('classification','two_phase');await fill('phaseNote','软件演练图人工标注测试，非实验结论');await go('labelBtn');check('人工判相关联采集记录',(await snapshot()).imaging.records[0].classification==='two_phase');
 await go('resetBtn');await choose('profileSelect','demo-11-2-first');await go('aspirateBtn');await go('discardPlugBtn');s=await snapshot();check('弃液复位不计目标剂量',!s.pending_plug&&s.samples['2'].dose_count===0&&s.common==='air_reset_verified');
 await page.locator('#withBubble').check();await go('doseBtn');s=await snapshot();check('组合单次吸取追送与鼓泡',s.samples['2'].dose_count===1&&s.common==='wet');
 await choose('profileSelect','demo-11-3-first');await page.locator('#withBubble').uncheck();await page.locator('#addQueueBtn').click();await choose('profileSelect','demo-11-3-continued');await page.locator('#addQueueBtn').click();await go('runBtn');s=await snapshot();check('自动队列串行初次连续并每剂后复位',s.samples['3'].dose_count===2&&s.common==='air_reset_verified');
 await page.locator('#commissionDetails').evaluate(e=>e.open=true);await page.locator('#commissionCheck').check();await go('commissionBtn');check('有限脉冲调试使液路状态失效',(await snapshot()).common==='unknown');
 await page.locator('#resetBtn').click();await wait(1200);check('观察点等待人工确认',!!(await snapshot()).prompt);await page.locator('#stopBtn').click();await wait(1200);check('停止优先且锁定状态',!(await snapshot()).connected&&(await snapshot()).common==='unknown');
 await page.locator('#setupDetails').evaluate(e=>e.open=true);await fill('statusOk','0');await fill('statusBusy','1');await fill('statusNote','软件表单保存测试数据，不是设备状态码证据');await fill('liquid14','PEG 测试名称，仅浏览器隔离测试');await go('saveSetupBtn');s=await snapshot();check('普通表单保存硬件和母液字段',s.config.pump.status_ok[0]===0&&s.config.liquids['14'].includes('测试名称')&&s.config.geometry.measured&&s.config.communication.port==='COM6'&&!s.config.reset.validated);
 await page.locator('#calibrationDetails').evaluate(e=>e.open=true);
 const values={calId:'ui-synthetic-test',calCountMin:0,calCountMax:0,calAspSteps:100,calAspRpm:1,calAspTimeout:5,calChaseSteps:100,calChaseRpm:1,calChaseTimeout:5,calBubbleSteps:1,calBubbleRpm:.1,calBubbleTimeout:2,calBubbleCount:1,calBubbleGap:.1,calImmersion:0,calBubbleImmersion:2,calSettle:300,calHold:600,calPlugHold:30,calMethod:'软件隔离测试人工合成数值，非实测；测试组分当量输入路径',calEvidence:'软件隔离测试原始证据，非实际实验；只验证表单保存',deliveryReadings:Array(10).fill('0.2').join('\n'),waterReadings:'0.001\n0.002\n0.001',crossReadings:'0.0001\n0.0002\n0.0001'};
 for(const[id,val]of Object.entries(values))await fill(id,val);
 await go('reportBtn');check('原始数据报告显示均值和重复次数',(await page.locator('#calReport').innerText()).includes('n=10')&&(await page.locator('#calReport').innerText()).includes('0.200000'));
 check('报告通过不自动人工签核',!await page.locator('#calValidated').isChecked()&&!await page.locator('#calValidated').isDisabled());
 await page.locator('#calValidated').check();await choose('calSample',2);check('换目标瓶撤销旧报告签核',!await page.locator('#calValidated').isChecked()&&await page.locator('#calValidated').isDisabled());await choose('calSample',1);
 await go('reportBtn');await page.locator('#calValidated').check();await fill('calChaseSteps',101);check('改动作参数撤销旧报告签核',!await page.locator('#calValidated').isChecked()&&await page.locator('#calValidated').isDisabled());await fill('calChaseSteps',100);
 await choose('calMode','water_gravimetry');await fill('calDensity',1);await go('reportBtn');check('禁止母液总质量直接换体积',(await page.locator('#errorBox').innerText()).includes('只用于15口'));
 await choose('calSource',15);await go('reportBtn');check('15口纯水质量密度计算可用',(await page.locator('#calReport').innerText()).includes('0.200000'));
 await choose('calMode','stock_equivalent');await choose('calSource',11);await go('reportBtn');await page.locator('#calValidated').check();await go('saveProfileBtn');s=await snapshot();const profile=s.config.profiles.find(p=>p.id==='ui-synthetic-test');check('普通表单保存全部参数与人工签核证据',profile.validated&&profile.evidence.n===10&&profile.max_plug_hold_s===30&&profile.geometry_signature===s.geometry_signature);
 await page.evaluate(()=>{const d=document.createElement('p');d.className='note';d.textContent='以下为隔离软件测试合成数据，非实验结果，不可用于实机。';document.querySelector('#calibrationDetails').prepend(d);document.querySelector('.toolbar').style.position='static'});await page.locator('#calibrationDetails').screenshot({path:OUT+'/UI校准表单预览.png'});
 await api('/api/config',{config:original});await page.reload();await wait(1500);
 await page.setViewportSize({width:390,height:844});check('手机页面无水平溢出',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:OUT+'/UI手机预览.png',fullPage:true});
 await page.setViewportSize({width:1440,height:1050});await page.screenshot({path:OUT+'/UI界面预览.png',fullPage:true});await page.screenshot({path:OUT+'/UI首屏预览.png',fullPage:false});
 check('无浏览器JavaScript错误',errors.length===0);
 fs.writeFileSync(OUT+'/program/BROWSER_TEST_REPORT.txt',new Date().toISOString()+'\n'+checks.map((x,i)=>`${i+1}. PASS ${x}`).join('\n')+'\n说明：本次使用测试进程注入硬件替身与人工合成校准数据，未打开实际COM6，仅验证软件流程与本地浏览器交互，不代表实机加料、鼓泡或判相性能。测试数据在program/tests/browser/test_output中，交付默认配置不含真实校准。\n');
 console.log(JSON.stringify({checks:checks.length,errors},null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

