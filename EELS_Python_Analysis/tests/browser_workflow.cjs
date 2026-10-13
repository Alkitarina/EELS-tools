const {artifactPath} = require('./artifacts.cjs');
// All writes and analyses use this test's synthetic fixture, never an active user session.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

(async () => {
  const root=path.resolve(__dirname,'..');
  const output=fs.mkdtempSync(artifactPath('.tmp_workflow_browser_'));
  const server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-X','utf8',path.join(__dirname,'browser_workflow_server.py'),output],{cwd:root,windowsHide:true});
  const closed=once(server,'close');let logs='',browser;
  server.stderr.on('data',c=>logs+=c);
  try {
    const fixture=await new Promise((resolve,reject)=>{
      let text='';const timer=setTimeout(()=>reject(Error(logs||'Fixture timeout')),30000);
      server.once('error',error=>{clearTimeout(timer);reject(error);});
      server.stdout.on('data',chunk=>{text+=chunk;if(text.includes('\n')){clearTimeout(timer);resolve(JSON.parse(text.split('\n')[0]));}});
    });
    browser=await chromium.launch({channel:'chrome',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1060},acceptDownloads:true});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(fixture.url);
    await page.waitForFunction(()=>state.info&&state.previews.Si&&!state.busy);
    assert.equal(await page.locator('#fit-mode').count(),1);
    assert((await page.locator('#fit-mode').boundingBox()).y<(await page.locator('.workspace').boundingBox()).y);
    await page.fill('#pca-components','3');await page.click('#fit-active');
    await page.waitForFunction(()=>state.pcaResults.Si&&!state.busy);
    const legacy=await page.evaluate(()=>JSON.stringify({rois:state.rois,pca:state.pcaResults}));
    await page.selectOption('#fit-mode','multivariate');
    await page.waitForFunction(()=>EelsWorkflow.state.restored);
    assert.equal(await page.isVisible('#legacy-workspace'),false);
    assert.equal(await page.locator('.wf-step:visible').count(),1);
    await page.fill('#wf-iterations','60');await page.fill('#wf-starts','1');
    await page.click('#wf-decompose');
    await page.waitForFunction(()=>!EelsWorkflow.state.busy&&byId('wf-status').textContent.includes('确认'));
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.decomposition),null);
    await page.check('#wf-preprocessed');await page.click('#wf-decompose');
    await page.waitForFunction(()=>EelsWorkflow.state.decomposition&&!EelsWorkflow.state.busy,{},{timeout:30000});
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.step),2);
    await page.screenshot({path:path.join(output,'02-components.png'),fullPage:true});
    const decompId=await page.evaluate(()=>EelsWorkflow.state.decomposition.result_id);
    await page.click('[data-wf-step="3"]');await page.check('#wf-core-enabled');
    const rois=await page.evaluate(()=>JSON.stringify(EelsWorkflow.state.rois));
    for(const kind of ['component_0','total_counts','reconstruction_error','integral']){
      await page.selectOption('#wf-background',kind);
      assert.equal(await page.evaluate(()=>JSON.stringify(EelsWorkflow.state.rois)),rois);
    }
    await page.click('#wf-roi-preview');await page.waitForFunction(()=>EelsWorkflow.state.roiPreview);
    await page.screenshot({path:path.join(output,'03-regions.png'),fullPage:true});
    await page.click('[data-wf-step="4"]');await page.fill('#wf-stripe-rows','2');
    await page.locator('#wf-alignment-details summary').click();await page.check('#wf-align');
    await page.fill('#wf-anchors','0, 7.15\n7, 7.85');
    await page.click('#wf-profiles');
    await page.waitForFunction(()=>EelsWorkflow.state.profiles&&!EelsWorkflow.state.busy,{},{timeout:30000});
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.profiles.strips.length),4);
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.profiles.alignment.axis),'distance_nm');
    await page.selectOption('#wf-strip-kind','residual');
    await page.screenshot({path:path.join(output,'04-stripes.png'),fullPage:true});
    await page.click('[data-wf-step="5"]');await page.selectOption('#wf-target','Core');
    await page.click('#wf-preview');await page.waitForFunction(()=>EelsWorkflow.state.preview);
    await page.fill('#wf-peak-count','1');await page.click('#wf-fit');
    await page.waitForFunction(()=>EelsWorkflow.state.fit&&!EelsWorkflow.state.busy,{},{timeout:30000});
    const peak=await page.evaluate(()=>EelsWorkflow.state.fit.joint.centers_meV[0]);
    assert(Math.abs(peak-46)<.15,`Recovered interface center: ${peak}`);
    assert(await page.evaluate(()=>EelsWorkflow.state.fit.residual.some(value=>value<0)));
    await page.screenshot({path:path.join(output,'05-residual.png'),fullPage:true});
    const saved=await page.evaluate(()=>JSON.stringify({rois:EelsWorkflow.state.rois,decomp:EelsWorkflow.state.decomposition.result_id,profiles:EelsWorkflow.state.profiles.result_id,fit:EelsWorkflow.state.fit.result_id}));
    const [download]=await Promise.all([page.waitForEvent('download'),page.click('#wf-export')]);
    assert.equal(download.suggestedFilename(),'synthetic.json');
    await download.saveAs(path.join(output,'synthetic.json'));
    const document=JSON.parse(fs.readFileSync(path.join(output,'synthetic.json'),'utf8'));
    assert.equal(document.format,'eels_interface_workflow_v1');assert.equal(document.reproducibility.steps.length,3);
    const second=await browser.newPage({viewport:{width:1440,height:1060}});
    await second.goto(fixture.url);await second.waitForFunction(()=>state.info&&!state.busy&&state.previews.Si);
    await second.selectOption('#fit-mode','multivariate');
    await second.waitForFunction(()=>EelsWorkflow.state.fit);
    assert.equal(await second.inputValue('#wf-target'),'Core','Reload restores the actual fitted target');
    await second.close();
    await page.selectOption('#fit-mode','pca');
    assert.equal(await page.isVisible('#workflow-workspace'),false);
    assert.equal(await page.evaluate(()=>JSON.stringify({rois:state.rois,pca:state.pcaResults})),legacy);
    await page.selectOption('#fit-mode','auto');assert.equal(await page.isVisible('#pca-fields'),false);
    assert.equal(await page.isVisible('#auto-fields'),true);
    await page.selectOption('#fit-mode','multivariate');
    assert.equal(await page.evaluate(()=>JSON.stringify({rois:EelsWorkflow.state.rois,decomp:EelsWorkflow.state.decomposition.result_id,profiles:EelsWorkflow.state.profiles.result_id,fit:EelsWorkflow.state.fit.result_id})),saved);
    await page.click('[data-wf-step="3"]');
    await page.locator('#wf-region-map').scrollIntoViewIfNeeded();
    const points=await page.evaluate(()=>{const w=EelsWorkflow.state,r=w.rois.Si,g=w.geometry,b=byId('wf-region-map').getBoundingClientRect();return {a:g.p((r.x_min+r.x_max)/2,(r.y_min+r.y_max)/2),left:b.left,top:b.top};});
    await page.mouse.move(points.left+points.a.x,points.top+points.a.y);await page.mouse.down();
    await page.mouse.move(points.left+points.a.x+15,points.top+points.a.y,{steps:4});await page.keyboard.press('Escape');await page.mouse.up();
    assert.equal(await page.evaluate(()=>JSON.stringify({rois:EelsWorkflow.state.rois,decomp:EelsWorkflow.state.decomposition.result_id,profiles:EelsWorkflow.state.profiles.result_id,fit:EelsWorkflow.state.fit.result_id})),saved,'Esc preserves dependent analyses');
    await page.fill('#wf-roi-xmax','3.4');await page.click('#wf-roi-apply');
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.profiles),null);
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.decomposition.result_id),decompId);
    await page.click('[data-wf-step="1"]');await page.fill('#wf-starts','12');await page.fill('#wf-iterations','2000');
    await page.click('#wf-decompose');await page.waitForFunction(()=>EelsWorkflow.state.job);
    await page.click('#wf-cancel');await page.waitForFunction(()=>!EelsWorkflow.state.busy,{},{timeout:15000});
    assert.equal(await page.evaluate(()=>EelsWorkflow.state.decomposition.result_id),decompId,'Cancelled replacement preserves decomposition');
    await page.setViewportSize({width:390,height:844});
    for(const step of [1,2,3,4,5]){await page.click(`[data-wf-step="${step}"]`);await page.waitForTimeout(80);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),`No horizontal overflow at step ${step}`);}
    await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',peak_center_meV:peak,output}));
  }catch(error){console.error(logs.slice(-3000));throw error;}
  finally{if(browser)await browser.close();server.kill();await closed;}
})().catch(error=>{console.error(error);process.exitCode=1;});
