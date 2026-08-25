import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) {
  throw new Error('WA_PREVIEW_URL must be an exact wave-alpha.pages.dev Preview origin');
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await context.newPage();
const historyResponses = [];

page.on('response', response => {
  try {
    const url = new URL(response.url());
    if (url.pathname !== '/api/liquidations/history' || url.searchParams.get('details') !== '1') return;
    historyResponses.push({
      status: response.status(),
      range: url.searchParams.get('range'),
      symbol: url.searchParams.get('symbol'),
      exchange: url.searchParams.get('exchange'),
      details: url.searchParams.get('details'),
      source: response.headers()['x-wave-liquidation-source'] || null,
      cache: response.headers()['x-wave-liquidation-cache'] || null,
    });
  } catch (_) {}
});

const evidence = {
  schema: 'wave-liquidation-1d-tooltip-preview-qa-v1',
  targetHost: new URL(target).hostname,
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  pass: false,
};

async function waitForHistoryReady() {
  await page.waitForFunction(() => {
    const audit = window.WaveLiquidationPageAudit?.snapshot?.();
    return audit
      && audit.active === true
      && audit.historyRange === '1d'
      && audit.historyPayloadRange === '1d'
      && String(audit.historyPayloadSymbol || '').toUpperCase() === 'ALL'
      && String(audit.historyPayloadExchange || '').toUpperCase() === 'ALL'
      && audit.historyTooltipMode === 'exchanges'
      && audit.historyLoading !== true
      && !audit.lastError
      && Number(audit.historyRowCount || 0) > 0;
  }, null, { timeout: 25_000, polling: 80 });
}

function tooltipState() {
  const tooltip = document.getElementById('cml-exchange-tooltip');
  if (!tooltip || tooltip.hidden) return null;
  const style = getComputedStyle(tooltip);
  const rect = tooltip.getBoundingClientRect();
  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0 || rect.width <= 0 || rect.height <= 0) return null;

  const head = tooltip.querySelector('.cml-exchange-tip-head');
  const date = String(tooltip.querySelector('.cml-exchange-tip-date')?.textContent || '').trim();
  const price = String(tooltip.querySelector('.cml-exchange-tip-price')?.textContent || '').trim();
  const total = String(tooltip.querySelector('.cml-exchange-tip-total')?.textContent || '').trim();
  const bodyRows = [...tooltip.querySelectorAll('tbody tr')];
  const realRows = bodyRows.filter(row => {
    const cells = [...row.querySelectorAll('td')].map(cell => String(cell.textContent || '').replace(/\s+/g, ' ').trim());
    return cells.length >= 3 && cells[0] && cells[1] && cells[2] && cells[1] !== '—' && cells[2] !== '—';
  });
  const footer = String(tooltip.querySelector('tfoot')?.textContent || '').replace(/\s+/g, ' ').trim();
  const headerText = String(head?.textContent || '').replace(/\s+/g, ' ').trim();

  return {
    visible: true,
    exchangeRowCount: bodyRows.length,
    realExchangeRowCount: realRows.length,
    hasDate: date.length > 0,
    hasPrice: /^Price:\s*\$?[\d.]/.test(price),
    hasTotal: /^Total:\s*\$?[\d.]/.test(total),
    hasShort: /Short/i.test(String(tooltip.querySelector('thead')?.textContent || '')),
    hasLong: /Long/i.test(String(tooltip.querySelector('thead')?.textContent || '')),
    hasFooterTotal: /Total/i.test(footer),
    headerPresent: headerText.length > 0,
  };
}

try {
  await page.goto(`${target}/?liquidationTest=1`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForFunction(() => {
    const audit = window.WaveLiquidationPageAudit?.snapshot?.();
    return audit?.mounted === true
      && typeof window.CryptoMarket?.onTabActivated === 'function'
      && typeof window.WaveCryptoLiquidation?.activate === 'function';
  }, null, { timeout: 25_000, polling: 80 });

  await page.evaluate(() => {
    const cryptoView = document.getElementById('crypto-market-view');
    if (!cryptoView) throw new Error('crypto-market-view missing');
    cryptoView.style.display = 'block';
    window.CryptoMarket.onTabActivated();
    window.WaveCryptoLiquidation.activate();
  });
  await page.waitForFunction(() => window.WaveLiquidationPageAudit?.snapshot?.()?.active === true, null, { timeout: 10_000, polling: 80 });

  await page.evaluate(() => {
    const range = document.querySelector('[data-cml-history-range="1d"]');
    const tooltip = document.querySelector('[data-cml-tooltip-mode="exchanges"]');
    if (!range || !tooltip) throw new Error('1D/Exchange tooltip controls missing');
    range.click();
    tooltip.click();
  });
  await waitForHistoryReady();

  const svg = page.locator('#cml-history-chart svg').first();
  await svg.waitFor({ state: 'visible', timeout: 10_000 });
  const box = await svg.boundingBox();
  if (!box || box.width <= 0 || box.height <= 0) throw new Error('history chart SVG has no visible bounding box');

  let found = null;
  let foundFraction = null;
  const fractions = [0.08, 0.16, 0.24, 0.32, 0.40, 0.48, 0.56, 0.64, 0.72, 0.80, 0.88, 0.94];
  for (const fraction of fractions) {
    await page.mouse.move(box.x + box.width * fraction, box.y + box.height * 0.46);
    await page.waitForTimeout(120);
    const state = await page.evaluate(tooltipState);
    if (state?.visible && state.realExchangeRowCount > 0) {
      found = state;
      foundFraction = fraction;
      break;
    }
  }

  const audit = await page.evaluate(() => {
    const source = window.WaveLiquidationPageAudit?.snapshot?.() || {};
    return {
      active: source.active === true,
      historyRange: source.historyRange || null,
      historyPayloadRange: source.historyPayloadRange || null,
      historyPayloadSymbol: source.historyPayloadSymbol || null,
      historyPayloadExchange: source.historyPayloadExchange || null,
      historyTooltipMode: source.historyTooltipMode || null,
      historyRowCount: Number.isFinite(Number(source.historyRowCount)) ? Number(source.historyRowCount) : null,
      historyLoading: source.historyLoading === true,
      priceStatus: source.priceStatus || null,
      pricePointCount: Number.isFinite(Number(source.pricePointCount)) ? Number(source.pricePointCount) : null,
      lastError: source.lastError ? String(source.lastError).slice(0, 180) : null,
    };
  });

  const response = historyResponses.at(-1) || null;
  const exactRequestObserved = Boolean(
    response
    && response.status === 200
    && response.range === '1d'
    && String(response.symbol || '').toUpperCase() === 'ALL'
    && String(response.exchange || '').toUpperCase() === 'ALL'
    && response.details === '1'
  );
  const tooltipPass = Boolean(
    found
    && found.visible
    && found.realExchangeRowCount > 0
    && found.hasDate
    && found.hasPrice
    && found.hasTotal
    && found.hasShort
    && found.hasLong
    && found.hasFooterTotal
  );

  evidence.audit = audit;
  evidence.detailRequest = response;
  evidence.exactDetailRequestObserved = exactRequestObserved;
  evidence.tooltip = found;
  evidence.observedPointerFraction = foundFraction;
  evidence.positionsTried = fractions.length;
  evidence.pass = exactRequestObserved && tooltipPass && !audit.lastError;
} catch (error) {
  evidence.error = String(error?.message || error || '').slice(0, 360);
}

await context.close();
await browser.close();
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
