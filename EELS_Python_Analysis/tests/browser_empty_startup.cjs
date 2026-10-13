// Blank startup and first imports, isolated from all user analysis services.
const {artifactPath}=require('./artifacts.cjs');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {chromium}=require('playwright');

(async()=>{
  const root=path.resolve(__dirname,'..'),output=fs.mkdtempSync(artifactPath('.tmp_empty_browser_'));
  let server,closed,browser;
  async function start(){
    server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8',path.join(__dirname,'browser_empty_startup_server.py'),output],{cwd:root,windowsHide:true});
    closed=once(server,'close');let logs='';server.stderr.on('data',c=>logs+=c);
    return new Promise((resolve,reject)=>{
      let text='';const timer=setTimeout(()=>reject(Error(logs||'Fixture timeout')),30000);
      server.once('error',error=>{clearTimeout(timer);reject(error);});
      server.stdout.on('data',chunk=>{text+=chunk;if(text.includes('\n')){clearTimeout(timer);resolve(JSON.parse(text.split('\n')[0]));}});
    });
  }
  async function stop(){if(server){server.kill();await closed;server=null;}}
  try{
    let fixture=await start();
    const original=fs.readFileSync(fixture.preferences);
    browser=await chromium.launch({channel:'chrome',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[],requests=[];page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(request.url().includes('/api/'))requests.push(request.url());});
    await page.goto(fixture.url);
    await page.waitForFunction(()=>state.info?.dataset_loaded===false&&!state.busy&&EelsWorkflow.state.restored);
    assert(await page.isVisible('#empty-workspace'));
    assert.equal(await page.textContent('#dataset-name'),'尚未导入数据');
    assert.equal(await page.isVisible('#legacy-workspace'),false);
    assert.equal(await page.isVisible('#workflow-workspace'),false);
    assert(await page.isEnabled('#import-data'));assert(await page.isEnabled('#import-workflow'));
    assert(!requests.some(url=>/\/api\/(map|preview|reference-maps|ui-preferences)/.test(url)));
    await page.click('#import-data');await page.click('#import-cancel');
    await page.reload();await page.waitForFunction(()=>state.info?.dataset_loaded===false&&!state.busy);
    assert.deepEqual(fs.readFileSync(fixture.preferences),original);
    await page.screenshot({path:path.join(output,'empty-ui.png'),fullPage:true});
    // An invalid JSON cannot turn the empty workspace into a broken analysis page.
    await page.click('#import-workflow');
    await page.setInputFiles('#workflow-import-file',{name:'invalid.json',mimeType:'application/json',buffer:Buffer.from('{broken')});
    await page.click('#workflow-import-submit');
    await page.waitForFunction(()=>document.getElementById('workflow-import-message').textContent.includes('无法读取'));
    assert.equal(await page.evaluate(()=>state.info.dataset_loaded),false);
    await page.click('#workflow-import-close');
    assert.deepEqual(fs.readFileSync(fixture.preferences),original);
    // First H5 import initializes axes/ROIs and restores appropriate preferences.
    await page.click('#import-data');await page.setInputFiles('#import-file',fixture.path);await page.click('#import-submit');
    await page.waitForFunction(()=>state.info.dataset_loaded&&state.previews.Si&&!state.busy,{},{timeout:30000});
    assert.equal(await page.inputValue('#pca-components'),'2');
    assert(await page.isVisible('#legacy-workspace'));
    await page.click('#fit-active');await page.waitForFunction(()=>state.pcaResults.Si&&!state.busy,{},{timeout:30000});
    assert.equal(await page.isVisible('#empty-workspace'),false);
    await page.evaluate(()=>EelsPreferences.save(true));
    await stop();fixture=await start();
    const savedDraft=fs.readFileSync(fixture.preferences);
    await page.goto(fixture.url);await page.waitForFunction(()=>state.info?.dataset_loaded===false&&!state.busy);
    assert.deepEqual(fs.readFileSync(fixture.preferences),savedDraft);
    // JSON can be the first import after restart; old drafts must not override its recipe.
    await page.click('#import-workflow');await page.setInputFiles('#workflow-import-file',fixture.workflow);await page.click('#workflow-import-submit');
    await page.waitForFunction(()=>state.info.dataset_loaded&&EelsWorkflow.state.fits.Si&&!state.busy,{},{timeout:45000});
    assert(await page.isVisible('#workflow-workspace'));
    assert.equal(await page.inputValue('#fit-mode'),'multivariate');
    assert.equal(await page.inputValue('#wf-rank'),'3');
    assert.equal(await page.inputValue('#wf-peak-count'),'1');
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.fits.Si.restoration),'saved_parameters_v1');
    await page.click('#wf-fit');await page.waitForFunction(()=>!EelsWorkflow.state.busy&&!EelsWorkflow.state.fits.Si.restoration);
    await page.screenshot({path:path.join(output,'json-first-import.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,output,checks:['empty startup','no data requests','preferences unchanged before import','first H5 and PCA','restart empty','first JSON and refit']}));
  }finally{if(browser)await browser.close();await stop();}
})().catch(error=>{console.error(error);process.exitCode=1;});
