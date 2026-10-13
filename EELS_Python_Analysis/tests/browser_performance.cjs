const {artifactPath} = require('./artifacts.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const output = artifactPath('.tmp_bootstrap_performance');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.EELS_TEST_URL || 'http://127.0.0.1:8771');
    await page.waitForFunction(() => Boolean(state.previews.Si));
    assert.equal(await page.inputValue('#samples'), '500');
    assert.equal(await page.inputValue('#bootstrap-workers'), '0');
    assert(await page.locator('#bootstrap-workers').isVisible());
    const started = page.waitForResponse(response => response.url().endsWith('/api/pca/bootstrap'));
    const start = Date.now();
    await page.click('#bootstrap-active');
    const response = await started;
    assert.equal(response.status(), 200);
    const job = await response.json();
    assert.equal(job.total, 500);
    assert.equal(job.backend, 'thread');
    await page.waitForFunction(() => !state.busy && Boolean(state.pcaResults.Si?.bootstrap), null, { timeout: 120000 });
    const elapsedSeconds = (Date.now() - start) / 1000;
    assert.equal(await page.evaluate(() => state.pcaResults.Si.bootstrap.requested), 500);
    const downloadEvent = page.waitForEvent('download');
    await page.click('#download-results');
    const download = await downloadEvent;
    const exportPath = path.join(output, 'ui-500-results.json');
    await download.saveAs(exportPath);
    const document = JSON.parse(fs.readFileSync(exportPath, 'utf8'));
    const recipe = document.reproducibility.analyses[0].bootstrap;
    assert.equal(recipe.n_resamples, 500);
    assert.equal(recipe.workers, job.workers);
    assert.equal(recipe.rng_scheme, 'seedsequence_per_resample_v1');
    assert.equal(recipe.blas_threads, 1);
    await page.screenshot({ path: path.join(output, 'ui-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(output, 'ui-mobile.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ elapsedSeconds, job, exportPath, errors }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
