import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const evidence = {
  schema: 'wave-liquidation-matrix-lighter-bar-preview-qa-v1',
  targetHost: new URL(target).hostname,
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  matrix: null,
  bar: null,
  pass: false,
};

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function stableActivate() {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await page.goto(`${target}/?liquidationTest=1`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
      await page.waitForFunction(() => typeof window.CryptoMarket?.onTabActivated === 'function' && typeof window.WaveCryptoLiquidation?.activate === 'function', null, { timeout: 25_000, polling: 80 });
      await page.evaluate(() => {
        const v = document.getElementById('crypto-market-view');
        if (!v) throw new Error('crypto view missing');
        v.style.display = 'block';
        window.CryptoMarket.onTabActivated();
        window.WaveCryptoLiquidation.activate();
      });
      await page.waitForFunction(() => {
        const a = window.WaveLiquidationExchangeAudit?.snapshot?.();
        return a?.mounted === true && a?.exchangeRows === 13;
      }, null, { timeout: 25_000, polling: 100 });
      return;
    } catch (error) {
      if (attempt === 2) throw error;
      await sleep(350);
    }
  }
}

try {
  await stableActivate();

  evidence.matrix = await page.evaluate(() => {
    const subtitle = String(document.querySelector('#cmlx-panel .cmlx-sub')?.textContent || '').replace(/\s+/g, ' ').trim();
    const row = document.querySelector('#cmlx-body tr[data-exchange="lighter"]');
    const cells = row ? [...row.querySelectorAll(':scope > td')] : [];
    const liquidationText = String(cells[1]?.textContent || '').trim();
    const shareText = String(cells[4]?.textContent || '').trim();
    const hasValue = Boolean(liquidationText && liquidationText !== '—' && liquidationText !== '$0');
    const shareNumeric = Number.parseFloat(shareText.replace('%', ''));
    return {
      subtitle13: /13\s+sàn/i.test(subtitle),
      lighterListed: Boolean(row),
      lighterHasValue: hasValue,
      lighterShareAvailableWhenValue: !hasValue || (shareText !== '—' && Number.isFinite(shareNumeric) && shareNumeric >= 0),
      lighterShareText: shareText,
    };
  });

  const started = Date.now();
  await page.evaluate(() => {
    document.querySelector('[data-cml-chart-style="bar"]')?.click();
    document.querySelector('[data-cml-chart-layout="diverging"]')?.click();
  });
  await page.waitForFunction(() => {
    const s = window.WaveLiquidationPageAudit?.snapshot?.();
    const a = window.WaveLiquidationPageAudit?.run?.();
    const chart = document.getElementById('cml-history-chart');
    return s?.historyChartStyle === 'bar'
      && s?.historyChartLayout === 'diverging'
      && chart?.dataset?.chartStyle === 'bar'
      && chart?.dataset?.chartLayout === 'diverging'
      && a?.checks?.longShortShareOneColumn === true;
  }, null, { timeout: 2500, polling: 40 });
  const durationMs = Date.now() - started;
  evidence.bar = await page.evaluate(durationMs => {
    const s = window.WaveLiquidationPageAudit?.snapshot?.() || {};
    const a = window.WaveLiquidationPageAudit?.run?.() || {};
    return {
      durationMs,
      eliteBudgetMs: 500,
      rendererBarPairs: Number(s.rendererBarPairs || 0),
      rendererBarMisalignedPairs: Number(s.rendererBarMisalignedPairs || 0),
      barCheck: a?.checks?.longShortShareOneColumn === true,
      pass: durationMs <= 500 && a?.checks?.longShortShareOneColumn === true,
    };
  }, durationMs);

  evidence.pass = evidence.matrix?.subtitle13 === true
    && evidence.matrix?.lighterListed === true
    && evidence.matrix?.lighterShareAvailableWhenValue === true
    && evidence.bar?.pass === true;
} catch (error) {
  evidence.error = String(error?.message || error || '').slice(0, 400);
}

await browser.close();
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
