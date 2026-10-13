const {artifactPath} = require('./artifacts.cjs');
// All mutations target this test's isolated service and generated fixture data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const output = fs.mkdtempSync(artifactPath('.tmp_reference_browser_regionfit_'));
  const server = spawn(path.join(root,'.venv/Scripts/python.exe'),
    ['-X','utf8',path.join(__dirname,'browser_reference_maps_server.py'),output],{cwd:root,windowsHide:true});
  const closed = once(server,'close');
  let logs='', browser;
  server.stderr.on('data',c=>logs+=c);
  try {
    const fixture = await new Promise((resolve,reject)=>{
      let data=''; const timer=setTimeout(()=>reject(new Error(logs||'Fixture timeout')),30000);
      server.once('error',e=>{clearTimeout(timer);reject(e);});
      server.stdout.on('data',c=>{data+=c;if(data.includes('\n')){clearTimeout(timer);resolve(JSON.parse(data.split('\n')[0]));}});
    });
    browser = await chromium.launch({channel:'chrome',headless:true});
    const page = await browser.newPage({viewport:{width:1440,height:1050}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    await page.goto(fixture.url);
    await page.waitForFunction(()=>state.references && state.previews.Si && !state.busy);
    assert.equal(await page.isChecked('#region-fit-enabled'),false);
    assert.equal(await page.isVisible('#region-fit-controls'),false);
    await page.fill('#pca-components','2');
    await page.click('#fit-active');
    await page.waitForFunction(()=>state.pcaResults.Si && !state.busy);
    const snapshot=()=>page.evaluate(()=>JSON.stringify({rois:state.rois,previews:state.previews,fits:state.fits,pca:state.pcaResults,active:state.activeRegion}));
    const before=await snapshot();
    await page.check('#region-fit-enabled');
    await page.click('#region-fit-run');
    await page.waitForFunction(()=>!byId('region-fit-apply').disabled);
    assert.equal(await snapshot(),before,'Preview must not change ROIs or analyses');
    assert.equal(await page.locator('#region-fit-models tr').count(),2);
    for(const model of ['logistic','erf']){
      await page.selectOption('#region-fit-model',model);
      assert.equal(await snapshot(),before,'Model switch is preview only');
    }
    await page.click('#region-fit-apply');
    await page.waitForFunction(()=>state.previews.Si && !state.pcaResults.Si);
    const applied=await page.evaluate(()=>structuredClone(state.rois));
    assert(Math.abs(applied.Interface.x_min-(8.6-1.2815515655*.8))<1e-5);
    assert(Math.abs(applied.Ge.x_min-(8.6+2.326347874*.8))<1e-5);
    for(const roi of Object.values(applied)){assert.equal(roi.y_min,0);assert.equal(roi.y_max,20);}
    const pixel=async(x,y)=>page.evaluate(({x,y})=>{const p=mapPoint(x,y),r=byId('map-canvas').getBoundingClientRect();return{x:r.left+p.x,y:r.top+p.y};},{x,y});
    await page.locator('#map-canvas').scrollIntoViewIfNeeded();
    const sx=(applied.Si.x_min+applied.Si.x_max)/2;
    const from=await pixel(sx,0),to=await pixel(sx,2);
    await page.mouse.move(from.x,from.y);await page.mouse.down();await page.mouse.move(to.x,to.y,{steps:4});await page.mouse.up();
    await page.waitForFunction(()=>state.rois.Si.y_min>1 && roiSignature(state.previews.Si?.roi)===roiSignature(state.rois.Si));
    const manual=await snapshot();
    for(const kind of ['total_counts','spectral_contribution','band_27_32','signal']){
      await page.selectOption('#map-kind',kind);
      await page.waitForFunction(k=>state.mapKind===k,kind);
      assert.equal(await snapshot(),manual,'Background switching must preserve manually adjusted fitted regions');
    }
    await page.fill('#region-fit-si','98');await page.fill('#region-fit-ge','98');
    await page.fill('#region-fit-low','20');await page.fill('#region-fit-high','80');
    assert(await page.isDisabled('#region-fit-apply'));
    assert.equal(await snapshot(),manual,'Changing percentages must not apply automatically');
    await page.click('#region-fit-run');await page.waitForFunction(()=>!byId('region-fit-apply').disabled);
    await page.uncheck('#region-fit-full-y');
    await page.click('#region-fit-apply');
    await page.waitForFunction(()=>state.previews.Si && roiSignature(state.previews.Si.roi)===roiSignature(state.rois.Si));
    assert.equal(await page.evaluate(()=>state.rois.Si.y_min),JSON.parse(manual).rois.Si.y_min);
    const preserved=await snapshot();
    await page.fill('#region-fit-si','100');await page.click('#region-fit-run');
    await page.locator('#region-fit-status.error').waitFor();
    assert.equal(await snapshot(),preserved,'Invalid thresholds preserve selections and analyses');
    assert(await page.isEnabled('#region-fit-run'));
    await page.fill('#region-fit-si','98');
    const validReply = await (await page.request.post(fixture.url+'/api/region-fit',{data:{source:'total_counts'}})).json();
    await page.route('**/api/region-fit',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({...validReply,dataset_id:'stale-dataset'})}));
    await page.click('#region-fit-run');await page.locator('#region-fit-status').filter({hasText:'本次拟合未采用'}).waitFor();
    assert.equal(await snapshot(),preserved);assert(await page.isDisabled('#region-fit-apply'));
    await page.unroute('**/api/region-fit');
    await page.route('**/api/region-fit',route=>route.fulfill({status:504,contentType:'application/json',body:JSON.stringify({error:'模拟后端超时'})}));
    await page.click('#region-fit-run');await page.locator('#region-fit-status').filter({hasText:'模拟后端超时'}).waitFor();
    assert.equal(await snapshot(),preserved);assert(await page.isEnabled('#region-fit-run'));
    await page.unroute('**/api/region-fit');
    let held;
    await page.route('**/api/region-fit',route=>{held=route;});
    await page.click('#region-fit-run');await page.waitForFunction(()=>!byId('region-fit-cancel').disabled);
    await page.click('#region-fit-cancel');
    assert.equal(await snapshot(),preserved);assert(await page.isEnabled('#region-fit-run'));
    await held.fulfill({status:500,contentType:'application/json',body:'{"error":"late"}'}).catch(()=>{});
    assert((await page.textContent('#region-fit-status')).includes('已取消'));
    held=null;
    await page.click('#region-fit-run');
    await page.locator('#region-fit-status').filter({hasText:'等待拟合响应超时'}).waitFor({timeout:16000});
    assert.equal(await snapshot(),preserved);assert(await page.isEnabled('#region-fit-run'));
    await held.abort().catch(()=>{});await page.unroute('**/api/region-fit');
    await page.click('#region-fit-run');await page.waitForFunction(()=>!byId('region-fit-apply').disabled);
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.uncheck('#region-fit-enabled');assert.equal(await snapshot(),preserved);
    assert.equal(await page.isVisible('#region-fit-controls'),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({output,previewPreserved:true,manualAfterFit:true,thresholdsApplied:true,cancelAndTimeoutRecovered:true,errors}));
  } finally {if(browser)await browser.close();server.kill();await closed;}
})().catch(e=>{console.error(e);process.exitCode=1;});
