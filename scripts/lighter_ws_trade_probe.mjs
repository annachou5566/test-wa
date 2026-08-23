const REST_BASE = 'https://mainnet.zklighter.elliot.ai';
const WS_URL = 'wss://mainnet.zklighter.elliot.ai/stream?readonly=true';
const SHARD_COUNT = 4;
const SUBSCRIBE_DELAY_MS = 250;
const RETRY_DELAY_MS = 12_000;
const MAX_SUBSCRIBE_ROUNDS = 3;
const OBSERVE_MS = 105_000;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const metaResponse = await fetch(`${REST_BASE}/api/v1/orderBooks`, {
  headers: { Accept: 'application/json', 'User-Agent': 'WaveAlpha-QA-Lighter-WS-Qualification/1.0' },
  signal: AbortSignal.timeout(15_000),
});
if (!metaResponse.ok) throw new Error(`orderBooks HTTP ${metaResponse.status}`);
const metadata = await metaResponse.json();
const activePerps = (Array.isArray(metadata?.order_books) ? metadata.order_books : [])
  .filter(book => book?.market_type === 'perp' && book?.status === 'active' && Number.isInteger(book?.market_id))
  .map(book => ({ marketId: book.market_id, symbol: String(book.symbol || '') }))
  .sort((a,b)=>a.marketId-b.marketId);
if (!activePerps.length) throw new Error('no active perps');

const expected = new Set(activePerps.map(m => m.marketId));
const acknowledged = new Set();
const updated = new Set();
const sockets = new Map();
const marketShard = new Map();
let malformed = 0;
let parseFailures = 0;
let ordinaryTrades = 0;
let liquidationTrades = 0;
const liquidationTradeIds = new Set();
let liquidationDuplicateTradeIds = 0;
const liquidationPatterns = new Map();

function marketIdFromChannel(channel) {
  const match = /^trade[:/](\d+)$/.exec(String(channel || ''));
  return match ? Number(match[1]) : null;
}
function sign(value) {
  const x = Number(value);
  return !Number.isFinite(x) ? 'invalid' : x > 0 ? 'positive' : x < 0 ? 'negative' : 'zero';
}
function inspectTrade(trade, explicitLiquidation) {
  if (!trade || typeof trade !== 'object') { malformed++; return; }
  const marketId = Number(trade.market_id);
  const tradeId = Number(trade.trade_id);
  const txHash = String(trade.tx_hash || '');
  const timestamp = Number(trade.timestamp);
  const size = Number(trade.size);
  const price = Number(trade.price);
  const usd = Number(trade.usd_amount);
  if (!Number.isInteger(marketId) || !expected.has(marketId) || !Number.isInteger(tradeId)
      || !txHash || !Number.isFinite(timestamp) || !Number.isFinite(size) || size < 0
      || !Number.isFinite(price) || price < 0 || !Number.isFinite(usd) || usd < 0) {
    malformed++; return;
  }
  const liquidation = explicitLiquidation || String(trade.type || '').toLowerCase() === 'liquidation';
  if (!liquidation) { ordinaryTrades++; return; }
  liquidationTrades++;
  if (liquidationTradeIds.has(tradeId)) liquidationDuplicateTradeIds++;
  liquidationTradeIds.add(tradeId);
  const makerAsk = trade.is_maker_ask === true ? 'true' : trade.is_maker_ask === false ? 'false' : 'other';
  const pattern = `makerAsk:${makerAsk}|makerBefore:${sign(trade.maker_position_size_before)}|takerBefore:${sign(trade.taker_position_size_before)}|takerSignChanged:${String(trade.taker_position_sign_changed)}`;
  liquidationPatterns.set(pattern, (liquidationPatterns.get(pattern) || 0) + 1);
}

const shards = Array.from({length: SHARD_COUNT}, ()=>[]);
activePerps.forEach((market,index)=>{
  const shard = index % SHARD_COUNT;
  shards[shard].push(market);
  marketShard.set(market.marketId, shard);
});

function openShard(index) {
  return new Promise((resolve,reject)=>{
    const ws = new WebSocket(WS_URL);
    const timer = setTimeout(()=>reject(new Error(`shard ${index} connect timeout`)),15_000);
    ws.addEventListener('open',()=>{ clearTimeout(timer); sockets.set(index,ws); resolve(); });
    ws.addEventListener('message',event=>{
      let message;
      try { message = JSON.parse(String(event.data)); } catch { parseFailures++; return; }
      if (message?.type === 'ping') { ws.send(JSON.stringify({type:'pong'})); return; }
      const marketId = marketIdFromChannel(message?.channel);
      if ((message?.type === 'subscribed/trade' || message?.type === 'subscribed') && expected.has(marketId)) acknowledged.add(marketId);
      if (message?.type === 'update/trade' && expected.has(marketId)) {
        updated.add(marketId);
        for (const trade of Array.isArray(message.trades) ? message.trades : []) inspectTrade(trade,false);
        for (const trade of Array.isArray(message.liquidation_trades) ? message.liquidation_trades : []) inspectTrade(trade,true);
      }
    });
    ws.addEventListener('error',()=>{});
  });
}

await Promise.all(Array.from({length:SHARD_COUNT},(_,i)=>openShard(i)));

const roundResults = [];
for (let round = 1; round <= MAX_SUBSCRIBE_ROUNDS; round++) {
  const pending = activePerps.filter(m=>!acknowledged.has(m.marketId));
  if (!pending.length) break;
  for (const market of pending) {
    const ws = sockets.get(marketShard.get(market.marketId));
    if (!ws || ws.readyState !== WebSocket.OPEN) throw new Error('subscription shard not open');
    ws.send(JSON.stringify({type:'subscribe',channel:`trade/${market.marketId}`}));
    await sleep(SUBSCRIBE_DELAY_MS);
  }
  await sleep(RETRY_DELAY_MS);
  roundResults.push({round, attempted:pending.length, acknowledged_total:acknowledged.size});
}

const remaining = activePerps.filter(m=>!acknowledged.has(m.marketId));
const remainingObservation = Math.max(0, OBSERVE_MS - (Date.now() - metaResponse.headers.get('date')));
// Use a fixed bounded tail rather than trusting response Date arithmetic across environments.
await sleep(35_000);
for (const ws of sockets.values()) { try { ws.close(1000,'probe complete'); } catch {} }
await sleep(500);

console.log(JSON.stringify({
  probe:'lighter-ws-retry-coverage-v6',
  order_books_http:metaResponse.status,
  active_perp_markets:activePerps.length,
  websocket_shards:SHARD_COUNT,
  subscribe_delay_ms:SUBSCRIBE_DELAY_MS,
  retry_delay_ms:RETRY_DELAY_MS,
  subscribe_rounds:roundResults,
  subscribed_trade_markets:acknowledged.size,
  missing_subscription_ack_count:remaining.length,
  missing_subscription_ack_market_ids:remaining.map(m=>m.marketId),
  markets_with_trade_updates:updated.size,
  ordinary_trade_count:ordinaryTrades,
  liquidation_trade_count:liquidationTrades,
  liquidation_unique_trade_ids:liquidationTradeIds.size,
  liquidation_duplicate_trade_ids:liquidationDuplicateTradeIds,
  liquidation_pattern_counts:Object.fromEntries([...liquidationPatterns.entries()].sort()),
  malformed_trade_rows:malformed,
  parse_failures:parseFailures,
  raw_trades_persisted:false,
  credentials_used:false,
},null,2));

if (remaining.length) process.exit(3);
if (malformed || parseFailures) process.exit(4);
