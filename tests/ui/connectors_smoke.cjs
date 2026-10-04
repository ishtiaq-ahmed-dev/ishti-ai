/* Synthetic browser regression: production bundle, mocked APIs, no real credentials/accounts. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.ISHTI_PLAYWRIGHT_MODULE || 'playwright');

const dist = path.resolve(__dirname, '../../interface/app/dist');
const service = {
  service: 'gmail', name: 'Gmail', family: 'google', kind: 'oauth', api: '', auth: '', scopes: [], granted: [],
  has_credentials: false, one_click: false, connected: false, account: '', has_refresh: false,
  expires_at: 0, connected_at: 0, state: 'empty', setup: {}, status_text: 'App registration required.', progress: { state: 'idle' },
};
const catalog = () => ({ connectors: [service], connected: service.connected ? 1 : 0,
  ready: service.has_credentials && !service.connected ? 1 : 0, total: 1, families: ['google'], note: 'Synthetic QA' });
const server = http.createServer((req, res) => {
  const file = path.resolve(dist, '.' + decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname));
  if (file !== dist && !file.startsWith(dist + path.sep)) { res.writeHead(403).end(); return; }
  const target = fs.existsSync(file) && fs.statSync(file).isFile() ? file : path.join(dist, 'index.html');
  res.setHeader('Content-Type', ({ '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2' })[path.extname(target)] || 'application/octet-stream');
  fs.createReadStream(target).pipe(res);
});

let browser;
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__qaSockets = [];
    window.WebSocket = class {
      constructor(url) { this.url = url; window.__qaSockets.push(this); setTimeout(() => this.onopen?.({}), 10); }
      send() {}
      close() {}
    };
  });
  await page.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    let data = {};
    if (pathname === '/api/connectors') data = catalog();
    else if (pathname === '/api/connectors/gmail/credentials') {
      assert.deepEqual(route.request().postDataJSON(), { client_id: 'synthetic-client', client_secret: 'synthetic-secret' });
      service.has_credentials = service.one_click = true; service.state = 'ready';
      data = { ok: true, reply: 'Synthetic app registration saved.' };
    } else if (pathname === '/api/connectors/gmail/connect') {
      service.progress = { state: 'waiting', reply: 'Synthetic waiting' };
      data = { ok: true, waiting: true, reply: 'Synthetic waiting' };
    } else if (pathname === '/api/flowise/flows') data = { configured: false, health: 'not configured', flows: [] };
    else if (pathname === '/api/facts') data = { facts: [] };
    else if (pathname === '/api/notifications') data = { notifications: [], unread: 0 };
    else if (pathname === '/api/ready') data = { ready: true, stages: [], progress: 100 };
    await route.fulfill({ json: data });
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/#connectors`);
  await page.getByRole('button', { name: 'Advanced setup', exact: true }).click();
  const id = page.getByRole('textbox', { name: 'Gmail client ID', exact: true });
  await id.pressSequentially('synthetic-client');
  assert.equal(await id.inputValue(), 'synthetic-client', 'Credential input must retain focus through rerenders');
  await page.getByLabel('Gmail client secret', { exact: true }).fill('synthetic-secret');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('button', { name: 'Connect to Google', exact: true }).click();
  await page.getByRole('button', { name: 'Waiting for consent…', exact: true }).waitFor();
  service.connected = true; service.state = 'connected'; service.account = 'test@example.invalid';
  service.progress = { state: 'done', reply: 'Synthetic sign-in complete.' };
  await page.evaluate(() => {
    window.__qaSockets.find(socket => socket.url.endsWith('/ws')).onmessage({ data: JSON.stringify({ type: 'connectors_updated', data: { service: 'gmail' } }) });
  });
  await page.getByText('test@example.invalid', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Disconnect', exact: true }).waitFor();
  await page.getByText('Synthetic sign-in complete.', { exact: true }).waitFor();
  assert.deepEqual(errors, [], 'No browser runtime errors');
  console.log('PASS: credential typing, save, Google sign-in button, waiting state, live completion without refresh, no runtime errors.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  server.close();
});
