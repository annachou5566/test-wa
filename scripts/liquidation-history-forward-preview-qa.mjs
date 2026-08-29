import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) {
  throw new Error('WA_PREVIEW_URL must be an exact wave-alpha.pages.dev Preview origin');
}

const expectedMembers = new Set([
  'binance-usdm', 'bybit-linear', 'okx-swap', 'gate-futures',
  'bitget-usdt-futures', 'aster-perp', 'htx-usdt-swap', 'coinex-futures',
  'pacifica-perp', 'backpack-perp', 'bitfinex-derivatives', 'deribit-futures',
  'hyperliquid-perp',
]);
const forwardDates = ['2026-08-25', '2026-08-26', '2026-08-27', '2026-08-28', '2026-08-29'];
const ranges = ['7d', '30d', '90d', 'all'];
const setEqual = (left, right) => left.size === right.size && [...left].every(value => right.has(value));

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1365, height: 900 } });
const page = await context.newPage();
const providerRequests = [];
page.on('request', request => {
  try {
    const url = new URL(request.url());
    if (/hyperliquid\.xyz$/i.test(url.hostname) || /api\.hyperliquid/i.test(url.hostname)) {
      providerRequests.push(`${url.protocol}//${url.hostname}`);
    }
  } catch (_) {}
});

const evidence = {
  schema: 'wave-liquidation-history-forward-preview-qa-v1',
  targetHost: new URL(target).hostname,
  generatedAt: new Date().toISOString(),
  rawRowsLogged: false,
  moneyValuesLogged: false,
  ranges: [],
  days: [],
  providerRequestCount: 0,
  pass: false,
};
let failed = false;

try {
  await page.goto(`${target}/`, { waitUntil: 'domcontentloaded', timeout: 45_000 });

  const rangeResults = await page.evaluate(async inputRanges => {
    const out = [];
    for (const range of inputRanges) {
      const started = performance.now();
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 12_000);
      try {
        const response = await fetch(`/api/liquidations/daily-history?range=${encodeURIComponent(range)}&exchange=ALL`, {
          method: 'GET',
          headers: { Accept: 'application/json', 'X-Wave-Client': 'liquidation-history-v1' },
          signal: controller.signal,
          cache: 'no-store',
        });
        const body = await response.json().catch(() => null);
        const members = Array.isArray(body?.aggregateMembers) ? body.aggregateMembers.map(String) : [];
        const historyStatus = body?.historySourceStatus && typeof body.historySourceStatus === 'object'
          ? {
              realtime: String(body.historySourceStatus.realtime || ''),
              hyperliquid: String(body.historySourceStatus.hyperliquid || ''),
              realtimeRange: body.historySourceStatus.realtimeRange == null ? null : String(body.historySourceStatus.realtimeRange),
            }
          : null;
        out.push({
          range,
          http: response.status,
          elapsedMs: Math.round(performance.now() - started),
          schema: String(body?.schema || ''),
          rowCount: Number.isFinite(Number(body?.rowCount)) ? Number(body.rowCount) : null,
          actualRows: Array.isArray(body?.rows) ? body.rows.length : null,
          start: body?.dateRange?.start || null,
          end: body?.dateRange?.end || null,
          aggregateMemberCount: members.length,
          aggregateMembers: members,
          historyOnlyMembers: Array.isArray(body?.historyOnlyMembers) ? body.historyOnlyMembers.map(String) : [],
          standaloneExcludedFromAll: Array.isArray(body?.standaloneExcludedFromAll) ? body.standaloneExcludedFromAll.map(String) : [],
          historyAggregateComplete: typeof body?.historyAggregateComplete === 'boolean' ? body.historyAggregateComplete : null,
          historyStatus,
          projection: response.headers.get('x-wave-history-projection'),
          cacheControl: response.headers.get('cache-control'),
          corp: response.headers.get('cross-origin-resource-policy'),
          nosniff: response.headers.get('x-content-type-options'),
          robots: response.headers.get('x-robots-tag'),
          error: body?.error ? String(body.error).slice(0, 120) : null,
        });
      } catch (error) {
        out.push({
          range,
          http: 0,
          elapsedMs: Math.round(performance.now() - started),
          error: String(error?.name || error || 'fetch-error'),
        });
      } finally {
        clearTimeout(timer);
      }
    }
    return out;
  }, ranges);

  for (const result of rangeResults) {
    const memberSet = new Set(result.aggregateMembers || []);
    const securityHeadersPass = String(result.cacheControl || '').includes('no-store')
      && result.corp === 'same-origin'
      && result.nosniff === 'nosniff'
      && String(result.robots || '').includes('noindex');
    const rangePass = result.http === 200
      && result.schema === 'wave-liquidation-daily-chart-v1'
      && Number.isInteger(result.rowCount)
      && result.rowCount === result.actualRows
      && result.rowCount > 0
      && result.elapsedMs <= 12_000
      && setEqual(memberSet, expectedMembers)
      && result.historyOnlyMembers?.length === 1
      && result.historyOnlyMembers[0] === 'hyperliquid-perp'
      && result.standaloneExcludedFromAll?.includes('lighter')
      && result.historyStatus?.realtime === 'ok'
      && result.historyStatus?.hyperliquid === 'ok'
      && securityHeadersPass;
    if (!rangePass) failed = true;
    evidence.ranges.push({
      range: result.range,
      http: result.http,
      elapsedMs: result.elapsedMs,
      rowCount: result.rowCount,
      start: result.start,
      end: result.end,
      aggregateMemberCount: result.aggregateMemberCount,
      historyAggregateComplete: result.historyAggregateComplete,
      historyStatus: result.historyStatus,
      projection: result.projection,
      securityHeadersPass,
      pass: rangePass,
      error: result.error || null,
    });
  }

  const dayResults = await page.evaluate(async dates => {
    const aliases = {
      'binance-usdm': 'binance', 'bybit-linear': 'bybit', 'okx-swap': 'okx', 'gate-futures': 'gate',
      'bitget-usdt-futures': 'bitget', 'aster-perp': 'aster', 'htx-usdt-swap': 'htx', 'coinex-futures': 'coinex',
      'pacifica-perp': 'pacifica', 'backpack-perp': 'backpack', 'bitfinex-derivatives': 'bitfinex',
      'deribit-futures': 'deribit', 'hyperliquid-perp': 'hyperliquid',
    };
    const validTriple = value => Array.isArray(value)
      && value.length >= 3
      && value.slice(0, 3).every(item => item != null && Number.isSafeInteger(Number(item)) && Number(item) >= 0)
      && Math.abs(Number(value[0]) + Number(value[1]) - Number(value[2])) <= 1;
    const out = [];
    for (const date of dates) {
      const started = performance.now();
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 12_000);
      try {
        const response = await fetch(`/api/liquidations/daily-history?date=${encodeURIComponent(date)}`, {
          method: 'GET',
          headers: { Accept: 'application/json', 'X-Wave-Client': 'liquidation-history-v1' },
          signal: controller.signal,
          cache: 'no-store',
        });
        const body = await response.json().catch(() => null);
        const descriptors = Array.isArray(body?.exchanges) ? body.exchanges : [];
        const vector = Array.isArray(body?.exchangeTriples) ? body.exchangeTriples : [];
        const normalized = new Map(descriptors.map((item, index) => [String(item?.id || item?.liveExchangeId || ''), vector[index]]));
        const memberStates = {};
        for (const member of Array.isArray(body?.aggregateMembers) ? body.aggregateMembers : []) {
          memberStates[String(member)] = validTriple(normalized.get(aliases[String(member)] || String(member)));
        }
        const totals = Array.isArray(body?.totals) ? body.totals.slice(0, 3) : [];
        const totalsState = totals.length === 3 && totals.every(value => value == null)
          ? 'null'
          : totals.length === 3 && totals.every(value => value != null && Number.isSafeInteger(Number(value)) && Number(value) >= 0)
            ? 'numeric'
            : 'mixed';
        out.push({
          date,
          http: response.status,
          elapsedMs: Math.round(performance.now() - started),
          schema: String(body?.schema || ''),
          aggregateComplete: body?.historyAggregateComplete === true,
          aggregateMembers: Array.isArray(body?.aggregateMembers) ? body.aggregateMembers.map(String) : [],
          historyOnlyMembers: Array.isArray(body?.historyOnlyMembers) ? body.historyOnlyMembers.map(String) : [],
          standaloneExcludedFromAll: Array.isArray(body?.standaloneExcludedFromAll) ? body.standaloneExcludedFromAll.map(String) : [],
          validRequiredMemberCount: Object.values(memberStates).filter(Boolean).length,
          missingRequiredMemberCount: Object.values(memberStates).filter(value => !value).length,
          totalsState,
          basis: String(body?.basis || ''),
          error: body?.error ? String(body.error).slice(0, 120) : null,
        });
      } catch (error) {
        out.push({
          date,
          http: 0,
          elapsedMs: Math.round(performance.now() - started),
          error: String(error?.name || error || 'fetch-error'),
        });
      } finally {
        clearTimeout(timer);
      }
    }
    return out;
  }, forwardDates);

  for (const result of dayResults) {
    const memberSet = new Set(result.aggregateMembers || []);
    const semanticPass = result.aggregateComplete
      ? result.totalsState === 'numeric' && result.missingRequiredMemberCount === 0 && result.validRequiredMemberCount === 13
      : result.totalsState === 'null' && result.missingRequiredMemberCount > 0;
    const dayPass = result.http === 200
      && result.schema === 'wave-liquidation-day-detail-v1'
      && setEqual(memberSet, expectedMembers)
      && result.historyOnlyMembers?.length === 1
      && result.historyOnlyMembers[0] === 'hyperliquid-perp'
      && result.standaloneExcludedFromAll?.includes('lighter')
      && result.elapsedMs <= 12_000
      && semanticPass;
    if (!dayPass) failed = true;
    evidence.days.push({
      date: result.date,
      http: result.http,
      elapsedMs: result.elapsedMs,
      aggregateComplete: result.aggregateComplete,
      validRequiredMemberCount: result.validRequiredMemberCount,
      missingRequiredMemberCount: result.missingRequiredMemberCount,
      totalsState: result.totalsState,
      basis: result.basis,
      pass: dayPass,
      error: result.error || null,
    });
  }
} catch (error) {
  failed = true;
  evidence.error = String(error?.message || error || '').slice(0, 300);
}

evidence.providerRequestCount = providerRequests.length;
if (providerRequests.length > 0) failed = true;
evidence.pass = !failed;
console.log(JSON.stringify(evidence, null, 2));
await context.close();
await browser.close();
process.exit(evidence.pass ? 0 : 1);
