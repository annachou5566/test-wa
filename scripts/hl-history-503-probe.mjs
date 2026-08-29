const ORIGIN = 'https://3879e8f9.wave-alpha.pages.dev';
const QA = {
  Accept: 'application/json',
  'X-Wave-Client': 'liquidation-history-v1',
  'X-Wave-Preview-QA': 'hyperliquid-full-test-v2',
  'Sec-Fetch-Site': 'same-origin',
  Origin: ORIGIN,
  Referer: ORIGIN + '/',
};
const PUBLIC = {
  Accept: 'application/json',
  'Sec-Fetch-Site': 'same-origin',
  Origin: ORIGIN,
  Referer: ORIGIN + '/',
};

function clean(text) {
  return String(text || '').replace(/[\r\n]+/g, ' ').replace(/\s+/g, ' ').slice(0, 260);
}

function isoDay(ms) {
  const n = Number(ms);
  return Number.isFinite(n) && n > 0 ? new Date(n).toISOString().slice(0, 10) : 'none';
}

function summarizeJson(body) {
  if (!body || typeof body !== 'object') return 'json=nonobject';
  const rawError = typeof body.error === 'string' ? body.error : body.error?.code || body.error?.message;
  const keys = Object.keys(body).sort().slice(0, 20).join(',');
  const fields = [
    ['ok', body.ok],
    ['status', body.status],
    ['reason', body.reason],
    ['code', body.code],
    ['error', rawError],
    ['message', body.message],
    ['retryable', body.retryable],
    ['storage', body.storage],
    ['range', body.range],
    ['selectedExchange', body.selectedExchange],
  ].filter(([, value]) => value !== undefined && value !== null && String(value) !== '')
   .map(([key, value]) => `${key}=${clean(value)}`)
   .join(' ');
  return `keys=${keys || 'none'}${fields ? ' ' + fields : ''}`;
}

async function request(path, headers = QA) {
  const started = Date.now();
  try {
    const res = await fetch(ORIGIN + path, { method: 'GET', cache: 'no-store', headers });
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch (_) {}
    return {
      status: res.status,
      ms: Date.now() - started,
      body,
      contentType: clean(res.headers.get('content-type')) || 'none',
      server: clean(res.headers.get('server')) || 'none',
      cfRay: clean(res.headers.get('cf-ray')) || 'none',
      raw: body ? '' : clean(text),
    };
  } catch (err) {
    return { status: 0, ms: Date.now() - started, error: clean(`${err?.name}:${err?.message}`) };
  }
}

function print(label, result) {
  if (!result.status) {
    console.log(`${label} status=ERR ms=${result.ms} error=${result.error || 'unknown'}`);
    return;
  }
  const detail = result.body ? summarizeJson(result.body) : `nonjson=${result.raw || 'empty'}`;
  console.log(`${label} status=${result.status} ms=${result.ms} contentType=${result.contentType} server=${result.server} cfRay=${result.cfRay} ${detail}`);
}

async function probe(label, path, headers = QA) {
  const result = await request(path, headers);
  print(label, result);
  return result;
}

async function historyMeta(exchange, day) {
  const path = `/api/liquidations/history?range=1d&symbol=ALL&exchange=${encodeURIComponent(exchange)}&day=${day}`;
  const result = await request(path, PUBLIC);
  const body = result.body || {};
  const d = body.diagnostics || {};
  const basis = body.dataBasis || {};
  console.log([
    `ARCHIVE exchange=${exchange}`,
    `day=${day}`,
    `http=${result.status || 'ERR'}`,
    `ms=${result.ms}`,
    `exactDay=${clean(body.exactDay || 'none')}`,
    `storage=${clean(body.storage || 'none')}`,
    `availableFrom=${isoDay(body.availableFrom)}`,
    `firstObserved=${isoDay(body.firstObservedAt)}`,
    `lastObserved=${isoDay(body.lastObservedAt)}`,
    `historyObjects=${Number(d.historyObjects) || 0}`,
    `eligible=${Number(d.eligibleHistoryObjects) || 0}`,
    `selected=${Number(d.selectedHistoryObjects) || 0}`,
    `readFailures=${Number(d.historyObjectReadFailures) || 0}`,
    `realtimeBuckets=${Number(d.realtimeBuckets) || 0}`,
    `partial=${basis.historyReadPartial === true}`,
    `fallback=${clean(basis.realtimeFallbackError || 'none')}`,
  ].join(' '));
  return result;
}

console.log('ROOTCAUSE_PROBE_V3');
await probe('SOURCE', '/api/liquidations/hyperliquid-preview-history?mode=source-diagnostic&from=2026-08-25&to=2026-08-29');

for (const day of ['2026-08-25', '2026-08-26', '2026-08-27', '2026-08-28']) {
  await historyMeta('bybit-linear', day);
}
await historyMeta('binance-usdm', '2026-08-25');
await historyMeta('binance-usdm', '2026-08-28');
await historyMeta('gate-futures', '2026-08-25');
await historyMeta('gate-futures', '2026-08-28');

for (let wave = 1; wave <= 4; wave++) {
  const contextPromise = probe(`C${wave}`, '/api/liquidations/context?window=24h&symbol=ALL&exchange=ALL&top=50&projection=full', PUBLIC);
  const historyPromises = ['7d', '30d', '90d', 'all'].map(range =>
    probe(`W${wave}_${range}`, `/api/liquidations/hyperliquid-preview-history?range=${range}&exchange=hyperliquid-perp&probe=${Date.now()}-${wave}-${range}`)
  );
  const results = await Promise.all([contextPromise, ...historyPromises]);
  console.log(`WAVE_${wave}_STATUSES=${results.map(r => r.status || 'ERR').join(',')}`);
  await new Promise(resolve => setTimeout(resolve, 250));
}
