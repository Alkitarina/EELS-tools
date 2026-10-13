// Real ZIP upload/path controls, failed import preservation, and subsequent PCA.
const {artifactPath}=require('./artifacts.cjs');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {chromium}=require('playwright');

(async()=>{
  const root=path.resolve(__dirname,'..'),output=fs.mkdtempSync(artifactPath('.tmp_zest_browser_'));
  let server,closed,browser;
  try{
    server=spawn(path.join(root,'.venv/Scripts/python.exe'),['-B','-X','utf8',path.join(__dirname,'browser_zest_import_server.py'),output],{cwd:root,windowsHide:true});
    closed=once(server,'close');let logs='';server.stderr.on('data',c=>logs+=c);
    const fixture=await new Promise((resolve,reject)=>{
      let text='';const timer=setTimeout(()=>reject(Error(logs||'Fixture timeout')),30000);
      server.once('error',error=>{clearTimeout(timer);reject(error);});
      server.stdout.on('data',chunk=>{text+=chunk;if(text.includes('\n')){clearTimeout(timer);resolve(JSON.parse(text.split('\n')[0]));}});
    });
    browser=await chromium.launch({channel:'chrome',headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1050}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(fixture.url);
    await page.waitForFunction(()=>state.info?.dataset_loaded===false&&!state.busy);
    assert((await page.getAttribute('#import-file','accept')).includes('.zip'));
    await page.click('#import-data');
    assert.equal(await page.isVisible('#zest-import-options'),false);
    await page.setInputFiles('#import-file',fixture.calibrated);
    assert(await page.isVisible('#zest-import-options'));
    assert.equal(await page.inputValue('#zest-signal'),'raw_minus_zlp');
    await page.screenshot({path:path.join(output,'zip-import-dialog.png'),fullPage:true});
    await page.setViewportSize({width:430,height:900});
    assert(await page.evaluate(()=>document.getElementById('import-dialog').scrollWidth<=document.getElementById('import-dialog').clientWidth+1));
    await page.screenshot({path:path.join(output,'zip-import-narrow.png'),fullPage:true});
    await page.setViewportSize({width:1440,height:1050});
    await page.click('#import-submit');
    await page.waitForFunction(()=>state.info?.dataset_loaded&&state.previews.Si&&!state.busy,{},{timeout:30000});
    assert.equal(await page.evaluate(()=>state.info.input_preparation.valid_pixels),248);
    assert((await page.textContent('#dataset-import-note')).includes('实测谱'));
    await page.fill('#pca-components','2');
    await page.click('#fit-active');
    await page.waitForFunction(()=>state.pcaResults.Si&&!state.busy,{},{timeout:30000});
    const result=await page.evaluate(()=>({id:state.info.dataset_id,result:state.pcaResults.Si.result_id}));
    await page.waitForFunction(()=>state.references?.maps.total_counts.available);
    assert((await page.textContent('#dataset-import-note')).includes('已保留原始 EELS 总计数'));
    const beforeCounts=await page.evaluate(()=>JSON.stringify({rois:state.rois,pca:state.pcaResults,previews:state.previews}));
    const countValues=await page.evaluate(()=>state.references.maps.total_counts.values);
    await page.selectOption('#map-kind','total_counts');
    assert.equal(await page.evaluate(()=>state.references.maps.total_counts.palette),'gray');
    await page.check('#region-fit-enabled');
    await page.selectOption('#region-fit-source','total_counts');
    await page.click('#region-fit-run');
    await page.waitForFunction(()=>!byId('region-fit-apply').disabled,{},{timeout:15000});
    assert.equal(await page.evaluate(()=>JSON.stringify({rois:state.rois,pca:state.pcaResults,previews:state.previews})),beforeCounts);
    await page.screenshot({path:path.join(output,'zip-total-counts-contrast.png'),fullPage:true});
    await page.uncheck('#region-fit-enabled');
    await page.selectOption('#fit-mode','multivariate');
    await page.waitForFunction(()=>EelsWorkflow.state.restored);
    await page.fill('#wf-iterations','60');await page.fill('#wf-starts','1');
    await page.check('#wf-preprocessed');await page.click('#wf-decompose');
    await page.waitForFunction(()=>EelsWorkflow.state.decomposition&&!EelsWorkflow.state.busy,{},{timeout:30000});
    assert.deepEqual(await page.evaluate(()=>EelsWorkflow.state.decomposition.maps.total_counts),countValues);
    await page.click('[data-wf-step="3"]');
    const workflowRois=await page.evaluate(()=>JSON.stringify(EelsWorkflow.state.rois));
    await page.selectOption('#wf-background','total_counts');
    await page.locator('#wf-contrast-panel summary').click();
    await page.click('#wf-contrast-run');
    await page.waitForFunction(()=>!byId('wf-contrast-apply').disabled,{},{timeout:15000});
    assert.equal(await page.evaluate(()=>JSON.stringify(EelsWorkflow.state.rois)),workflowRois);
    await page.screenshot({path:path.join(output,'zip-workflow-counts.png'),fullPage:true});
    await page.selectOption('#fit-mode','pca');
    await page.screenshot({path:path.join(output,'zip-analysis.png'),fullPage:true});
    // Missing geometry fails without changing either the dataset or its result.
    await page.click('#import-data');await page.fill('#import-path',fixture.old);
    assert.equal(await page.evaluate(()=>document.getElementById('import-file').files.length),0);
    await page.click('#import-submit');
    await page.waitForFunction(()=>!state.busy&&document.getElementById('import-message').textContent.includes('空间标定'),{},{timeout:30000});
    assert.deepEqual(await page.evaluate(()=>({id:state.info.dataset_id,result:state.pcaResults.Si.result_id})),result);
    assert(await page.isEnabled('#zest-signal'));
    // Quoted absolute path and an explicitly selected model/corrected axis.
    await page.fill('#import-path','"'+fixture.calibrated+'"');
    await page.selectOption('#zest-signal','vib_only');await page.selectOption('#zest-energy-axis','corrected');
    await page.click('#import-submit');
    await page.waitForFunction(()=>!state.busy&&state.info?.input_preparation?.options.signal==='vib_only',{},{timeout:30000});
    assert.equal(await page.evaluate(()=>state.info.input_preparation.options.energy_axis),'corrected');
    assert.equal(await page.evaluate(()=>Object.keys(state.pcaResults).length),0);
    assert((await page.textContent('#dataset-import-note')).includes('模型'));
    await page.waitForFunction(()=>state.references?.maps.total_counts.available);
    assert.deepEqual(await page.evaluate(()=>state.references.maps.total_counts.values),countValues);
    await page.click('#import-data');await page.fill('#import-path','C:\\fixture.h5');
    assert.equal(await page.isVisible('#zest-import-options'),false);
    await page.click('#import-cancel');
    // Optional user-provided calibrated sample: real upload and a fresh ROI analysis.
    if(process.env.EELS_ZEST_EXAMPLE){
      await page.click('#import-data');
      await page.setInputFiles('#import-file',process.env.EELS_ZEST_EXAMPLE);
      await page.selectOption('#zest-signal','raw_minus_zlp');
      await page.selectOption('#zest-energy-axis','raw');
      await page.click('#import-submit');
      await page.waitForFunction(()=>!state.busy&&state.info?.input_preparation?.job_id==='example4pixel',{},{timeout:30000});
      assert.deepEqual(await page.evaluate(()=>state.info.shape),{y:2,x:2,energy:683});
      const information=await page.evaluate(()=>state.info);
      assert(Math.abs(information.x_nm[1]-.4166666567325592)<1e-14);
      assert(Math.abs(information.y_nm[1]-.4166666567325592)<1e-14);
      assert.equal(information.input_preparation.total_counts_status,'available');
      await page.waitForFunction(()=>state.references?.maps.total_counts.available);
      const realCounts=await page.evaluate(()=>state.references.maps.total_counts.values);
      assert.deepEqual(realCounts,fixture.example_counts);
      await page.selectOption('#map-kind','total_counts');
      await page.fill('#pca-components','2');
      await page.click('#fit-active');
      await page.waitForFunction(()=>state.pcaResults.Si&&!state.busy,{},{timeout:30000});
      await page.screenshot({path:path.join(output,'real-calibrated-example.png'),fullPage:true});
      fs.writeFileSync(path.join(output,'real-example-validation.json'),JSON.stringify({info:information,analysis:await page.evaluate(()=>state.pcaResults.Si)},null,2));
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,output,checks:['ZIP upload','quoted local path','signal and energy options','mobile dialog','PCA analysis','atomic failure','model provenance','cleared stale results','H5 controls unchanged','retained source counts','contrast preview preserves ROIs/results','workflow total-count map and contrast preview','counts independent of selected spectrum']}));
  }finally{
    if(browser)await browser.close();
    if(server){server.kill();await closed;}
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
