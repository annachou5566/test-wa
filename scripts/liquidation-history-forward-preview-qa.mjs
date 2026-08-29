import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) {
  throw new Error('WA_PREVIEW_URL must be an exact wave-alpha.pages.dev Preview origin');
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1024, height: 768 } });
const page = await context.newPage();
const qaShellUrl = `${target}/__wave_alpha_qa_shell__`;
await page.route(qaShellUrl, async route => {
  await route.fulfill({
    status: 200,
    contentType: 'text/html; charset=utf-8',
    body: '<!doctype html><meta charset="utf-8"><title>Wave Alpha QA shell</title>',
  });
});
await page.goto(qaShellUrl, { waitUntil: 'load', timeout: 45_000 });
await page.unroute(qaShellUrl);
if (new URL(page.url()).origin !== new URL(target).origin) throw new Error('same-origin QA shell origin mismatch');

const result = await page.evaluate(async () => {
  const started = performance.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12_000);
  try {
    const response = await fetch('/api/liquidations/daily-history?range=7d&exchange=ALL', {
      method: 'GET',
      headers: { Accept: 'application/json', 'X-Wave-Client': 'liquidation-history-v1' },
      cache: 'no-store',
      signal: controller.signal,
    });
    const text = await response.text();
    let payload = null;
    try { payload = JSON.parse(text); } catch (_) {}
    const errors = Array.isArray(payload?.errors)
      ? payload.errors.slice(0, 3).map(item => ({
          code: item?.code == null ? null : String(item.code).slice(0, 40),
          message: item?.message == null ? null : String(item.message).slice(0, 160),
        }))
      : [];
    return {
      http: response.status,
      elapsedMs: Math.round(performance.now() - started),
      contentType: response.headers.get('content-type'),
      cfRayPresent: Boolean(response.headers.get('cf-ray')),
      bodyBytes: new TextEncoder().encode(text).byteLength,
      bodyKind: payload && typeof payload === 'object' ? 'json' : 'other',
      topLevelKeys: payload && typeof payload === 'object' && !Array.isArray(payload)
        ? Object.keys(payload).sort().slice(0, 20)
        : [],
      code: payload?.code == null ? null : String(payload.code).slice(0, 40),
      message: payload?.message == null ? null : String(payload.message).slice(0, 160),
      error: typeof payload?.error === 'string' ? payload.error.slice(0, 160) : null,
      errors,
      hasRows: Array.isArray(payload?.rows),
    };
  } catch (error) {
    return {
      http: 0,
      elapsedMs: Math.round(performance.now() - started),
      exception: String(error?.name || error || 'fetch-error').slice(0, 120),
    };
  } finally {
    clearTimeout(timer);
  }
});

console.log(JSON.stringify({
  schema: 'wave-liquidation-history-503-code-v1',
  targetHost: new URL(target).hostname,
  rawRowsLogged: false,
  moneyValuesLogged: false,
  ...result,
}, null, 2));
await context.close();
await browser.close();
process.exit(result.http === 200 ? 0 : 1);
