const REST_BASE = 'https://mainnet.zklighter.elliot.ai';
const WS_URL = 'wss://mainnet.zklighter.elliot.ai/stream?readonly=true';
const RUN_MS = 70_000;
const CONNECT_TIMEOUT_MS = 15_000;
const SHARD_COUNT = 4;
const SUBSCRIBE_DELAY_MS = 75;

function fail(message) {
  console.error(JSON.stringify({ probe: 'lighter-ws-trade-v3', error: message }));
  process.exit(2);
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const response = await fetch(`${REST_BASE}/api/v1/orderBooks`, {
  headers: { Accept: 'application/json', 'User-Agent': 'WaveAlpha-QA-Lighter-WS-Qualification/1.0' },
  signal: AbortSignal.timeout(15_000),
});
if (!response.ok) fail(`orderBooks HTTP ${response.status}`);
const metadata = await response.json();
const books = Array.isArray(metadata?.order_books) ? metadata.order_books : [];
const activePerps = books
  .filter(book => book?.market_type === 'perp' && book?.status === 'active' && Number.isInteger(book?.market_id))
  .map(book => ({ marketId: book.market_id, symbol: String(book.symbol || '') }))
  .sort((a, b) => a.marketId - b.marketId);
if (!activePerps.length) fail('no active perpetual markets from official metadata');

const expected = new Set(activePerps.map(item => item.marketId));
const acknowledged = new Set();
const updated = new Set();
const liquidationMarkets = new Set();
const unhandledTypes = new Map();
const providerErrors = new Map();
let liquidationCount = 0;
let ordinaryTradeCount = 0;
let deleverageCount = 0;
let settlementCount = 0;
let malformedTradeRows = 0;
let messages = 0;
let pings = 0;
let parseFailures = 0;

function marketIdFromChannel(channel) {
  const match = /^trade[:/](\d+)$/.exec(String(channel || ''));
  return match ? Number(match[1]) : null;
}

function inspectTrade(trade, liquidationArray) {
  if (!trade || typeof trade !== 'object') {
    malformedTradeRows++;
    return;
  }
  const type = String(trade.type || '').toLowerCase();
  const marketId = Number(trade.market_id);
  const size = Number(trade.size);
  const price = Number(trade.price);
  const usdAmount = Number(trade.usd_amount);
  const timestamp = Number(trade.timestamp);
  const txHash = String(trade.tx_hash || '');
  if (!Number.isInteger(marketId) || !expected.has(marketId)
      || !Number.isFinite(size) || size < 0
      || !Number.isFinite(price) || price < 0
      || !Number.isFinite(usdAmount) || usdAmount < 0
      || !Number.isFinite(timestamp) || timestamp < 0
      || !txHash) {
    malformedTradeRows++;
    return;
  }
  if (type === 'liquidation' || liquidationArray) {
    liquidationCount++;
    liquidationMarkets.add(marketId);
  } else if (type === 'deleverage') {
    deleverageCount++;
  } else if (type === 'market-settlement') {
    settlementCount++;
  } else if (type === 'trade') {
    ordinaryTradeCount++;
  }
}

function sanitizedProviderError(message) {
  const code = message?.code == null ? '' : String(message.code).slice(0, 80);
  const text = message?.message ?? message?.error ?? message?.msg ?? '';
  return `${code}|${String(text).slice(0, 160)}`;
}

const shards = Array.from({ length: SHARD_COUNT }, () => []);
activePerps.forEach((market, index) => shards[index % SHARD_COUNT].push(market));

function openShard(shardIndex, markets) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(WS_URL);
    let opened = false;
    let closeCode = null;
    let closeReason = '';
    const timer = setTimeout(() => {
      try { ws.close(); } catch {}
      reject(new Error(`shard ${shardIndex} connect timeout`));
    }, CONNECT_TIMEOUT_MS);

    ws.addEventListener('open', async () => {
      opened = true;
      clearTimeout(timer);
      for (const { marketId } of markets) {
        ws.send(JSON.stringify({ type: 'subscribe', channel: `trade/${marketId}` }));
        await sleep(SUBSCRIBE_DELAY_MS);
      }
    });

    ws.addEventListener('message', event => {
      messages++;
      let message;
      try { message = JSON.parse(String(event.data)); }
      catch { parseFailures++; return; }
      const type = String(message?.type || '');
      if (type === 'ping') {
        pings++;
        ws.send(JSON.stringify({ type: 'pong' }));
        return;
      }
      const marketId = marketIdFromChannel(message?.channel);
      if ((type === 'subscribed/trade' || type === 'subscribed') && Number.isInteger(marketId)) {
        acknowledged.add(marketId);
      }
      if (type === 'update/trade' && Number.isInteger(marketId)) {
        updated.add(marketId);
        for (const trade of Array.isArray(message.trades) ? message.trades : []) inspectTrade(trade, false);
        for (const trade of Array.isArray(message.liquidation_trades) ? message.liquidation_trades : []) inspectTrade(trade, true);
        return;
      }
      if (type !== 'connected' && !type.startsWith('subscribed/trade')) {
        unhandledTypes.set(type || '<missing>', (unhandledTypes.get(type || '<missing>') || 0) + 1);
        const errorKey = sanitizedProviderError(message);
        if (errorKey !== '|') providerErrors.set(errorKey, (providerErrors.get(errorKey) || 0) + 1);
      }
    });

    ws.addEventListener('close', event => {
      closeCode = event.code;
      closeReason = String(event.reason || '');
    });
    ws.addEventListener('error', () => {});

    setTimeout(() => {
      try { ws.close(1000, 'probe complete'); } catch {}
      setTimeout(() => resolve({ shardIndex, markets: markets.length, opened, closeCode, closeReason }), 500);
    }, RUN_MS);
  });
}

let shardResults;
try {
  shardResults = await Promise.all(shards.map((markets, index) => openShard(index, markets)));
} catch (error) {
  fail(String(error?.message || error));
}

const missingAcks = [...expected].filter(id => !acknowledged.has(id));
const summary = {
  probe: 'lighter-ws-trade-v3',
  rest_order_books_http: response.status,
  active_perp_markets: activePerps.length,
  market_id_min: Math.min(...activePerps.map(item => item.marketId)),
  market_id_max: Math.max(...activePerps.map(item => item.marketId)),
  websocket_url_mode: 'readonly',
  websocket_shards: SHARD_COUNT,
  subscribe_delay_ms: SUBSCRIBE_DELAY_MS,
  shard_results: shardResults,
  subscribed_trade_markets: acknowledged.size,
  missing_subscription_ack_count: missingAcks.length,
  missing_subscription_ack_market_ids: missingAcks,
  markets_with_trade_updates: updated.size,
  liquidation_trade_count: liquidationCount,
  markets_with_liquidations: liquidationMarkets.size,
  ordinary_trade_count: ordinaryTradeCount,
  deleverage_count: deleverageCount,
  market_settlement_count: settlementCount,
  malformed_trade_rows: malformedTradeRows,
  messages_seen: messages,
  ping_frames_seen: pings,
  parse_failures: parseFailures,
  unhandled_type_counts: Object.fromEntries([...unhandledTypes.entries()].sort()),
  provider_error_counts: Object.fromEntries([...providerErrors.entries()].sort()),
  raw_trades_persisted: false,
  credentials_used: false,
};
console.log(JSON.stringify(summary, null, 2));

if (shardResults.some(result => !result.opened)) process.exit(2);
if (acknowledged.size !== activePerps.length) process.exit(3);
if (malformedTradeRows !== 0 || parseFailures !== 0) process.exit(4);
