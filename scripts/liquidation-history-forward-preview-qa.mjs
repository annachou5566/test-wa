import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');
const QA = 'hyperliquid-full-test-v2';

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1024, height: 768 } });
const page = await context.newPage();
const shell = `${target}/__wave_alpha_qa_shell__`;
await page.route(shell, route => route.fulfill({
  status: 200,
  contentType: 'text/html; charset=utf-8',
  body: '<!doctype html><title>Wave Alpha QA</title>',
}));
await page.goto(shell, { waitUntil: 'load', timeout: 45_000 });
await page.unroute(shell);
if (new URL(page.url()).origin !== new URL(target).origin) throw new Error('origin mismatch');

const result = await page.evaluate(async QA => {
  async function timed(path, headers, timeoutMs = 12_000) {
    const started = performance.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(path, {
        method: 'GET',
        headers,
        cache: 'no-store',
        credentials: 'same-origin',
        signal: controller.signal,
      });
      const body = await response.json().catch(() => null);
      return { http: response.status, elapsedMs: Math.round(performance.now() - started), body };
    } catch (error) {
      return { http: 0, elapsedMs: Math.round(performance.now() - started), exception: String(error?.name || error).slice(0, 80), body: null };
    } finally {
      clearTimeout(timer);
    }
  }

  // Sequential by design: prove general service-binding RPC health first, then
  // Hyperliquid-only RPC without the 12-exchange History fan-out competing for the DO.
  const contextResult = await timed(
    '/api/liquidations/context?window=24h&symbol=ALL&exchange=ALL&top=5&projection=compact',
    { Accept: 'application/json' },
  );
  const contextBody = contextResult.body;
  const contextSummary = {
    http: contextResult.http,
    elapsedMs: contextResult.elapsedMs,
    transport: String(contextBody?.diagnostics?.contextTransport || ''),
    projectionFallback: contextBody?.diagnostics?.contextProjectionFallback === true,
    degraded: contextBody?.diagnostics?.degraded === true,
    hasContext: Boolean(contextBody?.context && typeof contextBody.context === 'object'),
    hasCapabilities: Array.isArray(contextBody?.exchangeCapabilities?.exchanges),
    error: typeof contextBody?.error === 'string' ? contextBody.error.slice(0, 100) : null,
    exception: contextResult.exception || null,
  };

  const hlResult = await timed(
    '/api/liquidations/hyperliquid-preview-history?mode=rpc-diagnostic&from=2026-08-25&to=2026-08-29',
    {
      Accept: 'application/json',
      'X-Wave-Client': 'liquidation-history-v1',
      'X-Wave-Preview-QA': QA,
    },
    8_000,
  );
  const hlBody = hlResult.body;
  const hlSummary = {
    http: hlResult.http,
    elapsedMs: hlResult.elapsedMs,
    ok: hlBody?.ok === true,
    transport: String(hlBody?.transport || ''),
    errorCode: String(hlBody?.errorCode || ''),
    rowCount: Number(hlBody?.rowCount) || 0,
    dateCount: Array.isArray(hlBody?.dates) ? hlBody.dates.length : 0,
    availableDateCount: Array.isArray(hlBody?.availableDates) ? hlBody.availableDates.length : 0,
    staleDateCount: Array.isArray(hlBody?.staleDates) ? hlBody.staleDates.length : 0,
    exception: hlResult.exception || null,
  };

  return { context: contextSummary, hyperliquid: hlSummary };
}, QA);

const contextDirectRpc = result.context.http === 200
  && result.context.transport === 'coordinator-service-binding-rpc-v1'
  && !result.context.projectionFallback
  && result.context.hasContext;
const hlAlonePass = result.hyperliquid.http === 200
  && result.hyperliquid.ok
  && result.hyperliquid.transport === 'liquidation-context-service-rpc'
  && !result.hyperliquid.errorCode;

console.log(JSON.stringify({
  schema: 'wave-liquidation-binding-isolation-preview-qa-v1',
  targetHost: new URL(target).hostname,
  rawRowsLogged: false,
  moneyValuesLogged: false,
  sequential: true,
  context: { ...result.context, directRpcPass: contextDirectRpc },
  hyperliquid: { ...result.hyperliquid, alonePass: hlAlonePass },
  classification: contextDirectRpc
    ? (hlAlonePass ? 'binding-and-hl-rpc-healthy-in-isolation' : 'binding-healthy-hl-rpc-specific-failure')
    : 'binding-or-general-coordinator-rpc-unhealthy',
  pass: contextDirectRpc && hlAlonePass,
}, null, 2));

await context.close();
await browser.close();
process.exit(contextDirectRpc && hlAlonePass ? 0 : 1);
