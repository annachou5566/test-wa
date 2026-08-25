import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim();
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev\/?$/i.test(target)) {
  throw new Error('WA_PREVIEW_URL must be an exact wave-alpha.pages.dev Preview origin');
}

const profiles = [
  {
    name: 'desktop-1440x900',
    context: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 },
  },
  {
    name: 'mobile-390x844-touch',
    context: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true },
  },
];

const browser = await chromium.launch({ headless: true });
const evidence = {
  schema: 'wave-liquidation-hardcore-preview-qa-v1',
  targetHost: new URL(target).hostname,
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  screenshotsStored: false,
  profiles: [],
};
let failed = false;

for (const profile of profiles) {
  const context = await browser.newContext(profile.context);
  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(String(message.text() || '').slice(0, 240));
  });
  page.on('pageerror', error => pageErrors.push(String(error?.message || error || '').slice(0, 240)));

  const started = Date.now();
  let result;
  try {
    await page.goto(target, { waitUntil: 'domcontentloaded', timeout: 45_000 });
    await page.locator('#wa-liq-debug-launcher').waitFor({ state: 'visible', timeout: 25_000 });
    await page.locator('#wa-liq-debug-launcher').click();
    await page.waitForFunction(() => {
      const button = document.getElementById('wa-liq-debug-run');
      const verdict = document.getElementById('wa-liq-debug-verdict')?.textContent || '';
      return button && !button.disabled && !/^READY\b/.test(verdict);
    }, null, { timeout: 120_000, polling: 200 });

    result = await page.evaluate(() => {
      const verdict = String(document.getElementById('wa-liq-debug-verdict')?.textContent || '');
      const rows = Array.isArray(window.WaveLiquidationDebug?.results)
        ? window.WaveLiquidationDebug.results
        : [];
      const audit = window.WaveLiquidationPageAudit?.snapshot?.() || null;
      return {
        verdict,
        productionCandidate: verdict.includes('PASS · PRODUCTION CANDIDATE'),
        steps: rows.map(row => ({
          name: String(row?.name || '').slice(0, 120),
          functionalPass: row?.functionalPass === true,
          elitePass: row?.elitePass === true,
          durationMs: Number.isFinite(Number(row?.durationMs)) ? Number(row.durationMs) : null,
          eliteBudgetMs: Number.isFinite(Number(row?.eliteBudgetMs)) ? Number(row.eliteBudgetMs) : null,
        })),
        final: audit ? {
          active: audit.active === true,
          historyRange: audit.historyRange || null,
          historyPayloadRange: audit.historyPayloadRange || null,
          historyRowCount: Number.isFinite(Number(audit.historyRowCount)) ? Number(audit.historyRowCount) : null,
          historyChartStyle: audit.historyChartStyle || null,
          historyChartLayout: audit.historyChartLayout || null,
          historyTooltipMode: audit.historyTooltipMode || null,
          historyLoading: audit.historyLoading === true,
          priceStatus: audit.priceStatus || null,
          pricePointCount: Number.isFinite(Number(audit.pricePointCount)) ? Number(audit.pricePointCount) : null,
          lastError: audit.lastError ? String(audit.lastError).slice(0, 180) : null,
        } : null,
      };
    });
  } catch (error) {
    result = {
      verdict: 'AUTOMATION_ERROR',
      productionCandidate: false,
      error: String(error?.message || error || '').slice(0, 360),
      steps: [],
      final: null,
    };
  }

  const profileEvidence = {
    profile: profile.name,
    durationMs: Date.now() - started,
    ...result,
    consoleErrorCount: consoleErrors.length,
    pageErrorCount: pageErrors.length,
    consoleErrors: consoleErrors.slice(0, 8),
    pageErrors: pageErrors.slice(0, 8),
  };
  evidence.profiles.push(profileEvidence);
  if (!profileEvidence.productionCandidate || profileEvidence.pageErrorCount > 0) failed = true;
  await context.close();
}

await browser.close();
evidence.pass = !failed && evidence.profiles.length === profiles.length;
console.log(JSON.stringify(evidence, null, 2));
process.exit(evidence.pass ? 0 : 1);
