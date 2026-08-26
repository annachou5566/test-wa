import { chromium } from 'playwright';

const targets = [
  { name: 'accepted-preview', origin: 'https://e94bdeb3.wave-alpha.pages.dev' },
  { name: 'production', origin: 'https://wave-alpha.pages.dev' },
];

const browser = await chromium.launch({ headless: true });
const evidence = {
  schema: 'wave-liquidation-lighter-runtime-diff-qa-v1',
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  probes: [],
  probePass: false,
  expectedRuntimeDivergenceObserved: false,
};

async function probe(target) {
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const result = {
    name: target.name,
    host: new URL(target.origin).hostname,
    ready: false,
  };

  try {
    await page.goto(`${target.origin}/?liquidationTest=1`, {
      waitUntil: 'domcontentloaded',
      timeout: 45_000,
    });

    await page.waitForFunction(() => (
      typeof window.CryptoMarket?.onTabActivated === 'function'
      && typeof window.WaveCryptoLiquidation?.activate === 'function'
      && window.WaveLiquidationContext
    ), null, { timeout: 30_000, polling: 100 });

    await page.evaluate(() => {
      const cryptoView = document.getElementById('crypto-market-view');
      if (cryptoView) cryptoView.style.display = 'block';
      window.CryptoMarket.onTabActivated();
      window.WaveCryptoLiquidation.activate();
      window.WaveLiquidationContext.set(
        { window: '24h', symbol: 'ALL', exchange: 'ALL' },
        { source: 'runtime-diff-qa', history: false, dashboard: true, reload: true },
      );
      window.WaveLiquidationContext.refresh?.('runtime-diff-qa');
    });

    await page.waitForFunction(() => {
      const api = window.WaveLiquidationContext;
      const payload = api?.payload;
      return Boolean(
        api
        && api.loading === false
        && payload
        && payload.context?.window === '24h'
        && payload.context?.symbol === 'ALL'
        && payload.context?.exchange === 'ALL'
        && Array.isArray(payload.matrix?.exchanges),
      );
    }, null, { timeout: 45_000, polling: 120 });

    Object.assign(result, await page.evaluate(() => {
      const payload = window.WaveLiquidationContext?.payload || null;
      const rows = Array.isArray(payload?.matrix?.exchanges) ? payload.matrix.exchanges : [];
      const lighter = rows.find(row => String(row?.exchange || '').toLowerCase() === 'lighter') || null;
      return {
        ready: true,
        source: payload?.source || null,
        storage: payload?.storage || null,
        contextTransport: payload?.diagnostics?.contextTransport || null,
        contextProjectionFallback: payload?.diagnostics?.contextProjectionFallback === true,
        rawSnapshotTransferred: payload?.diagnostics?.rawSnapshotTransferred ?? null,
        observedBuckets24hPositive: Number(payload?.diagnostics?.observedBuckets24h || 0) > 0,
        matrixCount: rows.length,
        lighterPresent: Boolean(lighter),
        lighterHasEvents: lighter?.hasEvents === true,
        lighterUnavailable: lighter?.unavailable === true,
        lighterIncludedInAll: lighter?.includedInAll === true,
        lighterStandalone: lighter?.standalone === true,
        lighterTotalPositive: Number(lighter?.totalUsd || 0) > 0,
        lighterCountPositive: Number(lighter?.count || 0) > 0,
        aggregateScope: payload?.dataBasis?.aggregateScope || null,
        pageAuditActive: window.WaveLiquidationPageAudit?.snapshot?.()?.active === true,
      };
    }));
  } catch (error) {
    result.error = String(error?.message || error || '').slice(0, 420);
  } finally {
    await context.close();
  }

  return result;
}

for (const target of targets) evidence.probes.push(await probe(target));

const preview = evidence.probes.find(item => item.name === 'accepted-preview');
const production = evidence.probes.find(item => item.name === 'production');
evidence.probePass = evidence.probes.every(item => item.ready === true);
evidence.expectedRuntimeDivergenceObserved = Boolean(
  preview?.ready
  && production?.ready
  && preview.lighterPresent
  && preview.lighterHasEvents
  && preview.lighterTotalPositive
  && preview.lighterCountPositive
  && production.lighterPresent
  && !production.lighterHasEvents
  && !production.lighterTotalPositive
  && !production.lighterCountPositive,
);

evidence.sameSourceTransport = Boolean(
  preview?.contextTransport
  && preview.contextTransport === production?.contextTransport,
);
evidence.sameMatrixCount = Number.isFinite(preview?.matrixCount)
  && preview.matrixCount === production?.matrixCount;

await browser.close();
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.probePass ? 0 : 1);
