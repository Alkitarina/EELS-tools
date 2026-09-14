// Run against an idle local test server; this switches its active data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const output = fs.mkdtempSync(path.join(root, '.tmp_browser_import_export_'));
  assert(process.argv[2], 'Pass a trusted MAT sample path.');
  const sample = path.resolve(process.argv[2]);
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  try {
    await page.goto(process.env.EELS_TEST_URL || 'http://127.0.0.1:8765');
    await page.locator('#status').filter({ hasText: '预览已更新' }).waitFor();
    await page.click('#import-data');
    await page.fill('#import-path', sample);
    await page.screenshot({ path: path.join(output, 'import-desktop.png'), fullPage: true });
    await page.click('#import-submit');
    await page.locator('#status').filter({ hasText: '数据导入完成' }).waitFor({ timeout: 180000 });
    assert((await page.textContent('#dataset-shape')).includes('2753'));
    await page.fill('#pca-components', '3');
    await page.fill('#pca-peak-count', '2');
    await page.click('#fit-all');
    await page.locator('#status').filter({ hasText: '区域的 PCA 峰分析' }).waitFor({ timeout: 90000 });
    await page.fill('#samples', '4');
    await page.click('#bootstrap-all');
    await page.locator('#status').filter({ hasText: /^已完成.*区域的不确定度计算/ }).waitFor({ timeout: 180000 });
    assert(await page.isEnabled('#download-results'));
    const downloadReady = page.waitForEvent('download', { timeout: 90000 });
    await page.click('#download-results');
    const download = await downloadReady;
    assert.equal(download.suggestedFilename(), path.parse(sample).name + '.json');
    const resultPath = path.join(output, 'real-sample-results.json');
    await download.saveAs(resultPath);
    const result = JSON.parse(fs.readFileSync(resultPath, 'utf8'));
    assert.equal(result.format, 'eels_peak_results_v1');
    assert.equal(result.results.length, 3);
    assert.equal(Object.keys(result).at(-1), 'reproducibility');
    assert(!('intensity' in result.reproducibility.data));
    await page.screenshot({ path: path.join(output, 'results-desktop.png'), fullPage: true });
    for (const viewport of [{ width: 1440, height: 1050 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await page.waitForTimeout(400);
      const metrics = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth > innerWidth,
        canvases: ['map-canvas', 'spectrum-canvas'].map((id) => {
          const canvas = document.getElementById(id);
          const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
          const colors = new Set();
          for (let i = 0; i < pixels.length; i += 64) colors.add(`${pixels[i]},${pixels[i+1]},${pixels[i+2]}`);
          return { id, width: canvas.width, height: canvas.height, colors: colors.size };
        }),
      }));
      assert(!metrics.overflow, JSON.stringify(metrics));
      for (const canvas of metrics.canvases) assert(canvas.colors > 10 && canvas.width > 100, JSON.stringify(canvas));
      console.log(JSON.stringify({ viewport, metrics }));
      await page.screenshot({ path: path.join(output, `results-${viewport.width}.png`), fullPage: true });
    }
    await page.click('#import-data');
    await page.screenshot({ path: path.join(output, 'import-mobile.png'), fullPage: true });
    await page.click('#import-cancel');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ output, errors }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
