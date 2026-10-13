const {artifactPath} = require('./artifacts.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const output = process.env.EELS_TEST_OUTPUT || artifactPath('.tmp_roi_ui');
fs.mkdirSync(output, { recursive: true });
const url = process.env.EELS_TEST_URL || 'http://127.0.0.1:8770';
const closeTo = (a, b) => assert(Math.abs(a - b) < 1e-5, `${a} != ${b}`);

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      if (response.url().includes('/api/') && response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);
    });
    let previews = 0;
    page.on('request', request => { if (request.url().endsWith('/api/preview')) previews += 1; });
    await page.goto(url);
    await page.waitForFunction(() => Boolean(state.previews.Si));
    const others = await page.evaluate(() => JSON.stringify([state.rois.Interface, state.rois.Ge]));
    const current = () => page.evaluate(() => ({ ...state.rois.Si }));
    const clientPoint = async (x, y) => page.evaluate(({ x, y }) => {
      const point = mapPoint(x, y);
      const bounds = byId('map-canvas').getBoundingClientRect();
      return { x: point.x + bounds.left, y: point.y + bounds.top };
    }, { x, y });
    const ready = () => page.waitForFunction(() => !state.drag &&
      roiSignature(state.previews.Si?.roi) === roiSignature(state.rois.Si));
    const drag = async (from, to) => {
      await page.mouse.move(from.x, from.y);
      await page.mouse.down();
      await page.mouse.move(to.x, to.y, { steps: 5 });
      await page.mouse.up();
      await ready();
    };

    await drag(await clientPoint(8, 5), await clientPoint(11, 13));
    let roi = await current();
    closeTo(roi.x_min, 8);
    closeTo(roi.y_max, 13);
    for (const mode of ['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se']) {
      const before = await current();
      const handle = await page.evaluate(mode => {
        const h = EelsRoi.handles(roiCanvasRect(state.rois.Si)).find(h => h.mode === mode);
        const bounds = byId('map-canvas').getBoundingClientRect();
        return { x: h.x + bounds.left, y: h.y + bounds.top };
      }, mode);
      const dx = mode.includes('w') ? -7 : mode.includes('e') ? 7 : 0;
      const dy = mode.includes('n') ? -7 : mode.includes('s') ? 7 : 0;
      await page.mouse.move(handle.x, handle.y);
      assert.equal(await page.locator('#map-canvas').evaluate(el => el.style.cursor), `${mode}-resize`);
      const count = previews;
      await page.mouse.down();
      await page.mouse.move(handle.x + dx, handle.y + dy, { steps: 4 });
      await page.waitForTimeout(200);
      assert.equal(previews, count, 'No new live preview requests during dragging');
      await page.mouse.up();
      await ready();
      assert.equal(previews, count + 1, 'One preview on release');
      const after = await current();
      for (const [key, edge] of [['x_min', 'w'], ['x_max', 'e'], ['y_min', 's'], ['y_max', 'n']]) {
        if (!mode.includes(edge)) closeTo(after[key], before[key]);
        else assert.notEqual(after[key], before[key], `${mode} changes ${key}`);
      }
    }

    roi = await current();
    const center = await clientPoint((roi.x_min + roi.x_max) / 2, (roi.y_min + roi.y_max) / 2);
    await drag(center, { x: center.x - 15, y: center.y - 12 });
    let moved = await current();
    closeTo(moved.x_max - moved.x_min, roi.x_max - roi.x_min);
    closeTo(moved.y_max - moved.y_min, roi.y_max - roi.y_min);
    const right = await clientPoint(moved.x_max, (moved.y_min + moved.y_max) / 2);
    await drag(right, { x: right.x + 600, y: right.y });
    closeTo((await current()).x_max, await page.evaluate(() => state.info.x_nm[1]));

    await page.selectOption('#fit-mode', 'pca');
    await page.fill('#pca-components', '2');
    await page.fill('#pca-peak-count', '2');
    await page.click('#fit-active');
    await page.waitForFunction(() => !state.busy && Boolean(state.pcaResults.Si));
    const id = await page.evaluate(() => state.pcaResults.Si.result_id);
    roi = await current();
    const middle = await clientPoint((roi.x_min + roi.x_max) / 2, (roi.y_min + roi.y_max) / 2);
    await page.mouse.click(middle.x, middle.y);
    assert.equal(await page.evaluate(() => state.pcaResults.Si.result_id), id, 'Click preserves analysis');
    await page.mouse.move(middle.x, middle.y);
    await page.mouse.down();
    await page.mouse.move(middle.x - 12, middle.y + 12, { steps: 3 });
    await page.keyboard.press('Escape');
    await page.mouse.up();
    assert.deepEqual(await current(), roi);
    assert.equal(await page.evaluate(() => state.pcaResults.Si.result_id), id, 'Cancel restores analysis');
    assert.equal(await page.evaluate(() => JSON.stringify([state.rois.Interface, state.rois.Ge])), others);

    const mapCheck = async (target) => target.evaluate(() => {
      const canvas = byId('map-canvas');
      const dpr = window.devicePixelRatio || 1;
      const ctx = canvas.getContext('2d');
      const pixel = ctx.getImageData(
        Math.round((state.mapLayout.left + state.mapLayout.width * 0.16) * dpr),
        Math.round((state.mapLayout.top + state.mapLayout.height * 0.22) * dpr), 1, 1
      ).data;
      return {
        pixel: Array.from(pixel),
        aspect: state.mapLayout.width / state.mapLayout.height,
        physicalAspect: (state.info.x_nm[1] - state.info.x_nm[0]) / (state.info.y_nm[1] - state.info.y_nm[0]),
        overflow: document.documentElement.scrollWidth > innerWidth,
        colorLow: colorScale(0), colorHigh: colorScale(1),
      };
    });
    const desktop = await mapCheck(page);
    closeTo(desktop.aspect, desktop.physicalAspect);
    assert(!desktop.overflow);
    assert(desktop.pixel[2] > desktop.pixel[0], 'Low-signal map is blue');
    assert.equal(desktop.colorLow, 'rgb(62,38,168)');
    assert.equal(desktop.colorHigh, 'rgb(249,251,21)');
    await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
    mobile.on('pageerror', error => errors.push(error.message));
    await mobile.goto(url);
    await mobile.waitForFunction(() => Boolean(state.previews.Si));
    const session = await mobile.context().newCDPSession(mobile);
    const touchHandle = await mobile.evaluate(() => {
      const rect = roiCanvasRect(state.rois.Si);
      const bounds = byId('map-canvas').getBoundingClientRect();
      return { x: rect.right + bounds.left, y: (rect.top + rect.bottom) / 2 + bounds.top };
    });
    const beforeTouch = await mobile.evaluate(() => state.rois.Si.x_max);
    await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [touchHandle] });
    await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ ...touchHandle, x: touchHandle.x + 18 }] });
    await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await mobile.waitForFunction(() => roiSignature(state.previews.Si?.roi) === roiSignature(state.rois.Si));
    assert((await mobile.evaluate(() => state.rois.Si.x_max)) > beforeTouch, 'Touch resizes edge');
    const mobileCheck = await mapCheck(mobile);
    assert(!mobileCheck.overflow);
    closeTo(mobileCheck.aspect, mobileCheck.physicalAspect);
    await mobile.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ desktop, mobile: mobileCheck, previews, errors, output }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
