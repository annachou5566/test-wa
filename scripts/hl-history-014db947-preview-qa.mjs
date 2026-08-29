import { chromium } from 'playwright';

const PREVIEW = 'https://27a195cc.wave-alpha.pages.dev/?hyperliquidTest=1';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const unexpected = [];
page.on('pageerror', error => unexpected.push(String(error?.message || error)));
page.on('console', msg => {
  if (msg.type() === 'error') unexpected.push(`console:${msg.text()}`);
});

try {
  const response = await page.goto(PREVIEW, { waitUntil: 'domcontentloaded', timeout: 45_000 });
  if (!response || response.status() !== 200) throw new Error(`preview HTTP ${response?.status() ?? 'ERR'}`);

  await page.waitForSelector('#wa-hft-v3-run', { timeout: 30_000 });
  await page.click('#wa-hft-v3-run');
  await page.waitForFunction(() => {
    const text = document.querySelector('#wa-hft-v3-log')?.textContent || '';
    return text.includes('COPY_THIS_LOG_TO_CHATGPT=YES');
  }, null, { timeout: 150_000 });

  const log = await page.locator('#wa-hft-v3-log').textContent() || '';
  const lines = log.split(/\r?\n/);
  const publicEvidence = lines.filter(line =>
    /^(PASS|FAIL|WARN) \[/.test(line)
    || /^SOURCE_(?:REALTIME|HL) /.test(line)
    || /^=== FINAL SUMMARY ===$/.test(line)
    || /^(PASS:|durationMs=|RESULT=|SECURITY_SCOPE=|PROVIDER_FINALITY=|WHOLE_HYPERLIQUID_EVENT_COMPLETENESS=|CANONICAL_HISTORY_STRUCTURAL_CONTINUITY_NOT_WHOLE_PROVIDER_COMPLETENESS)/.test(line)
  );
  console.log('WAVE_ALPHA_SELF_QA_PUBLIC_EVIDENCE_V1');
  console.log(`previewHost=${new URL(PREVIEW).hostname}`);
  for (const line of publicEvidence) console.log(line);

  const timeoutFailures = lines.filter(line => /^FAIL \[(?:HISTORY|PRODUCT|INTEGRITY|SECURITY|CONTEXT)\]/.test(line) && /timeout|AbortError|HTTP=ERR/i.test(line));
  const historyPasses = lines.filter(line => /^PASS \[HISTORY\] HL (7D|30D|90D|ALL) exact\/null-safe/.test(line));
  const performancePasses = lines.filter(line => /^PASS \[PERFORMANCE\] HL (7D|30D|90D|ALL) API <= 4000ms/.test(line));
  const invalidExchangePass = lines.some(line => /^PASS \[SECURITY\] Invalid exchange fails closed/.test(line));
  const headersPass = lines.some(line => /^PASS \[SECURITY\] History no-store\/noindex\/nosniff\/same-origin headers/.test(line));
  const sourceHlOk = lines.some(line => /^SOURCE_HL status=ok /.test(line));
  const sourceRealtimeOk = lines.some(line => /^SOURCE_REALTIME status=ok /.test(line));
  const runtimeClean = lines.some(line => /^PASS \[RUNTIME\] No unexpected browser errors\/rejections/.test(line));

  console.log(`SELF_QA_TIMEOUT_FAILURES=${timeoutFailures.length}`);
  console.log(`SELF_QA_HISTORY_RANGE_PASSES=${historyPasses.length}/4`);
  console.log(`SELF_QA_HISTORY_PERFORMANCE_PASSES=${performancePasses.length}/4`);
  console.log(`SELF_QA_INVALID_EXCHANGE=${invalidExchangePass ? 'PASS' : 'FAIL'}`);
  console.log(`SELF_QA_HEADERS=${headersPass ? 'PASS' : 'FAIL'}`);
  console.log(`SELF_QA_SOURCE_REALTIME=${sourceRealtimeOk ? 'PASS' : 'FAIL'}`);
  console.log(`SELF_QA_SOURCE_HL=${sourceHlOk ? 'PASS' : 'FAIL'}`);
  console.log(`SELF_QA_BROWSER_RUNTIME=${runtimeClean ? 'PASS' : 'FAIL'}`);

  const stabilityPass = timeoutFailures.length === 0
    && historyPasses.length === 4
    && performancePasses.length === 4
    && invalidExchangePass
    && headersPass
    && sourceRealtimeOk
    && sourceHlOk
    && runtimeClean;
  console.log(`SELF_QA_RUNTIME_STABILITY=${stabilityPass ? 'PASS' : 'FAIL'}`);
  if (!stabilityPass) process.exitCode = 1;
} finally {
  await browser.close();
}
