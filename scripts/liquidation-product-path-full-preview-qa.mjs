import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) {
  throw new Error('WA_PREVIEW_URL must be an exact wave-alpha.pages.dev Preview origin');
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await context.newPage();
const evidence = {
  schema: 'wave-liquidation-product-path-full-preview-qa-v1',
  targetHost: new URL(target).hostname,
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  pass: false,
};

try {
  await page.goto(`${target}/?liquidationTest=1`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForFunction(() => {
    return document.getElementById('wa-liq-qa-run')
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

  await page.waitForFunction(() => {
    const audit = window.WaveLiquidationPageAudit?.snapshot?.();
    return audit?.mounted === true
      && audit?.active === true
      && window.WaveLiquidationContext
      && window.WaveLiquidationExchangeAudit?.snapshot?.()?.mounted === true
      && window.WaveLiquidationTokenTopologyAudit?.snapshot?.()?.mounted === true;
  }, null, { timeout: 20_000, polling: 80 });

  await page.locator('#wa-liq-qa-run').click();
  await page.waitForFunction(() => {
    const status = document.getElementById('wa-liq-qa-status');
    const output = String(document.getElementById('wa-liq-qa-output')?.value || '');
    return status?.dataset?.state !== 'running'
      && /runState=(COMPLETE|TIMEOUT|STOPPED)/.test(output)
      && /summary=PASS:\d+ FAIL:\d+ TOTAL:\d+/.test(output);
  }, null, { timeout: 65_000, polling: 100 });

  const result = await page.evaluate(() => {
    const output = String(document.getElementById('wa-liq-qa-output')?.value || '');
    const lines = output.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    return {
      runState: lines.find(line => line.startsWith('runState=')) || null,
      summary: lines.find(line => line.startsWith('summary=')) || null,
      phase: lines.find(line => line.startsWith('phase=')) || null,
      targetCase: lines.find(line => line.includes('History 1D exchange tooltip follows pointer with real rows')) || null,
      failures: lines.filter(line => /^\d+\s+FAIL\s+\|/.test(line)).map(line => line.slice(0, 320)),
    };
  });

  evidence.runState = result.runState;
  evidence.summary = result.summary;
  evidence.phase = result.phase;
  evidence.targetCase = result.targetCase;
  evidence.failures = result.failures;
  evidence.targetCasePass = Boolean(result.targetCase && /\bPASS\s+\|/.test(result.targetCase));
  evidence.fullSuitePass = Boolean(result.summary && /FAIL:0\b/.test(result.summary) && result.runState === 'runState=COMPLETE');
  evidence.pass = evidence.targetCasePass && evidence.fullSuitePass;
} catch (error) {
  evidence.error = String(error?.message || error || '').slice(0, 360);
}

await context.close();
await browser.close();
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
