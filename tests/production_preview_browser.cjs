const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(require.resolve('playwright', { paths: [process.argv[2] || process.cwd()] }));
const project = path.resolve(__dirname, '..');
const pending = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
const ready = '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="240"><ellipse cx="60" cy="190" rx="45" ry="25" fill="gray"/><path d="M50 20h20v170H50z" fill="silver"/></svg>';
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    const counts = new Map();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://preview.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><head><style>*{box-sizing:border-box}.details-panel{width:360px}.detail-preview{display:flex}.detail-preview-motion{width:100%;height:100%}</style><link rel="stylesheet" href="/static/css/producao_panel.css"></head><body><div id="root"><div class="details-panel"><div class="detail-preview"><div class="detail-preview-motion"><img src="/items/1/thumbnail"/></div></div></div><div class="node-thumbnail"><img src="/items/1/thumbnail"/></div></div><script src="/static/js/producao_panel.js"></script></body></html>` });
      if (url.pathname.startsWith('/static/')) return route.fulfill({ path: path.join(project, url.pathname) });
      const key = url.pathname + (url.searchParams.get('variant') || 'thumbnail');
      const count = (counts.get(key) || 0) + 1;
      counts.set(key, count);
      if (url.pathname.includes('/missing/')) return route.fulfill({ status: 404, body: '' });
      if (url.pathname.includes('/slow/')) return route.fulfill({ contentType: 'image/png', body: pending });
      return route.fulfill({ contentType: count <= 2 ? 'image/png' : 'image/svg+xml', body: count <= 2 ? pending : ready });
    });
    await page.goto('http://preview.test/');
    await page.waitForFunction(() => [...document.querySelectorAll('.detail-preview,.node-thumbnail')].every(el => el.dataset.previewState === 'ready'));
    assert.equal(await page.locator('.detail-preview').evaluate(el => Math.round(el.getBoundingClientRect().height)), 324);
    assert.equal(await page.locator('.detail-preview img').evaluate(el => getComputedStyle(el).visibility), 'visible');
    // React can reuse the same image element when the selected item changes.
    await page.locator('.detail-preview img').evaluate(el => el.src = '/items/2/thumbnail');
    await page.waitForFunction(() => document.querySelector('.detail-preview').dataset.previewState === 'ready' && document.querySelector('.detail-preview img').dataset.productionSource.includes('/items/2/'));
    await page.locator('.detail-preview img').evaluate(el => el.src = '/items/missing/thumbnail');
    await page.waitForFunction(() => document.querySelector('.detail-preview').dataset.previewState === 'unavailable');
    // A permanently pending response must end its loading state.
    await page.clock.install();
    await page.locator('.detail-preview img').evaluate(el => el.src = '/items/slow/thumbnail');
    await page.waitForFunction(() => document.querySelector('.detail-preview').dataset.previewState === 'loading');
    await page.clock.fastForward(120001);
    assert.equal(await page.locator('.detail-preview').getAttribute('data-preview-state'), 'unavailable');
    assert.deepEqual(errors, []);
    console.log('Chrome: pending -> ready, card/details, item replacement, missing preview and timeout passed.');
    // Exercise the shipped React bundle as well as the presentation adapter.
    const app = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    app.on('pageerror', error => errors.push(error.message));
    const node = {
      id: 'item-1', aux_code: 1, code: 'TEST/001', description: 'Synthetic assembly',
      quantity: 2, parent_id: null, child_ids: [], has_children: false,
      predecessor_ids: [], path_ids: ['item-1'], path_labels: ['TEST/001'],
      state: 'available', state_reason: 'Ready', state_reason_code: 'ready',
      operations_total: 0, operations_started: 0, operations_completed: 0,
      has_drawing: true, thumbnail_url: '/api/v1/orders/TEST/items/1/thumbnail',
      detail_url: '/api/v1/orders/TEST/items/1'
    };
    await app.route('http://bundle.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/api/')) {
        if (url.pathname.endsWith('/thumbnail')) return route.fulfill({ contentType: 'image/svg+xml', body: ready });
        let data = { node, parent: null, path: [], predecessors: [], operations: [], drawings: [], documents: [], categories: [], observations_count: 0 };
        if (url.pathname.endsWith('/structure')) data = { order: { number: 'TEST', title: 'Synthetic assembly' }, roots: [node.id], nodes: [node], progress: { percentage: 0, finalized_operations: 0, total_operations: 0 }, pending_count: 0, current_stage: 'Test', source: {} };
        if (url.pathname.endsWith('/dependencies')) data = { selected_order_number: 'TEST', nodes: [], edges: [], source: {} };
        if (url.pathname.endsWith('/materials')) data = { item_code: node.code, materials: [], material_used: false };
        if (url.pathname.endsWith('/live')) data = { operations: [], source: {} };
        return route.fulfill({ json: data });
      }
      const relative = url.pathname.startsWith('/producao-original/') ? url.pathname.replace('/producao-original/', '/static/producao_original/') : url.pathname;
      const file = path.join(project, relative, relative.endsWith('/') ? 'index.html' : '');
      if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ path: file });
    });
    await app.goto('http://bundle.test/producao-original/?os=TEST');
    await app.locator('.assembly-node').click();
    await app.waitForFunction(() => ['.detail-preview', '.node-thumbnail'].every(selector => document.querySelector(selector)?.dataset.previewState === 'ready'));
    const dimensions = await app.locator('.detail-preview').evaluate(el => ({ height: el.clientHeight, width: el.clientWidth }));
    assert(dimensions.height >= 280 && dimensions.width > 200);
    assert.deepEqual(errors, []);
    await app.close();
    console.log('Chrome: shipped React bundle displays the cutout in card and details.');

  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
