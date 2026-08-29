// Focused rerun after daily History HL-off dataset-bounded recovery.
import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await context.newPage();
let sameOriginSpotKlines = 0;
let directSpotKlines = 0;
let directFuturesKlines = 0;

page.on('request', request => {
  try {
    const url = new URL(request.url());
    if (url.origin === target && url.pathname === '/api/binance-spot' && url.searchParams.get('endpoint') === '/api/v3/klines') sameOriginSpotKlines++;
    if (url.hostname === 'api.binance.com' && url.pathname === '/api/v3/klines') directSpotKlines++;
    if (url.hostname === 'fapi.binance.com' && url.pathname === '/fapi/v1/klines') directFuturesKlines++;
  } catch (_) {}
});

await page.goto(`${target}/`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
await page.waitForFunction(() => Boolean(document.getElementById('cm-btn-liquidation') && window.WaveLiquidationPageAudit?.snapshot?.()?.mounted), null, { timeout: 20_000 });
await page.evaluate(() => {
  const button = document.getElementById('cm-btn-liquidation');
  const state = window.WaveLiquidationPageAudit?.snapshot?.();
  if (!state?.active) button?.click();
});

let ready = false;
try {
  await page.waitForFunction(() => {
    const state = window.WaveLiquidationPageAudit?.snapshot?.();
    return Boolean(state?.active
      && state.historyRange === '90d'
      && state.historyPayloadRange === '90d'
      && Number(state.historyRowCount) === 90
      && state.historyLoading === false
      && state.priceStatus === 'available'
      && Number(state.pricePointCount) > 0);
  }, null, { timeout: 15_000 });
  ready = true;
} catch (_) {}

const summary = await page.evaluate(() => {
  const state = window.WaveLiquidationPageAudit?.snapshot?.() || {};
  const priceAudit = window.WaveLiquidationPriceContext?.audit?.() || {};
  return {
    active: state.active === true,
    historyRange: String(state.historyRange || ''),
    historyPayloadRange: String(state.historyPayloadRange || ''),
    historyRowCount: Number(state.historyRowCount) || 0,
    historyLoading: state.historyLoading === true,
    priceStatus: String(state.priceStatus || ''),
    pricePointCount: Number(state.pricePointCount) || 0,
    lastError: state.lastError == null ? null : String(state.lastError).slice(0, 120),
    priceTransport: String(priceAudit.priceTransport || ''),
    spotKlineProxyRouted: priceAudit.spotKlineProxyRouted === true,
  };
});

const pass = ready
  && summary.active
  && summary.historyRange === '90d'
  && summary.historyPayloadRange === '90d'
  && summary.historyRowCount === 90
  && !summary.historyLoading
  && summary.priceStatus === 'available'
  && summary.pricePointCount > 0
  && summary.priceTransport === 'same-origin-binance-spot-proxy-v1'
  && summary.spotKlineProxyRouted
  && sameOriginSpotKlines > 0
  && directSpotKlines === 0;

console.log(JSON.stringify({
  schema: 'wave-liquidation-price-route-preview-qa-v1',
  targetHost: new URL(target).hostname,
  rawRowsLogged: false,
  moneyValuesLogged: false,
  sameOriginSpotKlineRequests: sameOriginSpotKlines,
  directSpotKlineRequests: directSpotKlines,
  directFuturesKlineRequests: directFuturesKlines,
  summary,
  pass,
}, null, 2));

await context.close();
await browser.close();
process.exit(pass ? 0 : 1);
