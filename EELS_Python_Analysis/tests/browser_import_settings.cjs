// Starts its own ephemeral QA server; never imports into a user's running session.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const output = fs.mkdtempSync(path.join(root, '.tmp_import_settings_'));
  const server = spawn(path.join(root, '.venv', 'Scripts', 'python.exe'),
    ['-X', 'utf8', path.join(__dirname, 'browser_import_settings_server.py'), output],
    { cwd: root, windowsHide: true });
  const closed = once(server, 'close');
  let log = '';
  server.stderr.on('data', chunk => { log += chunk; });
  let browser;
  try {
    const fixture = await new Promise((resolve, reject) => {
      let data = '';
      const timer = setTimeout(() => reject(new Error('Fixture startup timed out: ' + log)), 30000);
      server.once('error', error => { clearTimeout(timer); reject(error); });
      server.once('exit', () => { clearTimeout(timer); reject(new Error(log)); });
      server.stdout.on('data', chunk => {
        data += chunk;
        if (!data.includes('\n')) return;
        clearTimeout(timer);
        try { resolve(JSON.parse(data.split('\n')[0])); } catch (error) { reject(error); }
      });
    });
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(fixture.url);
    await page.waitForFunction(() => Boolean(state.previews.Si));
    assert.equal(await page.inputValue('#fit-min'), '0');
    assert.equal(await page.inputValue('#fit-max'), '120');
    assert.equal(await page.inputValue('#fwhm-lower'), '6.00');
    assert.equal(await page.inputValue('#samples'), '500');

    const controls = () => page.evaluate(() => Array.from(
      document.querySelectorAll('.controls-band input, .controls-band select'),
      element => ({ id: element.id, name: element.name, value: element.value, checked: element.checked })
    ));
    async function importFile(name, upload = false) {
      const oldId = await page.evaluate(() => state.info.dataset_id);
      await page.click('#import-data');
      if (upload) await page.setInputFiles('#import-file', fixture.paths[name]);
      else await page.fill('#import-path', fixture.paths[name]);
      await page.click('#import-submit');
      await page.waitForFunction(id => !state.busy && state.info.dataset_id !== id, oldId);
      assert.equal(await page.locator('#import-dialog').evaluate(dialog => dialog.open), false);
    }

    await page.fill('#fit-min', '10');
    await page.fill('#fit-max', '80');
    await page.selectOption('#fit-mode', 'auto');
    await page.fill('#peak-count', '2');
    await page.fill('#search-starts', '8');
    await page.fill('#auto-fwhm-lower', '3.5');
    await page.fill('#auto-fwhm-upper', '25');
    await page.uncheck('#baseline');
    await page.selectOption('#fit-mode', 'manual');
    for (const [id, value] of Object.entries({ centers: '35, 60', 'center-lower': '28, 54',
      'center-upper': '43, 68', 'fwhm-lower': '3, 4', 'fwhm-upper': '22, 25' })) {
      await page.fill('#' + id, value);
    }
    await page.selectOption('#fit-mode', 'pca');
    await page.fill('#pca-components', '2');
    await page.fill('#pca-peak-count', '2');
    await page.fill('#pca-min-distance', '3');
    await page.fill('#pca-min-prominence', '1.2');
    await page.fill('#samples', '4');
    await page.fill('#block-size', '1');
    await page.selectOption('#bootstrap-workers', '1');
    const rois = {
      Si: { x_min: 1, x_max: 3, y_min: 1, y_max: 5 },
      Interface: { x_min: 4, x_max: 6, y_min: 1, y_max: 5 },
      Ge: { x_min: 7, x_max: 9, y_min: 1, y_max: 5 },
    };
    await page.evaluate(value => { state.rois = value; }, rois);
    await page.click('#update-map');
    await page.waitForFunction(() => state.map.fit_min === 10 && Boolean(state.previews.Si));
    await page.click('#bootstrap-active');
    await page.waitForFunction(() => !state.busy && Boolean(state.pcaResults.Si?.bootstrap));
    await page.fill('#samples', '123');
    await page.click('#toggle-raw');
    const saved = await controls();
    const oldIntensity = await page.evaluate(() => state.previews.Si.intensity);
    await importFile('compatible', true);
    assert.deepEqual(await controls(), saved);
    assert.deepEqual(await page.evaluate(() => state.rois), rois);
    assert.equal(await page.evaluate(() => state.curveVisibility.raw), false);
    assert.deepEqual(await page.evaluate(() => Object.keys(state.pcaResults)), []);
    assert.deepEqual(await page.evaluate(() => Object.keys(state.fits)), []);
    assert.deepEqual(await (await page.request.get(fixture.url + '/api/pca/results')).json(), {});
    assert.notDeepEqual(await page.evaluate(() => state.previews.Si.intensity), oldIntensity);
    assert.equal(await page.textContent('#progress-label'), '空闲');
    assert((await page.textContent('#status')).includes('沿用上一次的设置'));

    // Gaussian mode and fit provenance are preserved only for the old data on failure.
    await page.selectOption('#fit-mode', 'manual');
    await page.click('#fit-active');
    await page.waitForFunction(() => !state.busy && Boolean(state.fits.Si));
    const beforeFailure = await page.evaluate(() => JSON.stringify(state));
    await page.click('#import-data');
    await page.fill('#import-path', fixture.paths.invalid);
    await page.click('#import-submit');
    await page.waitForFunction(() => !state.busy && document.querySelector('#status').classList.contains('error'));
    const failedState = await page.evaluate(() => JSON.parse(JSON.stringify(state)));
    const previousState = JSON.parse(beforeFailure);
    for (const key of ['info', 'map', 'rois', 'fits', 'pcaResults', 'previews']) {
      assert.deepEqual(failedState[key], previousState[key], key);
    }
    await page.click('#import-cancel');
    const manualSettings = await controls();
    await importFile('partial');
    assert.deepEqual(await controls(), manualSettings);
    assert.deepEqual(await page.evaluate(() => state.fits), {});
    assert((await page.textContent('#status')).includes('仅使用交集'));
    assert.equal(await page.evaluate(() => state.previews.Si.energy_meV[0]), 20);
    assert.equal(await page.evaluate(() => state.previews.Si.energy_meV.at(-1)), 70);

    // Empty and nonuniform energy selections never trigger a silent window reset.
    for (const name of ['disjoint', 'sparse']) {
      await importFile(name);
      assert.deepEqual(await controls(), manualSettings);
      assert((await page.textContent('#status')).includes('不足 8 个通道'));
      assert(await page.locator('#status').evaluate(element => element.classList.contains('error')));
      assert.equal(await page.evaluate(() => state.map), null);
      assert.deepEqual(await page.evaluate(() => state.previews), {});
      assert.equal(await page.evaluate(() => {
        const c = document.getElementById('map-canvas');
        return c.getContext('2d').getImageData(0, 0, c.width, c.height).data.some(v => v !== 0);
      }), false);
    }
    await page.fill('#fit-min', '90');
    await page.fill('#fit-max', '110');
    await page.click('#update-map');
    await page.waitForFunction(() => Boolean(state.previews.Si));
    await page.fill('#fit-min', '10');
    await page.fill('#fit-max', '80');
    await importFile('spatial');
    assert.deepEqual(await controls(), manualSettings);
    const adjusted = await page.evaluate(() => state.rois);
    for (const roi of Object.values(adjusted)) {
      assert(roi.x_min >= 20 && roi.x_max <= 30);
      assert.equal(roi.y_min, 2);
      assert.equal(roi.y_max, 4);
    }
    assert((await page.textContent('#status')).includes('请确认位置'));
    await page.click('#toggle-raw');
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1050 });
      await page.waitForTimeout(200);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(output, `preserved-${width}.png`), fullPage: true });
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ output, passed: true, errors }));
  } finally {
    if (browser) await browser.close();
    server.kill();
    await closed;
    fs.writeFileSync(path.join(output, 'server.log'), log);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
