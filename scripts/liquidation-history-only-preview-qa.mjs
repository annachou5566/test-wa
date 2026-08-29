import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');

const profiles = [
  { name: 'desktop-1440x900', context: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 } },
  { name: 'mobile-390x844-touch', context: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true } },
];
const ranges = [
  { key: '90d', exactRows: 90 },
  { key: '7d', exactRows: 7 },
  { key: '30d', exactRows: 30 },
  { key: '90d', exactRows: 90 },
  { key: 'all', minRows: 90 },
];

const browser = await chromium.launch({ headless: true });
const evidence = {
  schema: 'wave-liquidation-history-only-preview-qa-v1',
  targetHost: new URL(target).hostname,
  ignoresPriceAvailability: true,
  rawRowsLogged: false,
  moneyValuesLogged: false,
  screenshotsStored: false,
  profiles: [],
};
let failed = false;

for (const profile of profiles) {
  const context = await browser.newContext(profile.context);
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(String(error?.message || error || '').slice(0, 200)));

  const profileEvidence = { profile: profile.name, ranges: [], pageErrors };
  try {
    await page.goto(`${target}/`, { waitUntil: 'domcontentloaded', timeout: 45_000 });
    await page.waitForFunction(() => Boolean(
      document.getElementById('cm-btn-liquidation')
      && window.WaveLiquidationPageAudit?.snapshot?.()?.mounted
    ), null, { timeout: 25_000 });

    const button = page.locator('#cm-btn-liquidation');
    await button.waitFor({ state: 'visible', timeout: 10_000 });
    if (!(await page.evaluate(() => window.WaveLiquidationPageAudit?.snapshot?.()?.active === true))) {
      await button.click();
      await page.waitForFunction(() => window.WaveLiquidationPageAudit?.snapshot?.()?.active === true, null, { timeout: 8_000 });
    }

    for (let index = 0; index < ranges.length; index++) {
      const range = ranges[index];
      if (index > 0) {
        const selector = `[data-cml-history-range="${range.key}"]`;
        const control = page.locator(selector);
        await control.waitFor({ state: 'visible', timeout: 8_000 });
        await control.click();
      }

      const started = Date.now();
      let ready = false;
      try {
        await page.waitForFunction(({ key, exactRows, minRows }) => {
          const state = window.WaveLiquidationPageAudit?.snapshot?.();
          const rowCount = Number(state?.historyRowCount) || 0;
          const rowsOk = Number.isFinite(exactRows) ? rowCount === exactRows : rowCount >= minRows;
          return Boolean(
            state?.active
            && state.historyRange === key
            && state.historyPayloadRange === key
            && state.historyLoading === false
            && rowsOk
          );
        }, range, { timeout: range.key === 'all' ? 30_000 : 22_000, polling: 150 });
        ready = true;
      } catch (_) {}

      const state = await page.evaluate(() => {
        const audit = window.WaveLiquidationPageAudit?.snapshot?.() || {};
        return {
          active: audit.active === true,
          historyRange: String(audit.historyRange || ''),
          historyPayloadRange: String(audit.historyPayloadRange || ''),
          historyRowCount: Number(audit.historyRowCount) || 0,
          historyLoading: audit.historyLoading === true,
          historyChartStyle: audit.historyChartStyle || null,
          historyChartLayout: audit.historyChartLayout || null,
          historyTooltipMode: audit.historyTooltipMode || null,
          lastError: audit.lastError == null ? null : String(audit.lastError).slice(0, 140),
        };
      });
      const rowPass = Number.isFinite(range.exactRows)
        ? state.historyRowCount === range.exactRows
        : state.historyRowCount >= range.minRows;
      const pass = ready
        && state.active
        && state.historyRange === range.key
        && state.historyPayloadRange === range.key
        && !state.historyLoading
        && rowPass
        && state.lastError == null;
      profileEvidence.ranges.push({
        range: range.key,
        durationMs: Date.now() - started,
        ...state,
        pass,
      });
      if (!pass) failed = true;
    }
  } catch (error) {
    profileEvidence.automationError = String(error?.message || error || '').slice(0, 300);
    failed = true;
  }

  if (pageErrors.length > 0) failed = true;
  evidence.profiles.push(profileEvidence);
  await context.close();
}

await browser.close();
evidence.pass = !failed && evidence.profiles.length === profiles.length;
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
