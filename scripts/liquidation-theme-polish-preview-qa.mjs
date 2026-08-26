import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');

const outDir = path.resolve('artifacts/liquidation-theme-polish-preview-qa');
await fs.mkdir(outDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const evidence = {
  schema: 'wave-liquidation-theme-polish-preview-qa-v1',
  targetHost: new URL(target).hostname,
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  accountSecretsLogged: false,
  desktop: null,
  mobile: null,
  pass: false,
};

async function clearFirstVisitLegal(page) {
  await page.waitForTimeout(1300);
  const shown = await page.locator('#legal-modal-overlay.show').count();
  if (shown) {
    await page.evaluate(() => document.getElementById('btn-accept-legal')?.click());
    await page.waitForFunction(() => !document.getElementById('legal-modal-overlay')?.classList.contains('show'), null, { timeout: 5000, polling: 60 });
  }
}

async function activate(page) {
  await page.goto(`${target}/?liquidationTest=1`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await clearFirstVisitLegal(page);
  await page.waitForFunction(() => typeof window.CryptoMarket?.onTabActivated === 'function' && typeof window.WaveCryptoLiquidation?.activate === 'function', null, { timeout: 30_000, polling: 80 });
  await page.evaluate(() => {
    const view = document.getElementById('crypto-market-view');
    if (!view) throw new Error('crypto-market-view missing');
    view.style.display = 'block';
    window.CryptoMarket.onTabActivated();
    window.WaveCryptoLiquidation.activate();
  });
  await page.waitForFunction(() => {
    const a = window.WaveCryptoLiquidation?.audit?.();
    return a?.active === true
      && window.WaveLiquidationThemeUiSyncAudit
      && window.WaveLiquidationHistoryDefaultsAudit
      && window.WaveLiquidationGuideAudit?.snapshot?.()?.mounted === true
      && window.WaveLiquidationExchangeHeatmapAudit?.snapshot?.()?.mounted === true;
  }, null, { timeout: 35_000, polling: 100 });
  await page.waitForFunction(() => {
    const overlay = document.getElementById('loading-overlay');
    if (!overlay) return true;
    const s = getComputedStyle(overlay), r = overlay.getBoundingClientRect();
    return overlay.hidden || s.display === 'none' || s.visibility === 'hidden' || s.pointerEvents === 'none' || r.width === 0 || r.height === 0;
  }, null, { timeout: 35_000, polling: 100 }).catch(() => {});
}

async function inspectViewport(name, viewport) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1, isMobile: name === 'mobile', hasTouch: name === 'mobile' });
  const page = await context.newPage();
  const result = { viewport, pass: false };
  try {
    await activate(page);
    await page.waitForFunction(() => (window.WaveLiquidationExchangeHeatmapAudit?.snapshot?.()?.exchangeRows || 0) > 0, null, { timeout: 25_000, polling: 120 }).catch(() => {});

    result.initial = await page.evaluate(() => {
      const audit = window.WaveCryptoLiquidation?.audit?.() || {};
      const defaults = window.WaveLiquidationHistoryDefaultsAudit?.snapshot?.() || {};
      const sync = window.WaveLiquidationThemeUiSyncAudit?.snapshot?.() || {};
      const guide = document.getElementById('cml-guide');
      const latest = document.getElementById('cml-guide-latest');
      const main = document.getElementById('cml-guide-snapshot');
      const p = guide?.querySelector('.cml-guide-card p');
      const noteText = String(guide?.querySelector('.cml-guide-sub')?.textContent || '').trim();
      const font = el => el ? parseFloat(getComputedStyle(el).fontSize || '0') : 0;
      return {
        audit: {
          historyTooltipMode: audit.historyTooltipMode,
          historyChartStyle: audit.historyChartStyle,
          historyChartLayout: audit.historyChartLayout,
        },
        defaults,
        sync,
        noteText,
        guideFontSizes: { live: font(main), latest: font(latest), body: font(p) },
        latestWhiteSpace: latest ? getComputedStyle(latest).whiteSpace : null,
        wavePicker: Boolean(window.WaveColorPicker?.open),
      };
    });

    await page.locator('#wa-liq-theme-trigger').click();
    const panelProbe = await page.evaluate(() => {
      const panel = document.getElementById('wa-liq-theme-panel');
      const rect = panel?.getBoundingClientRect();
      if (!panel || !rect || panel.hidden) return { visible: false };
      const x = Math.min(innerWidth - 4, Math.max(4, rect.left + Math.min(50, rect.width / 2)));
      const y = Math.min(innerHeight - 4, Math.max(4, rect.top + Math.min(50, rect.height / 2)));
      const top = document.elementFromPoint(x, y);
      return {
        visible: true,
        zIndex: getComputedStyle(panel).zIndex,
        hitInside: Boolean(top && panel.contains(top)),
        nativeColorVisible: [...panel.querySelectorAll('input[type="color"]')].some(el => getComputedStyle(el).display !== 'none'),
        paletteButtons: panel.querySelectorAll('[data-wa-liq-palette]').length,
      };
    });
    result.panel = panelProbe;

    await page.locator('[data-wa-liq-palette="long"]').click();
    result.wavePicker = await page.evaluate(() => ({
      shown: document.getElementById('wa-ucp')?.classList.contains('show') === true,
      overlayShown: getComputedStyle(document.getElementById('wa-ucp-overlay')).display !== 'none',
    }));
    await page.evaluate(() => window.WaveColorPicker?.close?.());

    await page.locator('[data-wa-liq-preset="trader"]').click();
    await page.waitForFunction(() => window.WaveLiquidationTheme?.current?.preset === 'trader', null, { timeout: 5000 });
    await page.waitForTimeout(180);

    result.themePropagation = await page.evaluate(() => {
      const rgb = el => el ? getComputedStyle(el) : null;
      const longCard = document.querySelector('.cml-window-card[data-liq-dominant="long"]');
      const shortCard = document.querySelector('.cml-window-card[data-liq-dominant="short"]');
      const ranking = document.querySelector('#cml-ranking-body tr');
      const liveLong = document.querySelector('.cml-live-row.long');
      const liveShort = document.querySelector('.cml-live-row.short');
      const exchangeLong = document.querySelector('.cmlx-long');
      const exchangeShort = document.querySelector('.cmlx-short');
      const balance = document.querySelector('.cmlx-balance-track');
      const tokenBars = document.querySelector('.cmlpt-source-bar');
      const historyLong = document.querySelector('#cml-history-chart [data-cml-bar="long"],#cml-history-chart [data-wc-liq-bar="long"],#cml-history-chart [data-wc-liq-stack="long"]');
      const historyShort = document.querySelector('#cml-history-chart [data-cml-bar="short"],#cml-history-chart [data-wc-liq-bar="short"],#cml-history-chart [data-wc-liq-stack="short"]');
      const styleText = document.getElementById('wa-liq-theme-ui-sync-css')?.textContent || '';
      const containsGreen = value => /14\s*,\s*203\s*,\s*129|rgb\(14,\s*203,\s*129\)|#0ECB81/i.test(String(value || ''));
      const containsRed = value => /246\s*,\s*70\s*,\s*93|rgb\(246,\s*70,\s*93\)|#F6465D/i.test(String(value || ''));
      return {
        rootLong: getComputedStyle(document.documentElement).getPropertyValue('--liq-long').trim(),
        rootShort: getComputedStyle(document.documentElement).getPropertyValue('--liq-short').trim(),
        longCard: longCard ? containsGreen(rgb(longCard).backgroundImage + ' ' + rgb(longCard).borderColor) : null,
        shortCard: shortCard ? containsRed(rgb(shortCard).backgroundImage + ' ' + rgb(shortCard).borderColor) : null,
        ranking: ranking ? containsGreen(rgb(ranking).backgroundImage) && containsRed(rgb(ranking).backgroundImage) : null,
        liveLong: liveLong ? containsGreen(getComputedStyle(liveLong, '::before').backgroundImage) : null,
        liveShort: liveShort ? containsRed(getComputedStyle(liveShort, '::before').backgroundImage) : null,
        exchangeLong: exchangeLong ? containsGreen(rgb(exchangeLong).color) : null,
        exchangeShort: exchangeShort ? containsRed(rgb(exchangeShort).color) : null,
        exchangeBalance: balance ? containsGreen(rgb(balance).backgroundImage) && containsRed(rgb(balance).backgroundImage) : null,
        tokenContract: styleText.includes('.cmlpt-source-bar i:first-child{background:var(--liq-long)') && styleText.includes('.cmlpt-source-bar i:last-child{background:var(--liq-short)'),
        historyLong: historyLong ? containsGreen(rgb(historyLong).fill + ' ' + rgb(historyLong).stroke) : null,
        historyShort: historyShort ? containsRed(rgb(historyShort).fill + ' ' + rgb(historyShort).stroke) : null,
      };
    });

    if (await page.locator('#wa-lt-fracture').isChecked() === false) await page.locator('#wa-lt-fracture').check();
    result.fracture = await page.evaluate(() => {
      const hero = document.querySelector('.cmlpc-hero');
      const tile = document.querySelector('.cmlxh .wa-hm-tile');
      const heroBg = hero ? getComputedStyle(hero, '::before').backgroundImage : '';
      const tileBg = tile ? getComputedStyle(tile, '::after').backgroundImage : '';
      return { heroSvg: /data:image\/svg\+xml/i.test(heroBg), tileSvg: tile ? /data:image\/svg\+xml/i.test(tileBg) : null };
    });

    await page.evaluate(() => window.WaveColorPicker?.close?.());
    await page.locator('#wa-lt-close').click().catch(() => {});
    await page.screenshot({ path: path.join(outDir, `${name}-top.png`), fullPage: false });
    await page.locator('#cml-guide').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(outDir, `${name}-guide.png`), fullPage: false });

    const propagationValues = Object.values(result.themePropagation || {}).filter(v => v === true || v === false);
    result.checks = {
      syncModuleLoaded: result.initial.sync?.installed === true,
      wavePickerOwner: result.initial.wavePicker === true && result.initial.sync?.paletteOwner === true,
      internalNoteRemoved: result.initial.noteText === '' && result.initial.sync?.guideInternalNoteVisible === false,
      guideReadable: result.initial.guideFontSizes.live >= 13 && result.initial.guideFontSizes.latest >= 13 && result.initial.guideFontSizes.body >= 13,
      desktopLatestNoWrap: name !== 'desktop' || result.initial.latestWhiteSpace === 'nowrap',
      historyDefaults: result.initial.audit.historyTooltipMode === 'exchanges' && result.initial.audit.historyChartStyle === 'bar' && result.initial.audit.historyChartLayout === 'stacked',
      panelAboveContent: result.panel?.visible === true && result.panel?.hitInside === true,
      nativePickerRemoved: result.panel?.nativeColorVisible === false && result.panel?.paletteButtons === 2,
      wavePickerOpens: result.wavePicker?.shown === true && result.wavePicker?.overlayShown === true,
      themeTokensTrader: result.themePropagation?.rootLong === '#0ECB81' && result.themePropagation?.rootShort === '#F6465D',
      themePropagationObserved: propagationValues.length >= 5 && propagationValues.every(Boolean),
      fractureSvg: result.fracture?.heroSvg === true && (result.fracture?.tileSvg === true || result.fracture?.tileSvg === null),
    };
    result.pass = Object.values(result.checks).every(Boolean);
  } catch (error) {
    result.error = String(error?.message || error || '').slice(0, 500);
  }
  await context.close();
  return result;
}

try {
  evidence.desktop = await inspectViewport('desktop', { width: 1440, height: 900 });
  evidence.mobile = await inspectViewport('mobile', { width: 390, height: 844 });
  evidence.pass = evidence.desktop?.pass === true && evidence.mobile?.pass === true;
} catch (error) {
  evidence.error = String(error?.message || error || '').slice(0, 500);
}

await browser.close();
await fs.writeFile(path.join(outDir, 'evidence.json'), JSON.stringify(evidence, null, 2));
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
