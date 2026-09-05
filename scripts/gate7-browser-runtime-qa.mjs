import { chromium } from 'playwright';

const url = 'https://wave-alpha.pages.dev/?waveGate7Diag=1';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1200 },
  locale: 'en-US',
});
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
await cdp.send('Network.enable');

const wsUrls = new Map();
cdp.on('Network.webSocketCreated', event => {
  wsUrls.set(event.requestId, event.url);
  if (event.url.includes('/api/alpha-live')) console.log('WS_CREATED=' + event.url);
});
cdp.on('Network.webSocketHandshakeResponseReceived', event => {
  const url = wsUrls.get(event.requestId) || '';
  if (!url.includes('/api/alpha-live')) return;
  console.log('WS_HANDSHAKE_HTTP=' + event.response.status);
  console.log('WS_HANDSHAKE_STATUS_TEXT=' + String(event.response.statusText || ''));
  const h = event.response.headers || {};
  for (const key of ['cf-ray','server','content-type','retry-after']) {
    const value = h[key] ?? h[key.toUpperCase()] ?? h[key.replace(/(^|-)([a-z])/g, (_,a,b)=>a+b.toUpperCase())];
    if (value != null) console.log('WS_RESPONSE_' + key.toUpperCase().replaceAll('-','_') + '=' + value);
  }
});
cdp.on('Network.webSocketFrameError', event => {
  const url = wsUrls.get(event.requestId) || '';
  if (url.includes('/api/alpha-live')) console.log('WS_FRAME_ERROR=' + String(event.errorMessage || ''));
});

page.on('websocket', ws => {
  if (!ws.url().includes('/api/alpha-live')) return;
  console.log('PW_WS=' + ws.url());
  ws.on('socketerror', error => console.log('PW_WS_SOCKET_ERROR=' + String(error || '')));
  ws.on('close', () => console.log('PW_WS_CLOSE=1'));
});

page.on('console', msg => {
  const text = msg.text();
  if (text.includes('/api/alpha-live') || text.includes('ALPHA-LIVE') || text.includes('GATE7')) {
    console.log('[PAGE:' + msg.type() + '] ' + text);
  }
});

try {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });

  const run = page.locator('[data-g7-run]');
  const log = page.locator('[data-g7-log]');
  await run.waitFor({ state: 'visible', timeout: 15000 });

  await page.evaluate(() => {
    try { localStorage.setItem('wave_active_tab', 'running'); } catch {}
  });

  await run.click();

  await page.waitForFunction(() => {
    const el = document.querySelector('[data-g7-log]');
    return Boolean(el && el.value.includes('=== GATE7_LIVE_VOLUME_RUNTIME_END ==='));
  }, null, { timeout: 90000 });

  const value = await log.inputValue();
  console.log(value);

  const pass = /GATE7_LIVE_VOLUME_RUNTIME=PASS(?:\n|$)/.test(value);
  console.log('GATE7_HEADLESS_QA=' + (pass ? 'PASS' : 'NOT_PASS'));
  if (!pass) process.exitCode = 2;
} catch (error) {
  console.error('GATE7_HEADLESS_QA_ERROR=' + (error?.stack || error));
  process.exitCode = 3;
} finally {
  await browser.close();
}
