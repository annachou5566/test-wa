const REST_BASE = 'https://mainnet.zklighter.elliot.ai';
const WS_URL = 'wss://mainnet.zklighter.elliot.ai/stream?readonly=true';
const SHARD_COUNT = 4;
const SUBSCRIBE_DELAY_MS = 250;
const OBSERVE_MS = 240_000;
const CONNECT_TIMEOUT_MS = 15_000;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const sign = value => {
  const x = Number(value);
  return !Number.isFinite(x) ? 'invalid' : x > 0 ? 'positive' : x < 0 ? 'negative' : 'zero';
};

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
let ordinaryTrades = 0;
let liquidationTrades = 0;
let malformed = 0;
let parseFailures = 0;
let messages = 0;
const liquidationPatterns = new Map();
const liquidationMarketIds = new Set();
const liquidationTradeIds = new Set();
let liquidationDuplicateTradeIds = 0;

function marketIdFromChannel(channel) {
  const match = /^trade[:/](\d+)$/.exec(String(channel || ''));
  return match ? Number(match[1]) : null;
}

function inspectTrade(trade, isLiquidationArray) {
  if (!trade || typeof trade !== 'object') { malformed++; return; }
  const marketId = Number(trade.market_id);
  const type = String(trade.type || '').toLowerCase();
  const tradeId = Number(trade.trade_id);
  const txHash = String(trade.tx_hash || '');
  const timestamp = Number(trade.timestamp);
  const size = Number(trade.size);
  const price = Number(trade.price);
  const usd = Number(trade.usd_amount);
  if (!Number.isInteger(marketId) || !expected.has(marketId)
      || !Number.isInteger(tradeId) || !txHash || !Number.isFinite(timestamp)
      || !Number.isFinite(size) || size < 0 || !Number.isFinite(price) || price < 0
      || !Number.isFinite(usd) || usd < 0) { malformed++; return; }

  const liquidation = isLiquidationArray || type === 'liquidation';
  if (!liquidation) { ordinaryTrades++; return; }
  liquidationTrades++;
  liquidationMarketIds.add(marketId);
  if (liquidationTradeIds.has(tradeId)) liquidationDuplicateTradeIds++;
  liquidationTradeIds.add(tradeId);
  const makerAsk = trade.is_maker_ask === true ? 'true' : trade.is_maker_ask === false ? 'false' : 'other';
  const pattern = [
    `makerAsk:${makerAsk}`,
    `makerBefore:${sign(trade.maker_position_size_before)}`,
    `takerBefore:${sign(trade.taker_position_size_before)}`,
    `askPnl:${sign(trade.ask_account_pnl)}`,
    `bidPnl:${sign(trade.bid_account_pnl)}`,
    `makerSignChanged:${String(trade.maker_position_sign_changed)}`,
    `takerSignChanged:${String(trade.taker_position_sign_changed)}`,
  ].join('|');
  liquidationPatterns.set(pattern, (liquidationPatterns.get(pattern) || 0) + 1);
}

const shards = Array.from({length: SHARD_COUNT}, ()=>[]);
activePerps.forEach((market,index)=>shards[index % SHARD_COUNT].push(market));

function runShard(index, markets) {
  return new Promise((resolve,reject)=>{
    const ws = new WebSocket(WS_URL);
    let opened = false;
    let closeCode = null;
    const timer = setTimeout(()=>reject(new Error(`shard ${index} connect timeout`)), CONNECT_TIMEOUT_MS);
    ws.addEventListener('open', async ()=>{
      opened = true;
      clearTimeout(timer);
      for (const market of markets) {
        ws.send(JSON.stringify({type:'subscribe',channel:`trade/${market.marketId}`}));
        await sleep(SUBSCRIBE_DELAY_MS);
      }
    });
    ws.addEventListener('message', event=>{
      messages++;
      let message;
      try { message = JSON.parse(String(event.data)); } catch { parseFailures++; return; }
      if (message?.type === 'ping') { ws.send(JSON.stringify({type:'pong'})); return; }
      const marketId = marketIdFromChannel(message?.channel);
      if ((message?.type === 'subscribed/trade' || message?.type === 'subscribed') && expected.has(marketId)) acknowledged.add(marketId);
      if (message?.type === 'update/trade' && expected.has(marketId)) {
        updated.add(marketId);
        for (const trade of Array.isArray(message.trades) ? message.trades : []) inspectTrade(trade, false);
        for (const trade of Array.isArray(message.liquidation_trades) ? message.liquidation_trades : []) inspectTrade(trade, true);
      }
    });
    ws.addEventListener('error', ()=>{});
    ws.addEventListener('close', event=>{ closeCode = event.code; });
    setTimeout(()=>{
      try { ws.close(1000,'probe complete'); } catch {}
      setTimeout(()=>resolve({index, markets:markets.length, opened, closeCode}),500);
    }, OBSERVE_MS);
  });
}

const shardResults = await Promise.all(shards.map((markets,index)=>runShard(index,markets)));
const missing = activePerps.filter(m=>!acknowledged.has(m.marketId));
console.log(JSON.stringify({
  probe:'lighter-ws-all-perps-v5',
  order_books_http:metaResponse.status,
  active_perp_markets:activePerps.length,
  websocket_shards:SHARD_COUNT,
  subscribe_delay_ms:SUBSCRIBE_DELAY_MS,
  observation_ms:OBSERVE_MS,
  shard_results:shardResults,
  subscribed_trade_markets:acknowledged.size,
  missing_subscription_ack_count:missing.length,
  missing_subscription_ack_market_ids:missing.map(m=>m.marketId),
  markets_with_trade_updates:updated.size,
  ordinary_trade_count:ordinaryTrades,
  liquidation_trade_count:liquidationTrades,
  liquidation_market_count:liquidationMarketIds.size,
  liquidation_duplicate_trade_ids:liquidationDuplicateTradeIds,
  liquidation_pattern_counts:Object.fromEntries([...liquidationPatterns.entries()].sort()),
  malformed_trade_rows:malformed,
  parse_failures:parseFailures,
  messages_seen:messages,
  raw_trades_persisted:false,
  credentials_used:false,
},null,2));

if (shardResults.some(result=>!result.opened)) process.exit(2);
if (missing.length) process.exit(3);
if (malformed || parseFailures) process.exit(4);
