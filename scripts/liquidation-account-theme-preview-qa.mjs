import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) {
  throw new Error('WA_PREVIEW_URL must be an exact wave-alpha.pages.dev Preview origin');
}

const outDir = path.resolve('artifacts/liquidation-account-theme-preview-qa');
await fs.mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const evidence = {
  schema: 'wave-liquidation-account-theme-preview-qa-v1',
  targetHost: new URL(target).hostname,
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  accountSecretsLogged: false,
  viewports: {},
  pass: false,
};

const forbiddenGuideTerms = /qualified set|official api|public channel|eventstore|degraded|snapshot liquidation/i;

async function activateLiquidation(page) {
  await page.goto(`${target}/?liquidationTest=1`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForFunction(() => {
    return typeof window.CryptoMarket?.onTabActivated === 'function'
      && typeof window.WaveCryptoLiquidation?.activate === 'function'
      && window.WaveLiquidationTheme;
  }, null, { timeout: 30_000, polling: 80 });

  await page.evaluate(() => {
    const cryptoView = document.getElementById('crypto-market-view');
    if (!cryptoView) throw new Error('crypto-market-view missing');
    cryptoView.style.display = 'block';
    window.CryptoMarket.onTabActivated();
    window.WaveCryptoLiquidation.activate();
  });

  await page.waitForFunction(() => {
    const pageAudit = window.WaveLiquidationPageAudit?.snapshot?.();
    const theme = window.WaveLiquidationTheme?.audit?.();
    const guide = window.WaveLiquidationGuideAudit?.snapshot?.();
    const heatmap = window.WaveLiquidationExchangeHeatmapAudit?.snapshot?.();
    const rootSync = window.WaveLiquidationThemeRootSyncAudit?.snapshot?.();
    return pageAudit?.mounted === true
      && pageAudit?.active === true
      && theme?.themeOwner === true
      && guide?.mounted === true
      && heatmap?.mounted === true
      && rootSync?.synced === true;
  }, null, { timeout: 35_000, polling: 100 });
}

async function runBuiltInRegression(page) {
  const button = page.locator('#wa-liq-qa-run');
  if (await button.count() !== 1) return { available: false, pass: false };
  await button.click();
  await page.waitForFunction(() => {
    const status = document.getElementById('wa-liq-qa-status');
    const output = String(document.getElementById('wa-liq-qa-output')?.value || '');
    return status?.dataset?.state !== 'running'
      && /runState=(COMPLETE|TIMEOUT|STOPPED)/.test(output)
      && /summary=PASS:\d+ FAIL:\d+ TOTAL:\d+/.test(output);
  }, null, { timeout: 70_000, polling: 120 });
  return page.evaluate(() => {
    const output = String(document.getElementById('wa-liq-qa-output')?.value || '');
    const lines = output.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    const runState = lines.find(line => line.startsWith('runState=')) || null;
    const summary = lines.find(line => line.startsWith('summary=')) || null;
    const failures = lines.filter(line => /^\d+\s+FAIL\s+\|/.test(line)).map(line => line.slice(0, 220));
    return {
      available: true,
      runState,
      summary,
      failureCount: failures.length,
      failures,
      pass: runState === 'runState=COMPLETE' && Boolean(summary && /FAIL:0\b/.test(summary)),
    };
  });
}

async function runViewport(name, viewport, runRegression) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const result = { viewport, pass: false };

  try {
    await activateLiquidation(page);

    result.initial = await page.evaluate(() => {
      const root = document.getElementById('cm-liq-v');
      const theme = window.WaveLiquidationTheme.audit();
      const heatmap = window.WaveLiquidationExchangeHeatmapAudit.snapshot();
      const guide = window.WaveLiquidationGuideAudit.snapshot();
      const rootSync = window.WaveLiquidationThemeRootSyncAudit.snapshot();
      const guideText = String(document.getElementById('cml-guide')?.textContent || '');
      const internalTips = [...document.querySelectorAll('#cmlxh-shell .wa-hm-tip')];
      return {
        theme,
        heatmap,
        guide,
        rootSync,
        rootTheme: root?.dataset?.liqTheme || null,
        rootFracture: root?.dataset?.liqFracture || null,
        cssLong: getComputedStyle(document.documentElement).getPropertyValue('--liq-long').trim(),
        cssShort: getComputedStyle(document.documentElement).getPropertyValue('--liq-short').trim(),
        sharedTooltipCount: document.querySelectorAll('#cmlxh-tip').length,
        internalTooltipVisibleCount: internalTips.filter(node => getComputedStyle(node).display !== 'none').length,
        pickerCount: document.querySelectorAll('#wa-liq-theme-bar').length,
        guideHasForbiddenInternalTerms: /qualified set|official api|public channel|eventstore|degraded|snapshot liquidation/i.test(guideText),
        localStorageKeys: Object.keys(localStorage).sort(),
      };
    });

    await page.locator('#wa-liq-theme-trigger').click();
    await page.locator('[data-wa-liq-preset="trader"]').click();
    await page.waitForFunction(() => window.WaveLiquidationTheme?.current?.preset === 'trader');

    result.guestPreset = await page.evaluate(() => ({
      preset: window.WaveLiquidationTheme.current.preset,
      long: window.WaveLiquidationTheme.current.long,
      short: window.WaveLiquidationTheme.current.short,
      loginModalOpened: document.getElementById('loginModal')?.classList.contains('show') || false,
      lastPersistence: window.WaveLiquidationTheme.audit().lastPersistence,
    }));

    await page.locator('#wa-lt-long-text').fill('#A12B3C');
    await page.locator('#wa-lt-short-text').fill('#D4E5F6');
    await page.waitForFunction(() => window.WaveLiquidationTheme?.current?.long === '#A12B3C' && window.WaveLiquidationTheme?.current?.short === '#D4E5F6');
    if (await page.locator('#wa-lt-fracture').isChecked()) await page.locator('#wa-lt-fracture').uncheck();

    result.custom = await page.evaluate(() => {
      const root = document.getElementById('cm-liq-v');
      return {
        theme: window.WaveLiquidationTheme.current,
        cssLong: getComputedStyle(document.documentElement).getPropertyValue('--liq-long').trim(),
        cssShort: getComputedStyle(document.documentElement).getPropertyValue('--liq-short').trim(),
        rootFracture: root?.dataset?.liqFracture || null,
        loginModalOpened: document.getElementById('loginModal')?.classList.contains('show') || false,
        localStorageKeys: Object.keys(localStorage).sort(),
      };
    });

    await page.locator('#wa-liq-theme-trigger').click();
    await page.waitForFunction(() => document.getElementById('wa-liq-theme-panel')?.hidden === true);

    await page.waitForFunction(() => (window.WaveLiquidationExchangeHeatmapAudit?.snapshot?.()?.exchangeRows || 0) > 0, null, { timeout: 30_000, polling: 150 });
    const firstTile = page.locator('#cmlxh-main .wa-hm-tile').first();
    await firstTile.scrollIntoViewIfNeeded();
    if (name === 'mobile') {
      await firstTile.evaluate(tile => {
        const rect = tile.getBoundingClientRect();
        tile.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerType: 'touch', clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 }));
      });
    } else {
      await firstTile.hover();
    }
    await page.waitForFunction(() => document.getElementById('cmlxh-tip')?.dataset?.open === 'true', null, { timeout: 8_000, polling: 80 });
    result.tooltip = await page.evaluate(() => {
      const tip = document.getElementById('cmlxh-tip');
      const text = String(tip?.textContent || '');
      return {
        open: tip?.dataset?.open === 'true',
        portalCount: document.querySelectorAll('#cmlxh-tip').length,
        hasLong: text.includes('Long liquidation'),
        hasShort: text.includes('Short liquidation'),
        hasEvents: text.includes('Tổng sự kiện'),
      };
    });

    await page.evaluate(() => window.WaveLiquidationTheme.reset());
    await page.locator('#wa-liq-theme-trigger').click();
    await page.screenshot({ path: path.join(outDir, `${name}-theme-heatmap.png`), fullPage: false });
    await page.locator('#wa-lt-close').click();
    await page.locator('#cml-guide').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(outDir, `${name}-guide.png`), fullPage: false });

    result.previewSuppression = await page.evaluate(async () => {
      let simulated = false;
      try {
        currentUser = { id: 'qa-preview-only' };
        userProfile = { tracker_data: {} };
        simulated = true;
      } catch (_) {}
      if (!simulated) return { simulated: false, pass: false };
      const saveResult = await window.WaveLiquidationTheme.saveAccount(window.WaveLiquidationTheme.current);
      const audit = window.WaveLiquidationTheme.audit();
      try { currentUser = null; userProfile = null; } catch (_) {}
      return {
        simulated: true,
        saveResult,
        lastPersistence: audit.lastPersistence,
        previewWriteSuppressed: audit.previewWriteSuppressed,
        pass: saveResult?.saved === false && saveResult?.preview === true && audit.lastPersistence === 'preview-write-suppressed' && audit.previewWriteSuppressed === true,
      };
    });

    result.regression = runRegression ? await runBuiltInRegression(page) : { available: true, pass: true, skippedOnSecondaryViewport: true };

    const noNewThemeStorage = JSON.stringify(result.initial.localStorageKeys) === JSON.stringify(result.custom.localStorageKeys);
    result.checks = {
      defaultTheme: result.initial.theme?.preset === 'wave-alpha' && result.initial.theme?.long === '#E58900' && result.initial.theme?.short === '#00CEE5',
      previewHostRecognized: result.initial.theme?.previewWriteSuppressed === true,
      oneThemeOwner: result.initial.theme?.themeOwner === true && result.initial.rootSync?.themeOwner === false,
      rootSynced: result.initial.rootSync?.synced === true && result.initial.rootTheme === 'wave-alpha',
      guideMounted: result.initial.guide?.mounted === true && result.initial.guideHasForbiddenInternalTerms === false,
      heatmapMounted: result.initial.heatmap?.mounted === true && result.initial.sharedTooltipCount === 1 && result.initial.internalTooltipVisibleCount === 0,
      pickerSingle: result.initial.pickerCount === 1,
      guestPresetSessionOnly: result.guestPreset?.preset === 'trader' && result.guestPreset?.loginModalOpened === false && result.guestPreset?.lastPersistence === 'not-attempted',
      customColorsApplied: result.custom?.theme?.preset === 'custom' && result.custom?.cssLong === '#A12B3C' && result.custom?.cssShort === '#D4E5F6' && result.custom?.rootFracture === 'off',
      noGuestThemeLocalStorage: noNewThemeStorage,
      sharedTooltip: result.tooltip?.open === true && result.tooltip?.portalCount === 1 && result.tooltip?.hasLong && result.tooltip?.hasShort && result.tooltip?.hasEvents,
      previewAccountWriteSuppressed: result.previewSuppression?.pass === true,
      regression: result.regression?.pass === true,
    };
    result.pass = Object.values(result.checks).every(Boolean);
  } catch (error) {
    result.error = String(error?.message || error || '').slice(0, 500);
  }

  await context.close();
  return result;
}

try {
  evidence.viewports.desktop = await runViewport('desktop', { width: 1440, height: 900 }, true);
  evidence.viewports.mobile = await runViewport('mobile', { width: 390, height: 844 }, false);
  evidence.pass = evidence.viewports.desktop.pass === true && evidence.viewports.mobile.pass === true;
} catch (error) {
  evidence.error = String(error?.message || error || '').slice(0, 500);
}

await browser.close();
await fs.writeFile(path.join(outDir, 'evidence.json'), JSON.stringify(evidence, null, 2));
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
