import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');
const QA = 'hyperliquid-full-test-v2';

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1024, height: 768 } });
const page = await context.newPage();
const shell = `${target}/__wave_alpha_qa_shell__`;
await page.route(shell, route => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: '<!doctype html><title>QA</title>' }));
await page.goto(shell, { waitUntil: 'load', timeout: 45_000 });
await page.unroute(shell);
if (new URL(page.url()).origin !== new URL(target).origin) throw new Error('origin mismatch');

const result = await page.evaluate(async QA => {
  const started = performance.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8_000);
  try {
    const response = await fetch(
      '/api/liquidations/hyperliquid-preview-history?mode=source-diagnostic&from=2026-08-25&to=2026-08-29',
      {
        method: 'GET',
        headers: {
          Accept: 'application/json',
          'X-Wave-Client': 'liquidation-history-v1',
          'X-Wave-Preview-QA': QA,
        },
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
      },
    );
    const body = await response.json().catch(() => null);
    return { http: response.status, elapsedMs: Math.round(performance.now() - started), body };
  } catch (error) {
    return { http: 0, elapsedMs: Math.round(performance.now() - started), exception: String(error?.name || error).slice(0, 80), body: null };
  } finally {
    clearTimeout(timer);
  }
}, QA);

const body = result.body;
const members = Array.isArray(body?.realtime?.members) ? body.realtime.members : [];
const summarizedMembers = members.map(item => ({
  exchange: String(item?.exchange || ''),
  status: String(item?.status || ''),
  reason: item?.reason == null ? null : String(item.reason).slice(0, 80),
  elapsedMs: Number(item?.elapsedMs) || 0,
  coverageComplete: item?.coverageComplete === true,
  dateCount: Array.isArray(item?.dates) ? item.dates.length : 0,
}));
const reasonCounts = {};
for (const item of summarizedMembers) {
  const key = item.reason || item.status || 'unknown';
  reasonCounts[key] = (reasonCounts[key] || 0) + 1;
}

const evidence = {
  schema: 'wave-liquidation-forward-contention-preview-qa-v1',
  targetHost: new URL(target).hostname,
  rawRowsLogged: false,
  moneyValuesLogged: false,
  http: result.http,
  elapsedMs: result.elapsedMs,
  realtimeStatus: String(body?.realtime?.status || ''),
  realtimeReason: body?.realtime?.reason == null ? null : String(body.realtime.reason),
  realtimeRange: String(body?.realtime?.range || ''),
  memberCount: summarizedMembers.length,
  okMembers: summarizedMembers.filter(item => item.status === 'ok').length,
  reasonCounts,
  members: summarizedMembers,
  hyperliquid: {
    ok: body?.hyperliquid?.ok === true,
    errorCode: String(body?.hyperliquid?.errorCode || ''),
    elapsedMs: Number(body?.hyperliquid?.elapsedMs) || 0,
    rowCount: Number(body?.hyperliquid?.rowCount) || 0,
  },
  valuesExposed: body?.valuesExposed === true,
  mutationScope: String(body?.mutationScope || ''),
  exception: result.exception || null,
};
console.log(JSON.stringify(evidence, null, 2));

await context.close();
await browser.close();
const expectedDiagnostic = evidence.http === 200
  && evidence.memberCount === 12
  && evidence.mutationScope === 'none-read-only'
  && !evidence.valuesExposed;
process.exit(expectedDiagnostic ? 0 : 1);
