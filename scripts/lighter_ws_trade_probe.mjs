const REST_BASE = 'https://mainnet.zklighter.elliot.ai';
const WS_URL = 'wss://mainnet.zklighter.elliot.ai/stream?readonly=true';
const RUN_MS = 60_000;
const CONNECT_TIMEOUT_MS = 15_000;

function fail(message) {
  console.error(JSON.stringify({ probe: 'lighter-ws-trade-v1', error: message }));
  process.exit(2);
}

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
let liquidationCount = 0;
let ordinaryTradeCount = 0;
let deleverageCount = 0;
let settlementCount = 0;
let malformedTradeRows = 0;
let messages = 0;
let pings = 0;
let unhandled = 0;
let connected = false;
let closed = false;
let closeCode = null;
let closeReason = '';

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

const ws = new WebSocket(WS_URL);
const connectTimer = setTimeout(() => {
  try { ws.close(); } catch {}
  fail('websocket connect timeout');
}, CONNECT_TIMEOUT_MS);

ws.addEventListener('open', () => {
  connected = true;
  clearTimeout(connectTimer);
  for (const { marketId } of activePerps) {
    ws.send(JSON.stringify({ type: 'subscribe', channel: `trade/${marketId}` }));
  }
});

ws.addEventListener('message', event => {
  messages++;
  let message;
  try { message = JSON.parse(String(event.data)); }
  catch { unhandled++; return; }
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
  if (type !== 'connected' && !type.startsWith('subscribed/trade')) unhandled++;
});

ws.addEventListener('close', event => {
  closed = true;
  closeCode = event.code;
  closeReason = String(event.reason || '');
});

ws.addEventListener('error', () => {});

await new Promise(resolve => setTimeout(resolve, RUN_MS));
try { ws.close(1000, 'probe complete'); } catch {}
await new Promise(resolve => setTimeout(resolve, 750));

const missingAcks = [...expected].filter(id => !acknowledged.has(id));
const summary = {
  probe: 'lighter-ws-trade-v1',
  rest_order_books_http: response.status,
  active_perp_markets: activePerps.length,
  active_perp_market_ids: activePerps.map(item => item.marketId),
  websocket_url_mode: 'readonly',
  websocket_connected: connected,
  subscribed_trade_markets: acknowledged.size,
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
  unhandled_messages: unhandled,
  websocket_closed: closed,
  websocket_close_code: closeCode,
  websocket_close_reason: closeReason,
  raw_trades_persisted: false,
  credentials_used: false,
};
console.log(JSON.stringify(summary, null, 2));

if (!connected) process.exit(2);
if (acknowledged.size !== activePerps.length) process.exit(3);
if (malformedTradeRows !== 0) process.exit(4);
