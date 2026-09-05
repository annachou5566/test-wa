import { chromium } from 'playwright';

const url = 'https://wave-alpha.pages.dev/?waveGate7Diag=1';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1200 },
  locale: 'en-US',
});
const page = await context.newPage();

page.on('console', msg => {
  const text = msg.text();
  if (text.includes('ALPHA-LIVE') || text.includes('GATE7')) {
    console.log('[PAGE]', text);
  }
});

try {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });

  const run = page.locator('[data-g7-run]');
  const log = page.locator('[data-g7-log]');
  await run.waitFor({ state: 'visible', timeout: 15000 });

  // Gate 7 requires the running view. This only selects the documented UI view;
  // it does not alter transport budgets or runtime data.
  await page.evaluate(() => {
    try { localStorage.setItem('wave_active_tab', 'running'); } catch {}
  });

  await run.click();

  await page.waitForFunction(() => {
    const el = document.querySelector('[data-g7-log]');
    return Boolean(el && el.value.includes('=== GATE7_LIVE_VOLUME_RUNTIME_END ==='));
  }, null, { timeout: 90000 });

  const value = await log.inputValue();
  console.log(value);

  const pass = /GATE7_LIVE_VOLUME_RUNTIME=PASS(?:\n|$)/.test(value);
  console.log('GATE7_HEADLESS_QA=' + (pass ? 'PASS' : 'NOT_PASS'));

  if (!pass) process.exitCode = 2;
} catch (error) {
  console.error('GATE7_HEADLESS_QA_ERROR=' + (error?.stack || error));
  process.exitCode = 3;
} finally {
  await browser.close();
}
