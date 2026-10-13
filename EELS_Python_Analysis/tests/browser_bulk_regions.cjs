const {artifactPath} = require('./artifacts.cjs');
// All analyses use this disposable synthetic service.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process'),{once}=require('node:events'),{chromium}=require('playwright');
(async()=>{
  const root=path.resolve(__dirname,'..'),output=fs.mkdtempSync(artifactPath('.tmp_workflow_browser_'));
  const server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8',path.join(__dirname,'browser_workflow_server.py'),output,'roi-signed'],{cwd:root,windowsHide:true});
  const closed=once(server,'close');let browser,logs='';server.stderr.on('data',c=>logs+=c);
  try{
    const fixture=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('Fixture timeout '+logs)),30000);server.once('error',e=>{clearTimeout(timer);reject(e);});server.stdout.on('data',c=>{text+=c;if(text.includes('\n')){clearTimeout(timer);resolve(JSON.parse(text.split('\n')[0]));}});});
    browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage({viewport:{width:1440,height:1100},acceptDownloads:true}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));await page.goto(fixture.url);await page.waitForFunction(()=>state.previews.Si&&!state.busy);
    const legacy=await page.evaluate(()=>JSON.stringify(state.rois));await page.selectOption('#fit-mode','multivariate');await page.waitForFunction(()=>EelsWorkflow.state.restored);
    await page.check('#wf-preprocessed');await page.fill('#wf-starts','1');await page.fill('#wf-iterations','60');await page.click('#wf-decompose');
    await page.waitForFunction(()=>EelsWorkflow.state.decomposition&&!EelsWorkflow.state.busy,{},{timeout:30000});
    await page.click('[data-wf-step="3"]');await page.check('#wf-core-enabled');const rois=await page.evaluate(()=>JSON.stringify(EelsWorkflow.state.rois));
    await page.click('[data-wf-step="4"]');await page.click('#wf-profiles');await page.waitForFunction(()=>EelsWorkflow.state.profiles&&!EelsWorkflow.state.busy,{},{timeout:30000});
    await page.click('[data-wf-step="5"]');
    assert.equal(await page.evaluate(()=>state.info.amplitude_limit_version),'fixed_initial_local_extremum_v1');
    assert.equal(await page.evaluate(()=>state.info.candidate_score_version),'zero_baseline_prominence_v1');
    assert.equal(await page.locator('#wf-amplitude-unsupported').isVisible(),false);
    for(const name of ['Si','Ge','Interface','Core']){
      await page.selectOption('#wf-target',name);
      const bulk=['Si','Ge'].includes(name);
      if(bulk){assert.equal(await page.locator('#wf-polarity').isVisible(),false);assert.equal(await page.inputValue('#wf-peak-shape'),'gaussian');}
      await page.fill('#wf-peak-count',bulk?'1':'2');await page.fill('#wf-centers',name==='Si'?'28':name==='Ge'?'65':'');
      assert.equal(await page.isChecked('#wf-amplitude-enabled'),true);
      await page.fill('#wf-amplitude-percent',name==='Core'?'125':'150');
      await page.click('#wf-preview');await page.waitForFunction(()=>EelsWorkflow.state.preview);
      if(bulk){assert.equal(await page.evaluate(()=>EelsWorkflow.state.preview.analysis_kind),'bulk_gaussian');assert.equal(await page.evaluate(()=>EelsWorkflow.state.preview.residual),undefined);}
      await page.click('#wf-fit');await page.waitForFunction(n=>EelsWorkflow.state.fits[n]&&!EelsWorkflow.state.busy,name,{timeout:30000});
      assert.equal(await page.evaluate(()=>EelsWorkflow.state.fit.settings.amplitude_limit.factor),name==='Core'?1.25:1.5);
      assert.equal(await page.evaluate(()=>EelsWorkflow.state.fit.settings.candidate_score),bulk?undefined:'zero_baseline_prominence_v1');
      if(!bulk)assert((await page.locator('#wf-fit-policy').innerText()).includes('零线约束评分'));
      assert(await page.evaluate(()=>{
        const f=EelsWorkflow.state.fit,models=f.analysis_kind==='bulk_gaussian'?[f.bulk_fit]:[f.two_stage,f.joint];
        return models.every(m=>m.amplitudes.every((a,i)=>Math.abs(a)<=m.amplitude_constraint.amplitude_abs_upper[i]*(1+1e-8)));
      }));
      assert((await page.locator('#wf-fit-policy').innerText()).includes(name==='Core'?'125%':'150%'));
      if(bulk){assert(await page.evaluate(()=>EelsWorkflow.state.fit.bulk_fit.centers_meV.length===1));await page.screenshot({path:path.join(output,name+'-bulk.png'),fullPage:true});}
      else assert(await page.evaluate(()=>EelsWorkflow.state.fit.two_stage.amplitudes.some(v=>v<0)));
    }
    const ids=await page.evaluate(()=>Object.fromEntries(Object.entries(EelsWorkflow.state.fits).map(([key,fit])=>[key,fit.result_id])));
    assert.equal(Object.keys(ids).length,4);assert.equal(await page.evaluate(()=>JSON.stringify(EelsWorkflow.state.rois)),rois);
    await page.selectOption('#wf-target','Si');assert.equal(await page.inputValue('#wf-centers'),'28');assert.equal(await page.inputValue('#wf-peak-count'),'1');
    assert.equal(await page.inputValue('#wf-amplitude-percent'),'150');
    await page.selectOption('#wf-target','Core');assert.equal(await page.inputValue('#wf-amplitude-percent'),'125');
    await page.selectOption('#wf-target','Si');
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.fit.result_id),ids.Si);
    await page.fill('#wf-peak-count','5'); // Unexecuted UI edits must not change exported recipes.
    const [download]=await Promise.all([page.waitForEvent('download'),page.click('#wf-export')]);await download.saveAs(path.join(output,'four-regions.json'));
    assert.equal(download.suggestedFilename(),'synthetic.json');const doc=JSON.parse(fs.readFileSync(path.join(output,'four-regions.json'),'utf8'));
    assert.equal(doc.reproducibility.steps.length,6);
    for(const name of Object.keys(ids)){
      const item=doc.results.regions[name];assert.equal(item.status,'completed');assert.equal(item.result.result_id,ids[name]);
      assert.equal(item.result.analysis_kind,['Si','Ge'].includes(name)?'bulk_gaussian':'residual');
      assert.equal(doc.reproducibility.steps.find(s=>s.target_key===name)?.settings.candidate_score,['Si','Ge'].includes(name)?undefined:'zero_baseline_prominence_v1');
    }
    assert.equal(doc.reproducibility.steps.find(s=>s.stage==='fit'&&s.target_key==='Si').settings.peak_count,1);
    await page.fill('#wf-peak-count','1');await page.selectOption('#fit-mode','pca');assert.equal(await page.evaluate(()=>JSON.stringify(state.rois)),legacy);await page.selectOption('#fit-mode','multivariate');
    const second=await browser.newPage();let releaseRestore,restoreRequests=0;
    const restoreGate=new Promise(resolve=>{releaseRestore=resolve;});
    await second.route('**/api/workflow/fit?*',async route=>{const response=await route.fetch();if(++restoreRequests===4)await restoreGate;await route.fulfill({response});});
    // Persistent parameters now restore the workspace during initialization, before raw preview.
    await second.goto(fixture.url);
    try{
      await second.waitForFunction(()=>Object.keys(EelsWorkflow.state.fits).length===3);
      assert.equal(await second.evaluate(()=>EelsWorkflow.state.fit),null,'Do not display a partially restored fit under default parameters');
      assert.equal(await second.locator('#wf-target').isDisabled(),true);
      assert.equal(await second.evaluate(()=>EelsWorkflow.state.restored),false);
    }finally{releaseRestore();}
    await second.waitForFunction(()=>state.previews.Si&&!state.busy&&Object.keys(EelsWorkflow.state.fits).length===4&&byId('wf-status').textContent.includes('已恢复'));await second.click('[data-wf-step="5"]');await second.selectOption('#wf-target','Ge');
    assert.equal(await second.evaluate(()=>EelsWorkflow.state.fit.result_id),ids.Ge);assert.equal(await second.inputValue('#wf-centers'),'65');
    await second.selectOption('#wf-target','Core');assert.equal(await second.inputValue('#wf-amplitude-percent'),'125');
    assert.equal(await second.isChecked('#wf-amplitude-enabled'),true);await second.close();
    await page.click('#wf-fit-regions');await page.waitForFunction(prior=>!EelsWorkflow.state.busy&&Object.keys(prior).every(k=>EelsWorkflow.state.fits[k]?.result_id!==prior[k]),ids,{timeout:30000});
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.fit.selection.region),'Si');
    assert.equal(await page.inputValue('#wf-centers'),'28');assert.equal(await page.inputValue('#wf-peak-count'),'1');
    assert((await page.locator('#wf-region-fit-status').innerText()).split('已完成').length===5);
    assert(await page.evaluate(()=>['Core','Interface'].every(n=>EelsWorkflow.state.fits[n].settings.candidate_score==='zero_baseline_prominence_v1')));
    await page.screenshot({path:path.join(output,'four-region-status.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));await page.screenshot({path:path.join(output,'bulk-mobile.png'),fullPage:true});
    await page.evaluate(async()=>{const saved=EelsWorkflow.preferenceSnapshot();for(const cfg of Object.values(saved.fitInputs))delete cfg.amplitude_limit;await EelsWorkflow.restorePreferences(saved,()=>{},true);});
    await page.selectOption('#wf-target','Core');
    assert.equal(await page.isChecked('#wf-amplitude-enabled'),true);assert.equal(await page.inputValue('#wf-amplitude-percent'),'150');
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.fits.Core.settings.amplitude_limit.factor),1.25,'Draft migration must not relabel completed fits');
    await page.click('[data-wf-step="3"]');await page.fill('#wf-roi-xmax','3.4');await page.click('#wf-roi-apply');
    assert.equal(await page.evaluate(()=>Object.keys(EelsWorkflow.state.fits).length),0);
    assert.deepEqual(errors,[]);console.log(JSON.stringify({status:'passed',output}));
  }catch(e){console.error(logs.slice(-2000));throw e;}finally{if(browser)await browser.close();server.kill();await closed;}
})().catch(e=>{console.error(e);process.exitCode=1;});
