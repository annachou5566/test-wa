import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) {
  throw new Error('WA_PREVIEW_URL must be an exact wave-alpha.pages.dev Preview origin');
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1400, height: 702 }, deviceScaleFactor: 2 });
const page = await context.newPage();
const evidence = {
  schema: 'wave-liquidation-hardcore-seeded-preview-qa-v1',
  targetHost: new URL(target).hostname,
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  seed: '90D + Bar + Diverging + Exchanges',
  pass: false,
};

const safeAudit = source => ({
  mounted: source?.mounted === true,
  active: source?.active === true,
  historyRange: source?.historyRange || null,
  historyPayloadRange: source?.historyPayloadRange || null,
  historyTooltipMode: source?.historyTooltipMode || null,
  historyChartStyle: source?.historyChartStyle || null,
  historyChartLayout: source?.historyChartLayout || null,
  historyRowCount: Number.isFinite(Number(source?.historyRowCount)) ? Number(source.historyRowCount) : null,
  historyLoading: source?.historyLoading === true,
  priceStatus: source?.priceStatus || null,
  pricePointCount: Number.isFinite(Number(source?.pricePointCount)) ? Number(source.pricePointCount) : null,
  lastError: source?.lastError ? String(source.lastError).slice(0, 160) : null,
  measureButtonExists: source?.measureButtonExists === true,
  rendererBarPairs: Number.isFinite(Number(source?.rendererBarPairs)) ? Number(source.rendererBarPairs) : null,
  rendererBarMisalignedPairs: Number.isFinite(Number(source?.rendererBarMisalignedPairs)) ? Number(source.rendererBarMisalignedPairs) : null,
});

try {
  await page.goto(`${target}/?liquidationTest=1`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForFunction(() => {
    const a = window.WaveLiquidationPageAudit?.snapshot?.();
    return a?.mounted === true
      && typeof window.CryptoMarket?.onTabActivated === 'function'
      && typeof window.WaveCryptoLiquidation?.activate === 'function'
      && typeof window.WaveLiquidationDebug?.run === 'function';
  }, null, { timeout: 25_000, polling: 80 });

  await page.evaluate(() => {
    const view = document.getElementById('crypto-market-view');
    if (!view) throw new Error('crypto-market-view missing');
    view.style.display = 'block';
    window.CryptoMarket.onTabActivated();
    window.WaveCryptoLiquidation.activate();
  });

  await page.waitForFunction(() => {
    const a = window.WaveLiquidationPageAudit?.snapshot?.();
    return a?.active === true && a.historyRange === '90d' && a.historyLoading !== true && Number(a.historyRowCount) === 90;
  }, null, { timeout: 25_000, polling: 80 });

  await page.evaluate(() => {
    document.querySelector('[data-cml-history-range="90d"]')?.click();
    document.querySelector('[data-cml-chart-style="bar"]')?.click();
    document.querySelector('[data-cml-chart-layout="diverging"]')?.click();
    document.querySelector('[data-cml-tooltip-mode="exchanges"]')?.click();
  });

  await page.waitForFunction(() => {
    const a = window.WaveLiquidationPageAudit?.snapshot?.();
    return a?.active === true
      && a.historyRange === '90d'
      && a.historyPayloadRange === '90d'
      && a.historyChartStyle === 'bar'
      && a.historyChartLayout === 'diverging'
      && a.historyTooltipMode === 'exchanges'
      && a.historyLoading !== true
      && Number(a.historyRowCount) === 90
      && a.priceStatus === 'available'
      && Number(a.pricePointCount) > 0
      && !a.lastError;
  }, null, { timeout: 25_000, polling: 80 });

  evidence.before = await page.evaluate(() => {
    const a = window.WaveLiquidationPageAudit?.snapshot?.() || {};
    const error = document.getElementById('cml-error');
    const errorStyle = error ? getComputedStyle(error) : null;
    const errorTextPresent = Boolean(String(error?.textContent || '').trim());
    const errorVisible = Boolean(error && !error.hidden && errorStyle?.display !== 'none' && errorStyle?.visibility !== 'hidden' && Number(errorStyle?.opacity ?? 1) !== 0 && errorTextPresent);
    const svg = document.querySelector('#cml-history-chart svg.wc-svg, #cml-history-chart svg');
    return {
      audit: {
        mounted: a.mounted === true,
        active: a.active === true,
        historyRange: a.historyRange || null,
        historyPayloadRange: a.historyPayloadRange || null,
        historyTooltipMode: a.historyTooltipMode || null,
        historyChartStyle: a.historyChartStyle || null,
        historyChartLayout: a.historyChartLayout || null,
        historyRowCount: Number.isFinite(Number(a.historyRowCount)) ? Number(a.historyRowCount) : null,
        historyLoading: a.historyLoading === true,
        priceStatus: a.priceStatus || null,
        pricePointCount: Number.isFinite(Number(a.pricePointCount)) ? Number(a.pricePointCount) : null,
        lastError: a.lastError ? String(a.lastError).slice(0, 160) : null,
        measureButtonExists: a.measureButtonExists === true,
        rendererBarPairs: Number.isFinite(Number(a.rendererBarPairs)) ? Number(a.rendererBarPairs) : null,
        rendererBarMisalignedPairs: Number.isFinite(Number(a.rendererBarMisalignedPairs)) ? Number(a.rendererBarMisalignedPairs) : null,
      },
      chartExists: Boolean(svg),
      visibleError: errorVisible,
      errorTextPresent,
      errorHiddenProperty: error ? error.hidden === true : null,
    };
  });

  const result = await page.evaluate(async () => window.WaveLiquidationDebug.run());
  evidence.hardcore = {
    functionalPass: result?.functionalPass === true,
    elitePerformancePass: result?.elitePerformancePass === true,
    productionCandidate: result?.productionCandidate === true,
    totalSteps: Number(result?.totalSteps || 0),
    functionalFailed: Array.isArray(result?.functionalFailed) ? result.functionalFailed.slice(0, 24) : [],
    eliteMissed: Array.isArray(result?.eliteMissed) ? result.eliteMissed.slice(0, 24) : [],
    finalState: safeAudit(result?.finalState),
  };

  evidence.after = await page.evaluate(() => {
    const error = document.getElementById('cml-error');
    const errorStyle = error ? getComputedStyle(error) : null;
    const textPresent = Boolean(String(error?.textContent || '').trim());
    return {
      chartExists: Boolean(document.querySelector('#cml-history-chart svg.wc-svg, #cml-history-chart svg')),
      visibleError: Boolean(error && !error.hidden && errorStyle?.display !== 'none' && errorStyle?.visibility !== 'hidden' && Number(errorStyle?.opacity ?? 1) !== 0 && textPresent),
      errorTextPresent: textPresent,
    };
  });

  evidence.pass = evidence.before.chartExists
    && !evidence.before.visibleError
    && evidence.hardcore.functionalPass
    && evidence.hardcore.elitePerformancePass
    && evidence.hardcore.productionCandidate;
} catch (error) {
  evidence.error = String(error?.message || error || '').slice(0, 360);
}

await context.close();
await browser.close();
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
