const ORIGIN = 'https://27a195cc.wave-alpha.pages.dev';
const QA = {
  Accept: 'application/json',
  'X-Wave-Client': 'liquidation-history-v1',
  'X-Wave-Preview-QA': 'hyperliquid-full-test-v2',
  'Sec-Fetch-Site': 'same-origin',
  Origin: ORIGIN,
  Referer: ORIGIN + '/',
};

function clean(text) {
  return String(text || '').replace(/[\r\n]+/g, ' ').replace(/\s+/g, ' ').slice(0, 220);
}

async function probe(label, path, headers = QA) {
  const started = Date.now();
  try {
    const res = await fetch(ORIGIN + path, { method: 'GET', cache: 'no-store', headers });
    const text = await res.text();
    let error = '';
    try {
      const body = JSON.parse(text);
      if (body && typeof body === 'object') error = clean(body.error?.code || body.error || body.message || '');
    } catch (_) {
      const title = text.match(/<title[^>]*>([^<]*)<\/title>/i)?.[1] || '';
      error = clean(title || text.slice(0, 180));
    }
    console.log(`${label} status=${res.status} ms=${Date.now() - started} contentType=${clean(res.headers.get('content-type')) || 'none'} server=${clean(res.headers.get('server')) || 'none'} cfRay=${clean(res.headers.get('cf-ray')) || 'none'} error=${error || 'none'}`);
    return res.status;
  } catch (err) {
    console.log(`${label} status=ERR ms=${Date.now() - started} error=${clean(err?.name + ':' + err?.message)}`);
    return 0;
  }
}

await probe('CONTEXT', '/api/liquidations/context?window=24h&symbol=ALL&exchange=ALL&top=50&projection=full', { Accept: 'application/json', 'Sec-Fetch-Site': 'same-origin', Origin: ORIGIN, Referer: ORIGIN + '/' });
await probe('SOURCE', '/api/liquidations/hyperliquid-preview-history?mode=source-diagnostic&from=2026-08-25&to=2026-08-29');
for (let wave = 1; wave <= 3; wave++) {
  const statuses = await Promise.all(['7d','30d','90d','all'].map(range => probe(`W${wave}_${range}`, `/api/liquidations/hyperliquid-preview-history?range=${range}&exchange=hyperliquid-perp&probe=${Date.now()}-${wave}-${range}`)));
  console.log(`WAVE_${wave}_STATUSES=${statuses.join(',')}`);
  await new Promise(resolve => setTimeout(resolve, 300));
}
