// Bounded rerun after HL-off dataset-bounded source invalidator.
const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');

const endpoint = `${target}/api/liquidations/daily-history?range=90d&symbol=ALL&exchange=ALL`;
const attempts = [];
for (let index = 1; index <= 3; index++) {
  const started = performance.now();
  let summary;
  try {
    const response = await fetch(endpoint, {
      method: 'GET',
      headers: {
        Accept: 'application/json',
        'X-Wave-Client': 'liquidation-history-v1',
        Origin: target,
        Referer: `${target}/`,
      },
      cache: 'no-store',
    });
    const body = await response.json().catch(() => null);
    summary = {
      attempt: index,
      http: response.status,
      elapsedMs: Math.round(performance.now() - started),
      retryAfter: response.headers.get('retry-after'),
      schema: String(body?.schema || ''),
      range: String(body?.range || ''),
      rowCount: Number.isInteger(Number(body?.rowCount)) ? Number(body.rowCount) : null,
      error: typeof body?.error === 'string' ? body.error.slice(0, 100) : null,
    };
  } catch (error) {
    summary = {
      attempt: index,
      http: 0,
      elapsedMs: Math.round(performance.now() - started),
      retryAfter: null,
      schema: '',
      range: '',
      rowCount: null,
      error: String(error?.name || error).slice(0, 100),
    };
  }
  attempts.push(summary);
  if (summary.http === 200 && summary.schema === 'wave-liquidation-daily-chart-v1' && summary.range === '90d' && summary.rowCount === 90) break;
  if (index < 3) await new Promise(resolve => setTimeout(resolve, 5_000));
}

const recovered = attempts.some(item => item.http === 200 && item.schema === 'wave-liquidation-daily-chart-v1' && item.range === '90d' && item.rowCount === 90);
console.log(JSON.stringify({
  schema: 'wave-liquidation-daily-history-api-diagnostic-v1',
  targetHost: new URL(target).hostname,
  rawRowsLogged: false,
  moneyValuesLogged: false,
  attempts,
  classification: recovered
    ? (attempts[0]?.http === 200 ? 'healthy-first-attempt' : 'transient-recovered')
    : 'persistent-unavailable',
  recovered,
}, null, 2));

process.exit(recovered ? 0 : 1);
