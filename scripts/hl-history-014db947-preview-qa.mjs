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
    || /^SOURCE [a-z0-9-]+ status=/.test(line)
    || /^=== FINAL SUMMARY ===$/.test(line)
    || /^(PASS:|durationMs=|RESULT=|SECURITY_SCOPE=|PROVIDER_FINALITY=|WHOLE_HYPERLIQUID_EVENT_COMPLETENESS=|CANONICAL_HISTORY_STRUCTURAL_CONTINUITY_NOT_WHOLE_PROVIDER_COMPLETENESS)/.test(line)
  );
  console.log('WAVE_ALPHA_SELF_QA_PUBLIC_EVIDENCE_V2');
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

  const stress = await page.evaluate(async () => {
    const route = '/api/liquidations/hyperliquid-preview-history';
    const ranges = ['7d', '30d', '90d', 'all'];
    const waves = [];
    for (let wave = 0; wave < 3; wave++) {
      const one = await Promise.all(ranges.map(async range => {
        const started = performance.now();
        const params = new URLSearchParams({
          range,
          exchange: 'hyperliquid-perp',
          qaStress: `${Date.now()}-${wave}-${range}`,
        });
        try {
          const res = await fetch(`${route}?${params}`, {
            method: 'GET',
            cache: 'no-store',
            credentials: 'same-origin',
            headers: {
              Accept: 'application/json',
              'X-Wave-Client': 'liquidation-history-v1',
              'X-Wave-Preview-QA': 'hyperliquid-full-test-v2',
            },
          });
          const body = await res.json().catch(() => null);
          return {
            range,
            status: res.status,
            ok: res.ok && body?.schema === 'wave-liquidation-daily-chart-v1',
            ms: Math.round(performance.now() - started),
          };
        } catch (error) {
          return { range, status: 0, ok: false, ms: Math.round(performance.now() - started), error: error?.name || 'error' };
        }
      }));
      waves.push(one);
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    return waves;
  });

  let stressOk = true;
  let stressMaxMs = 0;
  stress.forEach((wave, index) => {
    const statuses = wave.map(item => `${item.range}:${item.status}`).join(',');
    const maxMs = Math.max(...wave.map(item => item.ms));
    stressMaxMs = Math.max(stressMaxMs, maxMs);
    const ok = wave.every(item => item.ok && item.status === 200 && item.ms <= 4000);
    if (!ok) stressOk = false;
    console.log(`SELF_QA_CONCURRENT_WAVE_${index + 1}=${ok ? 'PASS' : 'FAIL'} maxMs=${maxMs} statuses=${statuses}`);
  });

  const contextTrials = await page.evaluate(async () => {
    const rows = [];
    for (let i = 0; i < 3; i++) {
      const started = performance.now();
      try {
        const res = await fetch(`/api/liquidations/context?window=24h&symbol=ALL&exchange=ALL&top=50&projection=full&qaContext=${Date.now()}-${i}`, {
          method: 'GET', cache: 'no-store', credentials: 'same-origin', headers: { Accept: 'application/json' },
        });
        const body = await res.json().catch(() => null);
        rows.push({ status: res.status, ok: res.ok && body?.exchangeCapabilities?.schema === 'wave-liquidation-exchange-capabilities-v1', ms: Math.round(performance.now() - started) });
      } catch (error) {
        rows.push({ status: 0, ok: false, ms: Math.round(performance.now() - started), error: error?.name || 'error' });
      }
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    return rows;
  });
  const contextStable = contextTrials.every(item => item.ok && item.status === 200 && item.ms <= 1500);
  console.log(`SELF_QA_CONTEXT_3_TRIALS=${contextStable ? 'PASS' : 'FAIL'} ${contextTrials.map(item => `${item.status}/${item.ms}ms`).join(',')}`);

  console.log(`SELF_QA_TIMEOUT_FAILURES=${timeoutFailures.length}`);
  console.log(`SELF_QA_HISTORY_RANGE_PASSES=${historyPasses.length}/4`);
  console.log(`SELF_QA_HISTORY_PERFORMANCE_PASSES=${performancePasses.length}/4`);
  console.log(`SELF_QA_INVALID_EXCHANGE=${invalidExchangePass ? 'PASS' : 'FAIL'}`);
  console.log(`SELF_QA_HEADERS=${headersPass ? 'PASS' : 'FAIL'}`);
  console.log(`SELF_QA_SOURCE_REALTIME=${sourceRealtimeOk ? 'PASS' : 'FAIL'}`);
  console.log(`SELF_QA_SOURCE_HL=${sourceHlOk ? 'PASS' : 'FAIL'}`);
  console.log(`SELF_QA_BROWSER_RUNTIME=${runtimeClean ? 'PASS' : 'FAIL'}`);
  console.log(`SELF_QA_CONCURRENT_STABILITY=${stressOk ? 'PASS' : 'FAIL'} maxMs=${stressMaxMs}`);
  console.log(`SELF_QA_CONTEXT_STABILITY=${contextStable ? 'PASS' : 'FAIL'}`);

  const stabilityPass = timeoutFailures.length === 0
    && historyPasses.length === 4
    && performancePasses.length === 4
    && invalidExchangePass
    && headersPass
    && sourceRealtimeOk
    && sourceHlOk
    && runtimeClean
    && stressOk
    && contextStable;
  console.log(`SELF_QA_RUNTIME_STABILITY=${stabilityPass ? 'PASS' : 'FAIL'}`);
  if (!stabilityPass) process.exitCode = 1;
} finally {
  await browser.close();
}
