// Owns only a fake-hardware fixture on port 8879; never contacts the live server.
const {spawn}=require('child_process');
const path=require('path');
const program=path.resolve(__dirname,'../..');
const python=process.env.PYTHON || 'python';
let fixture;
async function run(script){
  await new Promise((resolve,reject)=>{
    const child=spawn(process.execPath,[path.join(__dirname,script)],{cwd:program,windowsHide:true,stdio:'inherit'});
    child.on('error',reject);
    child.on('exit',code=>code===0?resolve():reject(Error(script+' exited '+code)));
  });
}
(async()=>{
  fixture=spawn(python,['-u',path.join(__dirname,'fixture_server.py')],{cwd:program,windowsHide:true,stdio:['ignore','pipe','pipe']});
  await new Promise((resolve,reject)=>{
    let out='',err='';
    const timeout=setTimeout(()=>reject(Error('Fixture startup timeout: '+err)),15000);
    fixture.stdout.on('data',data=>{
      out+=data;
      if(out.includes('TEST ONLY injected hardware on 127.0.0.1:8879')){
        clearTimeout(timeout);console.log(out.trim());resolve();
      }
    });
    fixture.stderr.on('data',data=>{err+=data;process.stderr.write(data);});
    fixture.on('error',error=>{clearTimeout(timeout);reject(error);});
    fixture.once('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exited '+code+': '+err));});
  });
  await run('free_debug.js');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{
  if(fixture){fixture.kill();console.log('Stopped owned fake fixture PID '+fixture.pid);}
});
