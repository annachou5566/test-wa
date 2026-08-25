import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');

const browser = await chromium.launch({ headless: true });
const evidence = {
  schema: 'wave-liquidation-final-mobile-audit-preview-qa-v1',
  targetHost: new URL(target).hostname,
  rawRowsLogged: false,
  exchangeTriplesLogged: false,
  priceValuesLogged: false,
  mobile: null,
  pageAudit: null,
  pass: false,
};

async function activate(page) {
  await page.goto(`${target}/?liquidationTest=1`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  await page.waitForTimeout(1200);
  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await page.waitForLoadState('domcontentloaded', { timeout: 10_000 }).catch(() => {});
      await page.waitForFunction(() => typeof window.CryptoMarket?.onTabActivated === 'function' && typeof window.WaveCryptoLiquidation?.activate === 'function', null, { timeout: 15_000, polling: 80 });
      await page.evaluate(() => {
        const v = document.getElementById('crypto-market-view');
        if (!v) throw new Error('crypto view missing');
        v.style.display = 'block';
        window.CryptoMarket.onTabActivated();
        window.WaveCryptoLiquidation.activate();
      });
      await page.waitForFunction(() => {
        const a = window.WaveCryptoLiquidation?.audit?.();
        return a?.active === true && a?.historyLoading === false && Number(a?.historyRowCount || 0) > 0;
      }, null, { timeout: 15_000, polling: 100 });
      return;
    } catch (error) {
      lastError = error;
      if (!/Execution context was destroyed|navigation|Timeout/i.test(String(error?.message || error))) throw error;
      await page.waitForTimeout(900);
    }
  }
  throw lastError || new Error('activation failed');
}

try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  await activate(page);
  await page.evaluate(async () => {
    const range = document.querySelector('[data-cml-history-range="1d"]');
    const mode = document.querySelector('[data-cml-tooltip-mode="exchanges"]');
    range?.click(); mode?.click();
    await window.WaveCryptoLiquidation.reloadHistory();
  });
  await page.waitForFunction(() => {
    const a = window.WaveCryptoLiquidation?.audit?.();
    return a?.historyRange === '1d' && a?.historyPayloadRange === '1d' && a?.historyTooltipMode === 'exchanges' && a?.historyLoading === false && !a?.lastError;
  }, null, { timeout: 15_000, polling: 80 });

  const mobile = await page.evaluate(async () => {
    const cap = document.querySelector('#cml-history-chart svg.wc-svg rect[id$="-cap"]');
    if (!cap) return { pass: false, error: 'capture rect missing' };
    const r = cap.getBoundingClientRect();
    const y = r.top + r.height * 0.5;
    const x1 = r.left + r.width * 0.22;
    const x2 = r.left + r.width * 0.72;
    const pointerId = 41;
    const fire = (type, x) => cap.dispatchEvent(new PointerEvent(type, {
      bubbles: true, cancelable: true, pointerId, pointerType: 'touch', isPrimary: true,
      clientX: x, clientY: y, buttons: type === 'pointerup' ? 0 : 1, pressure: type === 'pointerup' ? 0 : 0.5
    }));
    fire('pointerdown', x1);
    await new Promise(r => setTimeout(r, 280));
    fire('pointermove', x1);
    await new Promise(r => setTimeout(r, 80));
    const tip = document.getElementById('cml-exchange-tooltip');
    const visible = el => { if (!el) return false; const s=getComputedStyle(el), b=el.getBoundingClientRect(); return !el.hidden && s.display!=='none' && s.visibility!=='hidden' && b.width>0 && b.height>0; };
    const firstVisible = visible(tip);
    const firstDate = String(tip?.querySelector('.cml-exchange-tip-date')?.textContent || '').trim();
    const firstTransform = String(tip?.style?.transform || '');
    fire('pointermove', x2);
    await new Promise(r => setTimeout(r, 100));
    const secondVisible = visible(tip);
    const secondDate = String(tip?.querySelector('.cml-exchange-tip-date')?.textContent || '').trim();
    const secondTransform = String(tip?.style?.transform || '');
    const rows = [...(tip?.querySelectorAll('tbody tr') || [])];
    const realRows = rows.filter(row => {
      const cells=[...row.querySelectorAll('td')].map(c=>String(c.textContent||'').replace(/\s+/g,' ').trim());
      return cells.length>=3 && cells[0] && cells[1] && cells[2] && cells[1]!=='—' && cells[2]!=='—';
    }).length;
    fire('pointerup', x2);
    const moved = firstDate !== secondDate || firstTransform !== secondTransform;
    return { pass:firstVisible&&secondVisible&&moved&&realRows>0, holdMs:280, firstVisible, secondVisible, moved, realRows };
  });
  evidence.mobile = mobile;
  await context.close();
} catch (error) {
  evidence.mobile = { pass:false, error:String(error?.message||error||'').slice(0,360) };
}

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await context.addInitScript(() => {
    localStorage.setItem('wa-liquidation-chart-style', 'bar');
    localStorage.setItem('wa-liquidation-chart-layout', 'diverging');
    localStorage.setItem('wa-liquidation-chart-view', 'bar');
  });
  const page = await context.newPage();
  await activate(page);
  const audit = await page.evaluate(() => {
    const result = window.WaveLiquidationPageAudit?.run?.();
    if (!result) return { pass:false, error:'PageAudit unavailable' };
    const checks = { ...(result.checks || {}) };
    const priceRunnerException = checks.btcPriceAvailable === false && result.snapshot?.priceStatus === 'unavailable';
    const productChecks = { ...checks, btcPriceAvailable: checks.btcPriceAvailable === true || priceRunnerException };
    return {
      pass: Object.values(productChecks).every(Boolean),
      basePassed: result.passed === true,
      priceRunnerException,
      failedProductChecks: Object.entries(productChecks).filter(([,v])=>v!==true).map(([k])=>k),
      momentumVersion: result.snapshot?.momentumVersion || null,
      momentumCheck: checks.momentumUsesObservedMinutes === true,
      barCheck: checks.longShortShareOneColumn === true,
      rendererBarPairs: Number(result.snapshot?.rendererBarPairs || 0),
      rendererBarMisalignedPairs: Number(result.snapshot?.rendererBarMisalignedPairs || 0),
      priceStatus: result.snapshot?.priceStatus || null,
      pricePointCount: Number(result.snapshot?.pricePointCount || 0)
    };
  });
  evidence.pageAudit = audit;
  await context.close();
} catch (error) {
  evidence.pageAudit = { pass:false, error:String(error?.message||error||'').slice(0,360) };
}

await browser.close();
evidence.pass = evidence.mobile?.pass === true && evidence.pageAudit?.pass === true;
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
