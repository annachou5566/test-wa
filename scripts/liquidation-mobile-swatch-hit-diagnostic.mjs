import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');
const outDir = path.resolve('artifacts/liquidation-mobile-swatch-hit-diagnostic');
await fs.mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true });
await context.addInitScript(() => localStorage.setItem('wave_alpha_legal_accepted', 'qa-scope-fixture'));
const page = await context.newPage();
const pageErrors = [];
page.on('pageerror', error => { if (pageErrors.length < 8) pageErrors.push(String(error?.message || error).slice(0, 240)); });

await page.goto(`${target}/?liquidationTest=1`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
await page.waitForFunction(() => typeof window.CryptoMarket?.onTabActivated === 'function' && typeof window.WaveCryptoLiquidation?.activate === 'function' && window.WaveCryptoLiquidation?.mounted === true, null, { timeout: 30_000, polling: 80 });
await page.evaluate(() => {
  const view = document.getElementById('crypto-market-view');
  if (!view) throw new Error('crypto-market-view missing');
  view.style.display = 'block';
  window.CryptoMarket.onTabActivated();
  window.WaveCryptoLiquidation.activate();
});
await page.waitForFunction(() => window.WaveCryptoLiquidation?.audit?.()?.active === true && window.WaveLiquidationThemeUiSyncAudit, null, { timeout: 35_000, polling: 100 });
await page.locator('#wa-liq-theme-trigger').click({ timeout: 10_000 });
await page.waitForTimeout(150);

const diagnostic = await page.evaluate(() => {
  const swatch = document.querySelector('[data-wa-liq-palette="long"]');
  if (!swatch) return { error: 'long swatch missing' };
  const rect = swatch.getBoundingClientRect();
  const x = Math.max(1, Math.min(innerWidth - 1, rect.left + rect.width / 2));
  const y = Math.max(1, Math.min(innerHeight - 1, rect.top + rect.height / 2));
  const describe = el => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return {
      tag: el.tagName,
      id: el.id || null,
      className: typeof el.className === 'string' ? el.className.slice(0, 180) : null,
      role: el.getAttribute('role'),
      rows: el.getAttribute('rows'),
      placeholder: (el.getAttribute('placeholder') || '').slice(0, 120) || null,
      ariaHidden: el.getAttribute('aria-hidden'),
      display: s.display,
      visibility: s.visibility,
      opacity: s.opacity,
      pointerEvents: s.pointerEvents,
      position: s.position,
      zIndex: s.zIndex,
      rect: { left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) },
      insideThemePanel: Boolean(el.closest('#wa-liq-theme-panel')),
      insideFeedbackModal: Boolean(el.closest('#feedbackModal')),
    };
  };
  const stack = document.elementsFromPoint(x, y).slice(0, 12).map(describe);
  const textareas = [...document.querySelectorAll('textarea')]
    .map(describe)
    .filter(Boolean)
    .filter(item => item.display !== 'none' && item.visibility !== 'hidden' && Number(item.opacity) !== 0)
    .filter(item => x >= item.rect.left && x <= item.rect.left + item.rect.width && y >= item.rect.top && y <= item.rect.top + item.rect.height);
  const feedback = document.getElementById('feedbackModal');
  const feedbackStyle = feedback ? getComputedStyle(feedback) : null;
  return {
    viewport: { width: innerWidth, height: innerHeight },
    swatch: describe(swatch),
    point: { x: Math.round(x), y: Math.round(y) },
    top: stack[0] || null,
    stack,
    overlappingTextareas: textareas,
    feedbackModal: feedback ? {
      className: feedback.className,
      ariaHidden: feedback.getAttribute('aria-hidden'),
      display: feedbackStyle.display,
      visibility: feedbackStyle.visibility,
      pointerEvents: feedbackStyle.pointerEvents,
      zIndex: feedbackStyle.zIndex,
    } : null,
    modalBackdrops: [...document.querySelectorAll('.modal-backdrop')].map(describe),
    bodyClass: document.body.className,
  };
});

const evidence = {
  schema: 'wave-liquidation-mobile-swatch-hit-diagnostic-v1',
  targetHost: new URL(target).hostname,
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  accountSecretsLogged: false,
  pageErrors,
  diagnostic,
};

await page.screenshot({ path: path.join(outDir, 'mobile-swatch-hit.png'), fullPage: false });
await fs.writeFile(path.join(outDir, 'evidence.json'), JSON.stringify(evidence, null, 2));
console.log(JSON.stringify(evidence, null, 2));
await context.close();
await browser.close();
