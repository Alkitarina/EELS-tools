const {artifactPath} = require('./artifacts.cjs');
// Dedicated ephemeral QA service, never the user's active analysis session.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const output = fs.mkdtempSync(artifactPath('.tmp_reference_browser_'));
  const server = spawn(path.join(root, '.venv', 'Scripts', 'python.exe'),
    ['-X', 'utf8', path.join(__dirname, 'browser_reference_maps_server.py'), output],
    { cwd: root, windowsHide: true });
  const closed = once(server, 'close');
  let logs = '';
  server.stderr.on('data', chunk => logs += chunk);
  let browser;
  try {
    const fixture = await new Promise((resolve, reject) => {
      let data = '';
      const timer = setTimeout(() => reject(new Error('Fixture timeout: '+logs)),30000);
      server.once('error', error => { clearTimeout(timer); reject(error); });
      server.once('exit', () => { clearTimeout(timer); reject(new Error(logs)); });
      server.stdout.on('data', chunk => {
        data += chunk;
        if (!data.includes('\n')) return;
        clearTimeout(timer);
        try { resolve(JSON.parse(data.split('\n')[0])); } catch(error) { reject(error); }
      });
    });
    browser = await chromium.launch({ channel:'chrome',headless:true });
    const page = await browser.newPage({ viewport:{ width:1440,height:1050 } });
    const errors=[];
    let previews=0;
    page.on('pageerror', error=>errors.push(error.message));
    page.on('request', request=> { if(request.url().endsWith('/api/preview'))previews++; });
    await page.goto(fixture.url);
    await page.waitForFunction(()=>state.references && state.previews.Si);
    assert.equal(await page.locator('#candidate-readout span').count(),2);
    const point=async(x,y)=>page.evaluate(({x,y})=> {
      const p=mapPoint(x,y),r=byId('map-canvas').getBoundingClientRect();
      return {x:p.x+r.left,y:p.y+r.top};
    },{x,y});
    for(const [region,coords] of [['Si',[0.6,2,3.7,10]],['Interface',[7,5,8.8,13]],['Ge',[12,7,14.7,17]]]) {
      await page.check(`input[name="region"][value="${region}"]`);
      await page.waitForFunction(r=>state.previews[r],region);
      // Exercise real rectangle interaction to retain non-default selections.
      const from=await point(coords[0],coords[1]),to=await point(coords[2],coords[3]);
      await page.mouse.move(from.x,from.y);
      await page.mouse.down();
      await page.mouse.move(to.x,to.y,{steps:4});
      await page.mouse.up();
      await page.waitForFunction(r=>!state.drag && roiSignature(state.previews[r]?.roi)===roiSignature(state.rois[r]),region);
    }
    await page.fill('#pca-components','2');
    await page.fill('#pca-peak-count','2');
    await page.click('#fit-active');
    await page.waitForFunction(()=>!state.busy && state.pcaResults.Ge);
    const snapshot=()=>page.evaluate(()=>JSON.stringify({rois:state.rois,active:state.activeRegion,
      previews:state.previews,fits:state.fits,pca:state.pcaResults,
      controls:Array.from(document.querySelectorAll('.controls-band input,.controls-band select'),e=>[e.id,e.value,e.checked])}));
    const before=await snapshot(),previewCount=previews;
    for(const kind of ['total_counts','band_27_32','spectral_contribution','signal','spectral_contribution','total_counts','signal']) {
      await page.selectOption('#map-kind',kind);
      await page.waitForFunction(k=>state.mapKind===k,kind);
      assert.equal(await snapshot(),before,'Display switching must preserve ALL selected ROIs, inputs, previews and fitted results');
    }
    assert.equal(previews,previewCount,'No spectrum preview calls when switching reference maps');
    await page.selectOption('#map-kind','total_counts');
    const visible=await page.locator('#map-canvas').evaluate(canvas=>canvas.toDataURL());
    await page.uncheck('#show-centers');
    const hidden=await page.locator('#map-canvas').evaluate(canvas=>canvas.toDataURL());
    assert.notEqual(visible,hidden,'Candidate overlays visibly toggle');
    assert.equal(await snapshot(),before);
    await page.check('#show-centers');
    await page.screenshot({path:path.join(output,'desktop.png'),fullPage:true});

    const beforeRois=await page.evaluate(()=>JSON.stringify(state.rois));
    await page.click('#import-data');
    await page.fill('#import-path',fixture.paths.legacy);
    await page.click('#import-submit');
    await page.waitForFunction(()=>!state.busy && state.references && !state.references.maps.total_counts.available);
    assert.equal(await page.evaluate(()=>JSON.stringify(state.rois)),beforeRois,'Same-grid import preserves ROIs');
    const missingBefore=await snapshot();
    await page.selectOption('#map-kind','total_counts');
    await page.waitForFunction(()=>byId('reference-note').classList.contains('error'));
    assert.equal(await page.inputValue('#map-kind'),'signal');
    assert.equal(await snapshot(),missingBefore,'Missing reference map leaves selections and previews intact');

    // A slow initial reference request must not overwrite the user's latest choice.
    const raced=await browser.newPage({viewport:{width:1440,height:1050}});
    let release;
    const gate=new Promise(resolve=>release=resolve);
    await raced.route('**/api/reference-maps',async route=>{await gate;await route.continue();});
    await raced.goto(fixture.url);
    await raced.waitForFunction(()=>Boolean(state.signalMap && state.referenceRequest));
    const raceRois=await raced.evaluate(()=>JSON.stringify(state.rois));
    await raced.selectOption('#map-kind','spectral_contribution');
    await raced.selectOption('#map-kind','band_27_32');
    await raced.selectOption('#map-kind','signal');
    release();
    await raced.waitForFunction(()=>state.references && !state.referenceRequest);
    assert.equal(await raced.evaluate(()=>state.mapKind),'signal');
    assert.equal(await raced.evaluate(()=>JSON.stringify(state.rois)),raceRois);
    await raced.close();

    const mobile=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:2});
    await mobile.goto(fixture.url);
    await mobile.waitForFunction(()=>state.references && state.previews[state.activeRegion]);
    await mobile.selectOption('#map-kind','spectral_contribution');
    await mobile.waitForFunction(()=>state.mapKind==='spectral_contribution');
    assert.equal(await mobile.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await mobile.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({switches:7,allSelectionsAndAnalysesPreserved:true,missingMapPreserved:true,errors,output},null,2));
  } finally {
    if(browser)await browser.close();
    server.kill();
    await closed;
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
