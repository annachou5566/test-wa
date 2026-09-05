import { chromium } from 'playwright';

const url = 'https://wave-alpha.pages.dev/?waveGate7Diag=1';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1200 },
  locale: 'en-US',
});
const page = await context.newPage();

const interesting = /(?:alpha-live|wave-security|gate7-live-volume-diagnostic)/;

page.on('response', response => {
  const u = response.url();
  if (interesting.test(u)) {
    console.log('RESOURCE_HTTP=' + response.status() + ' ' + u);
  }
});

page.on('requestfailed', request => {
  const u = request.url();
  if (interesting.test(u)) {
    console.log('RESOURCE_FAILED=' + u + ' :: ' + String(request.failure()?.errorText || ''));
  }
});

page.on('pageerror', error => {
  console.log('PAGE_ERROR=' + String(error?.message || error));
});

try {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(5000);

  const state = await page.evaluate(() => ({
    href: location.href,
    scripts: Array.from(document.scripts)
      .map(s => s.src)
      .filter(src => /alpha-live|wave-security|gate7-live-volume-diagnostic/.test(src)),
    security: Boolean(globalThis.WaveSecurity?.installed),
    client: Boolean(globalThis.WaveAlphaLiveClient?.telemetry),
    bootstrap: Boolean(globalThis.WaveAlphaLiveBootstrap?.reconcile),
    chartTail: Boolean(globalThis.WaveAlphaChartTail?.project),
    diagnosticButton: Boolean(document.querySelector('[data-g7-run]')),
  }));

  console.log('STATE=' + JSON.stringify(state));

  const run = page.locator('[data-g7-run]');
  if (await run.count()) {
    await run.click();
    await page.waitForTimeout(1500);
    const log = await page.locator('[data-g7-log]').inputValue().catch(() => '');
    console.log(log);
  }
} finally {
  await browser.close();
}
