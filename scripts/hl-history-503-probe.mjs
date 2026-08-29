const COORDINATOR = 'https://wave-alpha-liquidation-coordinator.wavealpha.workers.dev';
const MEMBERS = [
  'binance-usdm',
  'bybit-linear',
  'okx-swap',
  'gate-futures',
  'bitget-usdt-futures',
  'aster-perp',
  'htx-usdt-swap',
  'coinex-futures',
  'pacifica-perp',
  'backpack-perp',
  'bitfinex-derivatives',
  'deribit-futures',
];

function clean(text) {
  return String(text || '').replace(/[\r\n]+/g, ' ').replace(/\s+/g, ' ').slice(0, 220);
}

function isoDay(ms) {
  const n = Number(ms);
  return Number.isFinite(n) && n > 0 ? new Date(n).toISOString().slice(0, 10) : 'none';
}

async function probe(exchange) {
  const url = `${COORDINATOR}/history?range=7d&symbol=ALL&exchange=${encodeURIComponent(exchange)}`;
  const started = Date.now();
  try {
    const res = await fetch(url, { method: 'GET', cache: 'no-store', headers: { Accept: 'application/json' } });
    const body = await res.json();
    const observedDates = [...new Set((Array.isArray(body?.rows) ? body.rows : [])
      .filter(row => row?.totalUsd != null)
      .map(row => isoDay(row?.time))
      .filter(day => day !== 'none'))].sort();
    const basis = body?.dataBasis || {};
    const d = body?.diagnostics || {};
    console.log([
      `COORDINATOR exchange=${exchange}`,
      `http=${res.status}`,
      `ms=${Date.now() - started}`,
      `storage=${clean(body?.storage || 'none')}`,
      `observed=${observedDates.join(',') || 'none'}`,
      `availableFrom=${isoDay(body?.availableFrom)}`,
      `coverageFrom=${isoDay(basis.historyCoverageFrom)}`,
      `coverageComplete=${basis.historyCoverageComplete === true}`,
      `backfillComplete=${basis.historyBackfillComplete === true}`,
      `materializationGapHours=${Number(basis.historyMaterializationGapHoursTotal) || 0}`,
      `lateCount=${Number(basis.historyLateAfterFinalizationCount) || 0}`,
      `historyHoursRead=${Number(d.historyHoursRead) || 0}`,
      `recentMinutesRead=${Number(d.recentMinutesRead) || 0}`,
      `error=${clean(typeof body?.error === 'string' ? body.error : body?.error?.code || 'none')}`,
    ].join(' '));
  } catch (error) {
    console.log(`COORDINATOR exchange=${exchange} http=ERR ms=${Date.now() - started} error=${clean(error?.message || error)}`);
  }
}

console.log('COORDINATOR_HISTORY_COVERAGE_TRACE_V1');
for (const exchange of MEMBERS) await probe(exchange);
