// Isolated synthetic fixture only; never uses the daily service at port 8765.
const {artifactPath}=require('./artifacts.cjs');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {chromium}=require('playwright');

(async()=>{
  const root=path.resolve(__dirname,'..'),output=fs.mkdtempSync(artifactPath('.tmp_workflow_browser_'));
  const server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8',path.join(__dirname,'browser_workflow_server.py'),output,'roi-signed'],{cwd:root,windowsHide:true});
  const closed=once(server,'close');let logs='',browser;
  server.stderr.on('data',chunk=>logs+=chunk);
  try{
    const fixture=await new Promise((resolve,reject)=>{
      let text='';const timer=setTimeout(()=>reject(Error(logs||'Fixture timeout')),30000);
      server.on('error',reject);server.stdout.on('data',chunk=>{text+=chunk;if(text.includes('\n')){clearTimeout(timer);resolve(JSON.parse(text.split('\n')[0]));}});
    });
    async function api(endpoint,payload){
      const response=await fetch(fixture.url+'/api/workflow/'+endpoint,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const result=await response.json();assert(response.ok,JSON.stringify(result));return result;
    }
    const ids={};
    async function run(stage,settings){
      const job=await api('run',{stage,settings,decomposition_id:ids.decomposition,profiles_id:ids.profiles});
      for(let i=0;i<300;i++){
        await new Promise(resolve=>setTimeout(resolve,100));const status=await api('jobs/'+job.job_id);
        if(status.status==='done'){ids[stage]=status.result_id;return;}
        if(status.status==='error')throw Error(status.error);
      }
      throw Error('Job timed out');
    }
    await run('decomposition',{method:'mcr_als',components:3,fit_min:10,fit_max:80,init:'nndsvda',starts:1,seed:37,max_iter:60,tolerance:1e-5,negative_policy:'reject',preprocessing_confirmed:true});
    const roi=(x_min,x_max,y_min=0,y_max=7)=>({x_min,x_max,y_min,y_max});
    await run('profiles',{regions_confirmed:true,core_enabled:true,stripe_rows:2,bulk_scope:'global',rois:{Si:roi(0,4),Ge:roi(11,15),Interface:roi(6,9),Core:roi(7,8,3,4)},alignment:{enabled:false},template:{kind:'mean'}});
    const configs=Object.fromEntries(['Si','Ge','Interface','Core'].map(name=>[name,{selection:{kind:'region',region:name},analysis_kind:['Si','Ge'].includes(name)?'bulk_gaussian':'residual',shape:'gaussian',polarity:['Si','Ge'].includes(name)?'positive':'both',peak_count:name==='Si'||name==='Ge'?1:2,centers:name==='Si'?[28]:name==='Ge'?[65]:null,prominence:.05,center_radius:3,fwhm_min:1,fwhm_max:14}]));
    await run('fit_regions',{regions:configs});
    const state=await api('state');
    const saved=await api('export',{result_ids:state.result_ids});
    const doc=structuredClone(saved);doc.reproducibility.software.python='older-version';
    const json=path.join(output,'保存流程.json');fs.writeFileSync(json,'\ufeff'+JSON.stringify(doc));
    browser=await chromium.launch({channel:'chrome',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1060},acceptDownloads:true});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(fixture.url);await page.waitForFunction(()=>state.info&&!state.busy&&state.previews.Si);
    await page.selectOption('#fit-mode','multivariate');await page.waitForFunction(()=>EelsWorkflow.state.fit&&!EelsWorkflow.state.busy);
    await page.click('[data-wf-step="5"]');await page.fill('#wf-peak-count','7');
    const old=await page.evaluate(()=>EelsWorkflow.state.decomposition.result_id);
    await page.click('#import-workflow');await page.setInputFiles('#workflow-import-file',json);
    await page.click('#workflow-import-submit');await page.waitForFunction(()=>document.getElementById('workflow-import-message').textContent.includes('版本'));
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.decomposition.result_id),old);
    await page.check('#workflow-import-version');await page.click('#workflow-import-submit');
    await page.waitForFunction(prior=>!EelsWorkflow.state.busy&&EelsWorkflow.state.fits.Core?.restoration==='saved_parameters_v1'&&EelsWorkflow.state.decomposition?.result_id!==prior,old,{timeout:45000});
    assert.equal(await page.inputValue('#wf-peak-count'),'2');
    const imported=await page.evaluate(()=>({rois:EelsWorkflow.state.rois,fits:EelsWorkflow.state.fits}));
    assert.deepEqual(imported.rois,saved.reproducibility.steps[1].settings.rois);
    for(const name of ['Si','Ge','Interface','Core']){
      const key=['Si','Ge'].includes(name)?'bulk_fit':'two_stage';
      assert.deepEqual(imported.fits[name][key].centers_meV,saved.results.regions[name].result[key].centers_meV);
    }
    assert((await page.textContent('#wf-fit-policy')).includes('JSON 恢复'));
    await page.screenshot({path:path.join(output,'imported-results.png'),fullPage:true});
    await page.selectOption('#wf-target','Si');await page.fill('#wf-centers','28.2');
    await page.click('#wf-fit');await page.waitForFunction(()=>!EelsWorkflow.state.busy&&!EelsWorkflow.state.fits.Si.restoration);
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.fits.Ge.result_id),imported.fits.Ge.result_id);
    const [download]=await Promise.all([page.waitForEvent('download'),page.click('#wf-export')]);
    const exportedPath=path.join(output,'adjusted.json');await download.saveAs(exportedPath);
    const exported=JSON.parse(fs.readFileSync(exportedPath,'utf8'));
    assert.equal(exported.reproducibility.steps.find(s=>s.target_key==='Si').restoration,undefined);
    assert.equal(exported.reproducibility.steps.find(s=>s.target_key==='Core').restoration,'saved_parameters_v1');
    const bad=path.join(output,'bad.json');fs.writeFileSync(bad,'{"format":"eels_peak_results_v1"}');
    await page.click('#import-workflow');await page.setInputFiles('#workflow-import-file',bad);await page.click('#workflow-import-submit');
    await page.waitForFunction(()=>document.getElementById('workflow-import-message').textContent.includes('不支持旧 PCA'));
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.fits.Ge.result_id),imported.fits.Ge.result_id);
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'import-dialog-mobile.png'),fullPage:true});
    assert(await page.evaluate(()=>document.getElementById('workflow-import-dialog').getBoundingClientRect().width<=innerWidth));
    assert(await page.evaluate(()=>{const d=document.getElementById('workflow-import-dialog');return d.scrollWidth<=d.clientWidth+1;}),'Dialog content must fit without horizontal clipping');
    await page.click('#workflow-import-close');await page.setViewportSize({width:1440,height:1060});
    await page.evaluate(()=>EelsPreferences.save(true));await page.reload();
    await page.waitForFunction(()=>EelsWorkflow.state.fits.Core&&!EelsWorkflow.state.busy);
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.fits.Core.result_id),imported.fits.Core.result_id);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,output,checks:['BOM JSON','version mismatch opt-in','saved peak equality','ROI and drafts restored','refit one region','re-export provenance','invalid JSON atomicity','reload','mobile dialog']}));
  }finally{
    if(browser)await browser.close();server.kill();await closed;
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
