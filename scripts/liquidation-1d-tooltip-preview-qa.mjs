import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) {
  throw new Error('WA_PREVIEW_URL must be an exact wave-alpha.pages.dev Preview origin');
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await context.newPage();
const historyResponses = [];
const priceResponses = [];

page.on('response', response => {
  try {
    const url = new URL(response.url());
    if (url.pathname === '/api/liquidations/history') {
      historyResponses.push({
        status: response.status(),
        range: url.searchParams.get('range'),
        symbol: url.searchParams.get('symbol'),
        exchange: url.searchParams.get('exchange'),
        details: url.searchParams.get('details'),
        source: response.headers()['x-wave-liquidation-source'] || null,
        cache: response.headers()['x-wave-liquidation-cache'] || null,
      });
      return;
    }
    if (url.pathname === '/api/binance-spot' && url.searchParams.get('endpoint') === '/api/v3/klines') {
      priceResponses.push({ route: 'same-origin-binance-spot-klines', status: response.status() });
      return;
    }
    if (url.hostname === 'fapi.binance.com' && url.pathname === '/fapi/v1/klines') {
      priceResponses.push({ route: 'direct-binance-futures-klines', status: response.status() });
    }
  } catch (_) {}
});

const evidence = {
  schema: 'wave-liquidation-1d-tooltip-preview-qa-v5-sequential-user-flow',
  targetHost: new URL(target).hostname,
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  userFlow: '1D Total settles -> Exchanges -> hover',
  pass: false,
};

async function waitForTotal1dReady() {
  await page.waitForFunction(() => {
    const audit = window.WaveLiquidationPageAudit?.snapshot?.();
    return audit
      && audit.active === true
      && audit.historyRange === '1d'
      && audit.historyPayloadRange === '1d'
      && String(audit.historyPayloadSymbol || '').toUpperCase() === 'ALL'
      && String(audit.historyPayloadExchange || '').toUpperCase() === 'ALL'
      && audit.historyTooltipMode === 'summary'
      && audit.historyLoading !== true
      && !audit.lastError
      && Number(audit.historyRowCount || 0) > 0;
  }, null, { timeout: 25_000, polling: 80 });
}

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
    const summary = document.querySelector('[data-cml-tooltip-mode="summary"]');
    const range = document.querySelector('[data-cml-history-range="1d"]');
    if (!range || !summary) throw new Error('1D/Total controls missing');
    if (!summary.classList.contains('active')) summary.click();
    range.click();
  });
  await waitForTotal1dReady();

  const summaryAudit = await page.evaluate(() => {
    const a = window.WaveLiquidationPageAudit?.snapshot?.() || {};
    return {
      historyRange: a.historyRange || null,
      historyTooltipMode: a.historyTooltipMode || null,
      historyLoading: a.historyLoading === true,
      lastError: a.lastError ? String(a.lastError).slice(0, 180) : null,
      historyRowCount: Number(a.historyRowCount || 0),
    };
  });

  await page.evaluate(() => {
    const tooltip = document.querySelector('[data-cml-tooltip-mode="exchanges"]');
    if (!tooltip) throw new Error('Exchange tooltip control missing');
    tooltip.click();
  });
  await waitForHistoryReady();

  await page.locator('#cml-history-chart svg.wc-svg').waitFor({ state: 'visible', timeout: 10_000 });
  await page.waitForFunction(() => {
    const svg = document.querySelector('#cml-history-chart svg.wc-svg');
    return Boolean(svg?.querySelector('rect[id$="-cap"]'));
  }, null, { timeout: 10_000, polling: 80 });

  let found = null;
  let foundFraction = null;
  let genericTooltipSeen = false;
  const fractions = Array.from({ length: 61 }, (_, index) => (index + 1) / 62);
  for (const fraction of fractions) {
    await page.evaluate(value => {
      const cap = document.querySelector('#cml-history-chart svg.wc-svg rect[id$="-cap"]');
      if (!cap) throw new Error('WaveChart capture rect missing');
      const rect = cap.getBoundingClientRect();
      const clientX = rect.left + rect.width * value;
      const clientY = rect.top + rect.height * 0.5;
      const base = { bubbles: true, cancelable: true, pointerId: 1, pointerType: 'mouse', clientX, clientY };
      cap.dispatchEvent(new PointerEvent('pointerenter', base));
      cap.dispatchEvent(new PointerEvent('pointermove', base));
    }, fraction);
    await page.waitForTimeout(60);
    const state = await page.evaluate(() => {
      const tooltip = document.getElementById('cml-exchange-tooltip');
      let exchange = null;
      if (tooltip && !tooltip.hidden) {
        const style = getComputedStyle(tooltip);
        const rect = tooltip.getBoundingClientRect();
        const visible = style.display !== 'none'
          && style.visibility !== 'hidden'
          && Number(style.opacity) !== 0
          && rect.width > 0
          && rect.height > 0;
        if (visible) {
          const date = String(tooltip.querySelector('.cml-exchange-tip-date')?.textContent || '').trim();
          const price = String(tooltip.querySelector('.cml-exchange-tip-price')?.textContent || '').trim();
          const total = String(tooltip.querySelector('.cml-exchange-tip-total')?.textContent || '').trim();
          const bodyRows = [...tooltip.querySelectorAll('tbody tr')];
          const realRows = bodyRows.filter(row => {
            const cells = [...row.querySelectorAll('td')].map(cell => String(cell.textContent || '').replace(/\s+/g, ' ').trim());
            return cells.length >= 3 && cells[0] && cells[1] && cells[2] && cells[1] !== '—' && cells[2] !== '—';
          });
          const footer = String(tooltip.querySelector('tfoot')?.textContent || '').replace(/\s+/g, ' ').trim();
          exchange = {
            visible: true,
            exchangeRowCount: bodyRows.length,
            realExchangeRowCount: realRows.length,
            hasDate: date.length > 0,
            hasPrice: /^Price:\s*\$?[\d.]/.test(price),
            hasTotal: /^Total:\s*\$?[\d.]/.test(total),
            hasShort: /Short/i.test(String(tooltip.querySelector('thead')?.textContent || '')),
            hasLong: /Long/i.test(String(tooltip.querySelector('thead')?.textContent || '')),
            hasFooterTotal: /Total/i.test(footer),
          };
        }
      }
      const generic = document.querySelector('#cml-history-chart .wc-tip');
      let genericVisible = false;
      if (generic) {
        const style = getComputedStyle(generic);
        const rect = generic.getBoundingClientRect();
        genericVisible = style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
      }
      return { exchange, genericVisible };
    });
    genericTooltipSeen ||= state.genericVisible;
    if (state.exchange?.visible && state.exchange.realExchangeRowCount > 0) {
      found = state.exchange;
      foundFraction = fraction;
      break;
    }
  }

  const runtime = await page.evaluate(() => {
    const source = window.WaveLiquidationPageAudit?.snapshot?.() || {};
    const resilience = window.WaveLiquidationApiResilience?.snapshot?.() || {};
    return {
      audit: {
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
      },
      cacheName: resilience.cacheName || null,
      exactDetailRetries: Number(resilience.exactDetailRetries || 0),
      exactExchangeDetailSemanticGuard: resilience.exactExchangeDetailSemanticGuard === true,
      exactExchangeDetailFailClosed: resilience.exactExchangeDetailFailClosed === true,
    };
  });

  const detailResponses = historyResponses.filter(item => item.details === '1');
  const response = detailResponses.at(-1) || null;
  const exactRequestObserved = Boolean(response && response.status === 200 && response.range === '1d' && String(response.symbol || '').toUpperCase() === 'ALL' && String(response.exchange || '').toUpperCase() === 'ALL' && response.details === '1');
  const githubRunnerBinanceBlock = Boolean(found && !found.hasPrice && priceResponses.some(item => item.route === 'same-origin-binance-spot-klines' && item.status >= 400));
  const priceGate = Boolean(found?.hasPrice || githubRunnerBinanceBlock);
  const tooltipPass = Boolean(found && found.visible && found.realExchangeRowCount > 0 && found.hasDate && priceGate && found.hasTotal && found.hasShort && found.hasLong && found.hasFooterTotal);
  const cachePass = runtime.cacheName === 'wave-liquidation-read-v8';
  const semanticGuardPass = runtime.exactExchangeDetailSemanticGuard && runtime.exactExchangeDetailFailClosed;

  evidence.summary1d = summaryAudit;
  evidence.historyStatuses = historyResponses.map(item => ({ status: item.status, details: item.details || '0', cache: item.cache }));
  evidence.audit = runtime.audit;
  evidence.cacheName = runtime.cacheName;
  evidence.cacheNamespacePass = cachePass;
  evidence.exactDetailRetries = runtime.exactDetailRetries;
  evidence.semanticGuardPass = semanticGuardPass;
  evidence.detailRequest = response;
  evidence.exactDetailRequestObserved = exactRequestObserved;
  evidence.tooltip = found;
  evidence.priceGate = found?.hasPrice ? 'product-price-observed' : (githubRunnerBinanceBlock ? 'github-runner-binance-block' : 'failed');
  evidence.genericTooltipSeen = genericTooltipSeen;
  evidence.observedPointerFraction = foundFraction;
  evidence.positionsTried = fractions.length;
  evidence.priceTransport = priceResponses;
  evidence.pass = summaryAudit.historyLoading === false && !summaryAudit.lastError && exactRequestObserved && tooltipPass && cachePass && semanticGuardPass && !runtime.audit.lastError;
} catch (error) {
  evidence.error = String(error?.message || error || '').slice(0, 360);
  evidence.historyStatuses = historyResponses.map(item => ({ status: item.status, details: item.details || '0', cache: item.cache }));
  evidence.priceTransport = priceResponses;
}

await context.close();
await browser.close();
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
