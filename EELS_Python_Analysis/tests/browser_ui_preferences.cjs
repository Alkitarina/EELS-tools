const {artifactPath} = require('./artifacts.cjs');
// Synthetic-only service restart, option/ROI drafts, completed recipes, and save failures.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {chromium}=require('playwright');

(async()=>{
  const root=path.resolve(__dirname,'..'),output=fs.mkdtempSync(artifactPath('.tmp_workflow_browser_preferences_'));
  let server,closed,browser,context,logs='',fixture;
  async function start(){
    console.log('Starting isolated fixture',output);
    server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8',path.join(__dirname,'browser_workflow_server.py'),output],{cwd:root,windowsHide:true});
    closed=once(server,'close');server.stderr.on('data',chunk=>logs+=chunk);
    fixture=await new Promise((resolve,reject)=>{
      let content='';const timer=setTimeout(()=>reject(Error(logs||'Fixture timeout')),30000);
      server.once('error',error=>{clearTimeout(timer);reject(error);});
      server.once('exit',()=>{clearTimeout(timer);reject(Error(logs||'Fixture exited before startup'));});
      server.stdout.on('data',chunk=>{content+=chunk;if(content.includes('\n')){clearTimeout(timer);resolve(JSON.parse(content.split('\n')[0]));}});
    });
  }
  const errors=[];
  async function pageForContext(){
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.goto(fixture.url);await page.waitForFunction(()=>state.previews.Si&&!state.busy&&!byId('preferences-save').disabled);
    return page;
  }
  async function save(page){
    const response=page.waitForResponse(r=>r.url().endsWith('/api/ui-preferences')&&r.request().method()==='POST');
    await page.click('#preferences-save');assert.equal((await response).status(),200);
    await page.waitForFunction(()=>byId('preferences-status').textContent.startsWith('参数已记忆'));
  }
  async function values(page,fields){
    await page.evaluate(fields=>{for(const [id,value]of Object.entries(fields)){
      const node=byId(id);if(typeof value==='boolean')node.checked=value;else node.value=value;
      node.dispatchEvent(new Event('change',{bubbles:true}));
    }},fields);
    await page.waitForTimeout(30);
  }
  try{
    await start();console.log('Fixture ready',fixture.url);browser=await chromium.launch({channel:'chrome',headless:true});context=await browser.newContext({viewport:{width:1440,height:1060}});
    let page=await pageForContext();console.log('Page ready');
    assert(!fs.existsSync(path.join(output,'ui_preferences.json')),'First load must not overwrite saved drafts with defaults');
    await page.fill('#pca-components','7');await page.check('#region-fit-enabled');
    await page.selectOption('#region-fit-model','logistic');await page.uncheck('#region-fit-full-y');
    await page.selectOption('#fit-mode','multivariate');await page.waitForFunction(()=>EelsWorkflow.state.restored);
    await page.selectOption('#wf-init','roi');await page.selectOption('#wf-method','nmf');
    await page.selectOption('#wf-negative','clip_for_decomposition');await page.fill('#wf-rank','8');await page.locator('#wf-rank').blur();
    await page.selectOption('#wf-seed-component','7');await page.check('#wf-seed-enabled');
    await page.fill('#wf-seed-xmin','6.2');await page.fill('#wf-seed-xmax','7.2');await page.click('#wf-seed-apply');
    await page.selectOption('#wf-seed-component','1');await page.uncheck('#wf-seed-enabled');
    await page.fill('#wf-rank','3');await page.locator('#wf-rank').blur();
    const options={'wf-preprocessed':true,'wf-local-method':'nmf','wf-local-init':'random','wf-local-negative':'clip_for_decomposition','wf-local-expand':'140',
      'wf-contrast-model':'logistic','wf-contrast-low':'12','wf-contrast-high':'88','wf-contrast-full-y':false,
      'wf-bulk-scope':'global','wf-stripe-rows':'2','wf-core-enabled':true,'wf-region-evidence':'已核对界面；保留草稿参数'};
    await values(page,options);
    await page.click('[data-wf-step="5"]');await page.selectOption('#wf-target','Si');await page.fill('#wf-peak-high','42');
    await page.selectOption('#wf-target','Ge');await page.fill('#wf-peak-high','36');
    await page.click('[data-wf-step="3"]');await page.locator('#wf-local-panel summary').click();
    await page.selectOption('#wf-region','Interface');await page.fill('#wf-roi-ymin','1');await page.fill('#wf-roi-ymax','6');await page.click('#wf-roi-apply');
    await page.waitForFunction(()=>byId('preferences-status').textContent.startsWith('参数已记忆'));
    await page.waitForFunction(()=>!EelsWorkflow.state.busy);await save(page);
    const saved=await page.evaluate(()=>EelsPreferences.snapshot());
    assert.equal(saved.workflow.seeds[7].enabled,true);assert.equal(saved.workflow.fitInputs.Si.fwhm_max,42);
    const file=JSON.parse(fs.readFileSync(path.join(output,'ui_preferences.json'),'utf8'));
    assert.deepEqual(file.snapshot,saved);assert.equal(file.snapshot.controls['wf-init'],'roi');
    const checks=async()=>{
      const actual=await page.evaluate(()=>EelsPreferences.snapshot());
      for(const id of ['fit-mode','wf-init','wf-rank','wf-seed-component','wf-seed-enabled','pca-components','region-fit-enabled','region-fit-model','region-fit-full-y',...Object.keys(options)])
        assert.equal(actual.controls[id],saved.controls[id],id);
      assert.deepEqual(actual.workflow.rois,saved.workflow.rois);
      assert.deepEqual(actual.workflow.seeds,saved.workflow.seeds);
      assert.equal(actual.workflow.fitInputs.Si.fwhm_max,42);assert.equal(actual.workflow.fitInputs.Ge.fwhm_max,36);
      assert.equal(actual.details['wf-local-panel'],true);
    };
    await page.reload();await page.waitForFunction(()=>state.previews.Si&&!state.busy&&!byId('preferences-save').disabled);await checks();
    console.log('Reload passed; restarting fixture');await context.close();server.kill();await closed;await start();
    // New server and fresh browser storage: the file is the source of restored settings.
    context=await browser.newContext({viewport:{width:1440,height:1060}});page=await pageForContext();await checks();
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.decomposition),null,'Restoring parameters does not run an analysis');
    await page.screenshot({path:path.join(output,'restored-options.png'),fullPage:true});
    await page.click('[data-wf-step="1"]');await page.selectOption('#wf-method','mcr_als');await page.selectOption('#wf-init','nndsvda');
    await page.fill('#wf-iterations','60');await page.fill('#wf-starts','1');await page.click('#wf-decompose');
    await page.waitForFunction(()=>EelsWorkflow.state.decomposition&&!EelsWorkflow.state.busy,{},{timeout:30000});
    await page.click('[data-wf-step="4"]');await page.click('#wf-profiles');
    await page.waitForFunction(()=>EelsWorkflow.state.profiles&&!EelsWorkflow.state.busy,{},{timeout:30000});
    await page.click('[data-wf-step="5"]');await page.selectOption('#wf-target','Si');await page.fill('#wf-peak-count','1');
    await page.fill('#wf-peak-high','20');await page.fill('#wf-centers','28');await page.click('#wf-fit');
    await page.waitForFunction(()=>EelsWorkflow.state.fits.Si&&!EelsWorkflow.state.busy,{},{timeout:30000});
    await page.click('[data-wf-step="4"]');await page.selectOption('#wf-strip-view','2');
    await page.waitForFunction(()=>EelsWorkflow.state.strip.index===2);await page.click('[data-wf-step="5"]');
    const completed=await page.evaluate(()=>({decomp:EelsWorkflow.state.decomposition.result_id,profiles:EelsWorkflow.state.profiles.result_id,fit:EelsWorkflow.state.fits.Si.result_id}));
    await page.fill('#wf-peak-high','35');await page.click('[data-wf-step="1"]');await page.selectOption('#wf-init','roi');
    await page.click('[data-wf-step="5"]');await save(page);await page.reload();
    await page.waitForFunction(()=>state.previews.Si&&!state.busy&&!byId('preferences-save').disabled);
    assert.equal(await page.inputValue('#wf-init'),'roi');assert.equal(await page.inputValue('#wf-peak-high'),'35');
    assert.equal(await page.inputValue('#wf-strip-view'),'2');
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.strip.index),2,'Restored strip label and displayed data must match');
    assert.deepEqual(await page.evaluate(()=>({decomp:EelsWorkflow.state.decomposition.result_id,profiles:EelsWorkflow.state.profiles?.result_id,fit:EelsWorkflow.state.fits.Si?.result_id})),completed);
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.fits.Si.settings.fwhm_max),20,'Draft edits never relabel actual completed recipes');
    const exported=await page.evaluate(()=>EelsWorkflow.api('export',{result_ids:{decomposition:EelsWorkflow.state.decomposition.result_id,profiles:EelsWorkflow.state.profiles.result_id},fit_result_ids:{Si:EelsWorkflow.state.fits.Si.result_id}}));
    assert.equal(exported.reproducibility.steps.find(step=>step.stage==='fit').settings.fwhm_max,20);
    await page.route('**/api/ui-preferences',route=>route.request().method()==='POST'?route.fulfill({status:500,json:{error:'模拟保存失败'}}):route.continue());
    await page.fill('#wf-peak-high','34');await page.waitForFunction(()=>byId('preferences-status').textContent.includes('模拟保存失败'));
    assert.equal(await page.evaluate(()=>state.busy),false);await page.unroute('**/api/ui-preferences');await save(page);
    await page.route('**/api/ui-preferences',async route=>{await new Promise(resolve=>setTimeout(resolve,5200));await route.abort().catch(()=>{});});
    await page.fill('#wf-peak-high','33');await page.waitForFunction(()=>byId('preferences-status').textContent.includes('超时'),{},{timeout:10000});
    assert.equal(await page.evaluate(()=>state.busy),false);await page.unroute('**/api/ui-preferences');await save(page);
    const failed=await context.newPage();failed.on('pageerror',error=>errors.push(error.message));
    await failed.route('**/api/ui-preferences',route=>route.fulfill({status:400,json:{error:'模拟参数文件损坏'}}));
    await failed.goto(fixture.url);await failed.waitForFunction(()=>state.previews.Si&&!state.busy);
    assert(await failed.locator('#preferences-save').isDisabled());
    assert((await failed.textContent('#preferences-status')).includes('未覆盖'));await failed.close();
    await context.close();server.kill();await closed;await start();
    const savedFile=path.join(output,'ui_preferences.json'),mismatch=JSON.parse(fs.readFileSync(savedFile,'utf8'));
    mismatch.snapshot.dataset.source='previous-input.h5';fs.writeFileSync(savedFile,JSON.stringify(mismatch),'utf8');
    context=await browser.newContext();page=await pageForContext();
    const changedData=await page.evaluate(()=>EelsPreferences.snapshot());
    assert.equal(changedData.controls['wf-init'],'roi');assert.equal(changedData.controls['wf-preprocessed'],false);
    assert.equal(changedData.workflow.seeds[7].enabled,true,'Enable options carry over without old geometry');
    assert.notDeepEqual(changedData.workflow.seeds[7].roi,saved.workflow.seeds[7].roi);
    assert.equal(changedData.workflow.rois.Interface.y_min,0);
    assert.equal(Number(changedData.controls['wf-roi-ymin']),changedData.workflow.rois[changedData.workflow.region].y_min);
    assert.deepEqual(errors,[]);console.log(JSON.stringify({status:'passed',output}));
  }catch(error){console.error(logs.slice(-2000));throw error;}
  finally{if(browser)await browser.close();if(server){server.kill();await closed;}}
})().catch(error=>{console.error(error);process.exitCode=1;});
