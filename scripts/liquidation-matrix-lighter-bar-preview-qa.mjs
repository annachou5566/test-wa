import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 702 } });
const evidence = {
  schema: 'wave-liquidation-lighter-matrix-pressure-cache-preview-qa-v2',
  targetHost: new URL(target).hostname,
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
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

function parseUsd(text) {
  const clean = String(text || '').trim();
  if (!clean || clean === '—') return null;
  const match = clean.match(/^\$([\d.]+)([KMB])?$/i);
  if (!match) return null;
  const value = Number(match[1]);
  if (!Number.isFinite(value)) return null;
  const multiplier = match[2]?.toUpperCase() === 'B' ? 1e9 : match[2]?.toUpperCase() === 'M' ? 1e6 : match[2]?.toUpperCase() === 'K' ? 1e3 : 1;
  return value * multiplier;
}

try {
  await stableActivate();

  evidence.runtime = await page.evaluate(() => {
    const scripts = [...document.scripts].map(s => String(s.src || ''));
    return {
      exchangeMatrixV14: scripts.some(src => /\/public\/js\/liquidation\/tab\/exchange-matrix\.js\?v=14(?:$|&)/.test(src)),
      apiResilienceV9: scripts.some(src => /\/public\/js\/liquidation\/tab\/api-resilience\.js\?v=9(?:$|&)/.test(src)),
      controllerV20: scripts.some(src => /\/public\/js\/liquidation\/tab\/controller\.js\?v=20(?:$|&)/.test(src)),
      version: null,
    };
  });
  const versionResponse = await page.request.get(`${target}/version.json?_qa=${Date.now()}`);
  const versionBody = versionResponse.ok() ? await versionResponse.json() : {};
  evidence.runtime.version = String(versionBody?.version || '');

  evidence.matrix = await page.evaluate(() => {
    const lighter = document.querySelector('#cmlx-body tr[data-exchange="lighter"]');
    const cells = lighter ? [...lighter.querySelectorAll(':scope > td')] : [];
    const liquidationText = String(cells[1]?.textContent || '').trim();
    const shareText = String(cells[4]?.textContent || '').trim();
    const rows = [...document.querySelectorAll('#cmlx-body tr[data-exchange]:not(.cmlx-row-all)')].map(row => {
      const td = [...row.querySelectorAll(':scope > td')];
      return { exchange: String(row.dataset.exchange || ''), liquidationText: String(td[1]?.textContent || '').trim() };
    });
    return {
      rowCount: rows.length,
      lighterListed: Boolean(lighter),
      lighterHasValue: Boolean(liquidationText && liquidationText !== '—' && liquidationText !== '$0'),
      lighterShareText: shareText,
      lighterShareAvailable: Boolean(shareText && shareText !== '—'),
      rows,
    };
  });
  const parsed = evidence.matrix.rows.map(row => ({ exchange: row.exchange, value: parseUsd(row.liquidationText) }));
  const available = parsed.filter(row => row.value != null);
  evidence.matrix.volumeDescending = available.every((row, index) => index === 0 || available[index - 1].value >= row.value);
  evidence.matrix.exchangeOrder = parsed.map(row => row.exchange);
  delete evidence.matrix.rows;

  evidence.pressure = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#cmlx-pressure .cmlx-pressure-row[data-exchange]')];
    const lighter = rows.find(row => row.dataset.exchange === 'lighter') || null;
    return {
      rowCount: rows.length,
      lighterListed: Boolean(lighter),
      lighterLabel: String(lighter?.querySelector('.cmlx-pressure-name')?.textContent || '').trim(),
      lighterValueAvailable: Boolean(String(lighter?.querySelector('.cmlx-pressure-value')?.textContent || '').trim().replace('—', '')),
    };
  });

  evidence.pass = evidence.runtime?.exchangeMatrixV14 === true
    && evidence.runtime?.apiResilienceV9 === true
    && evidence.runtime?.controllerV20 === true
    && evidence.runtime?.version === '1.0.25'
    && evidence.matrix?.rowCount === 13
    && evidence.matrix?.lighterListed === true
    && evidence.matrix?.lighterShareAvailable === true
    && evidence.matrix?.volumeDescending === true
    && evidence.pressure?.rowCount === 13
    && evidence.pressure?.lighterListed === true
    && evidence.pressure?.lighterLabel === 'Lighter';
} catch (error) {
  evidence.error = String(error?.message || error || '').slice(0, 400);
}

await browser.close();
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
