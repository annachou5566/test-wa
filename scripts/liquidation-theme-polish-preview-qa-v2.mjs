import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');
const outDir = path.resolve('artifacts/liquidation-theme-polish-preview-qa-v2');
await fs.mkdir(outDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const evidence = {
  schema: 'wave-liquidation-theme-polish-readiness-diagnostic-v1',
  targetHost: new URL(target).hostname,
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  accountSecretsLogged: false,
  diagnosticOnly: true,
  viewports: {},
};

async function diagnose(name, viewport) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1, isMobile: name === 'mobile', hasTouch: name === 'mobile' });
  await context.addInitScript(() => localStorage.setItem('wave_alpha_legal_accepted', 'qa-scope-fixture'));
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', error => { if (pageErrors.length < 8) pageErrors.push(String(error?.message || error).slice(0, 280)); });
  page.on('console', msg => { if (msg.type() === 'error' && consoleErrors.length < 8) consoleErrors.push(String(msg.text() || '').slice(0, 280)); });
  const result = { viewport, pageErrors, consoleErrors };
  try {
    await page.goto(`${target}/?liquidationTest=1`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
    await page.waitForFunction(() => typeof window.CryptoMarket?.onTabActivated === 'function' && typeof window.WaveCryptoLiquidation?.activate === 'function', null, { timeout: 30_000, polling: 80 });
    await page.evaluate(() => {
      const view = document.getElementById('crypto-market-view');
      if (view) view.style.display = 'block';
      window.CryptoMarket?.onTabActivated?.();
      window.WaveCryptoLiquidation?.activate?.();
    });
    await page.waitForTimeout(12_000);
    result.state = await page.evaluate(() => {
      const scriptNames = [...document.scripts].map(s => String(s.src || '').split('/').pop()).filter(Boolean);
      const audit = window.WaveCryptoLiquidation?.audit?.() || null;
      return {
        overviewBootstrap: Boolean(window.__waCryptoMarketTabBootstrapLoaded),
        cryptoMarket: Boolean(window.CryptoMarket),
        liquidationController: Boolean(window.WaveCryptoLiquidation),
        liquidationActive: audit?.active === true,
        themeOwner: Boolean(window.WaveLiquidationTheme),
        themeRootSync: Boolean(window.WaveLiquidationThemeRootSyncAudit),
        historyDefaults: Boolean(window.WaveLiquidationHistoryDefaultsAudit),
        context: Boolean(window.WaveLiquidationContext),
        guideAudit: Boolean(window.WaveLiquidationGuideAudit),
        guideMounted: window.WaveLiquidationGuideAudit?.snapshot?.()?.mounted === true,
        heatmapAudit: Boolean(window.WaveLiquidationExchangeHeatmapAudit),
        heatmapMounted: window.WaveLiquidationExchangeHeatmapAudit?.snapshot?.()?.mounted === true,
        themeUiSyncAudit: Boolean(window.WaveLiquidationThemeUiSyncAudit),
        themeUiSyncFlag: Boolean(window.__waLiquidationThemeUiSyncInstalled),
        historyDefaultsFlag: Boolean(window.__waLiquidationHistoryDefaultsInstalled),
        themeSyncScriptTag: scriptNames.some(name => name.startsWith('theme-ui-sync.js')),
        historyDefaultsScriptTag: scriptNames.some(name => name.startsWith('history-defaults.js')),
        overviewScriptTag: scriptNames.some(name => name.startsWith('overview-tab.js')),
        guideNode: Boolean(document.getElementById('cml-guide')),
        heatmapNode: Boolean(document.getElementById('cmlxh-shell')),
        pickerNode: Boolean(document.getElementById('wa-liq-theme-bar')),
        historyStyle: audit?.historyChartStyle || null,
        historyLayout: audit?.historyChartLayout || null,
        historyTooltipMode: audit?.historyTooltipMode || null,
      };
    });
  } catch (error) {
    result.error = String(error?.message || error || '').slice(0, 500);
  }
  await context.close();
  return result;
}

evidence.viewports.desktop = await diagnose('desktop', { width: 1440, height: 900 });
evidence.viewports.mobile = await diagnose('mobile', { width: 390, height: 844 });
await browser.close();
await fs.writeFile(path.join(outDir, 'evidence.json'), JSON.stringify(evidence, null, 2));
console.log(JSON.stringify(evidence, null, 2));
process.exit(0);
