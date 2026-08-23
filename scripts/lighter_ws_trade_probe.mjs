const REST_BASE = 'https://mainnet.zklighter.elliot.ai';
const WS_URL = 'wss://mainnet.zklighter.elliot.ai/stream?readonly=true';
const EDGE_IDS = new Set([219,220,221,222,223,224,225,226,227,228]);
const WAIT_MS = 12_000;

function safeError(value) {
  if (value == null) return null;
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value).slice(0, 240);
  if (Array.isArray(value)) return value.slice(0, 8).map(safeError);
  if (typeof value === 'object') {
    const out = {};
    for (const [key, child] of Object.entries(value).slice(0, 12)) {
      out[String(key).slice(0, 80)] = safeError(child);
    }
    return out;
  }
  return String(value).slice(0, 240);
}

async function getJson(path) {
  const response = await fetch(REST_BASE + path, {
    headers: { Accept: 'application/json', 'User-Agent': 'WaveAlpha-QA-Lighter-Edge-Qualification/1.0' },
    signal: AbortSignal.timeout(15_000),
  });
  let payload = null;
  try { payload = await response.json(); } catch {}
  return { http: response.status, payload };
}

const metadataRead = await getJson('/api/v1/orderBooks');
if (metadataRead.http !== 200) throw new Error(`orderBooks HTTP ${metadataRead.http}`);
const books = Array.isArray(metadataRead.payload?.order_books) ? metadataRead.payload.order_books : [];
const activePerps = books.filter(book => book?.market_type === 'perp' && book?.status === 'active');
const edgeMarkets = activePerps
  .filter(book => EDGE_IDS.has(Number(book.market_id)))
  .map(book => ({ market_id: Number(book.market_id), symbol: String(book.symbol || ''), created_at: String(book.created_at || '') }))
  .sort((a, b) => a.market_id - b.market_id);

const recentReads = [];
for (const market of edgeMarkets) {
  const read = await getJson(`/api/v1/recentTrades?market_id=${market.market_id}&limit=5`);
  const trades = Array.isArray(read.payload?.trades) ? read.payload.trades : null;
  recentReads.push({
    market_id: market.market_id,
    symbol: market.symbol,
    http: read.http,
    rows: trades == null ? null : trades.length,
    types: trades == null ? [] : [...new Set(trades.map(trade => String(trade?.type || '')))].sort(),
    newest_timestamp: trades?.reduce((max, trade) => Math.max(max, Number(trade?.timestamp) || 0), 0) || null,
  });
}

const ack = new Set();
const updates = new Set();
const errors = [];
let connected = false;
let parseFailures = 0;
let ordinaryTrades = 0;
let liquidationTrades = 0;

await new Promise((resolve, reject) => {
  const ws = new WebSocket(WS_URL);
  const connectTimer = setTimeout(() => reject(new Error('WS connect timeout')), 15_000);
  ws.addEventListener('open', async () => {
    connected = true;
    clearTimeout(connectTimer);
    for (const market of edgeMarkets) {
      ws.send(JSON.stringify({ type: 'subscribe', channel: `trade/${market.market_id}` }));
      await new Promise(r => setTimeout(r, 250));
    }
  });
  ws.addEventListener('message', event => {
    let message;
    try { message = JSON.parse(String(event.data)); }
    catch { parseFailures++; return; }
    if (message?.type === 'ping') {
      ws.send(JSON.stringify({ type: 'pong' }));
      return;
    }
    const match = /^trade[:/](\d+)$/.exec(String(message?.channel || ''));
    const marketId = match ? Number(match[1]) : null;
    if ((message?.type === 'subscribed/trade' || message?.type === 'subscribed') && EDGE_IDS.has(marketId)) ack.add(marketId);
    if (message?.type === 'update/trade' && EDGE_IDS.has(marketId)) {
      updates.add(marketId);
      ordinaryTrades += Array.isArray(message.trades) ? message.trades.length : 0;
      liquidationTrades += Array.isArray(message.liquidation_trades) ? message.liquidation_trades.length : 0;
      return;
    }
    if (message?.type !== 'connected') {
      errors.push({
        type: message?.type == null ? null : String(message.type).slice(0, 120),
        channel: message?.channel == null ? null : String(message.channel).slice(0, 120),
        code: safeError(message?.code),
        error: safeError(message?.error),
        message: safeError(message?.message),
      });
    }
  });
  ws.addEventListener('error', () => {});
  setTimeout(() => {
    try { ws.close(1000, 'probe complete'); } catch {}
    setTimeout(resolve, 500);
  }, WAIT_MS);
});

console.log(JSON.stringify({
  probe: 'lighter-edge-markets-v4',
  order_books_http: metadataRead.http,
  active_perp_count: activePerps.length,
  edge_markets: edgeMarkets,
  recent_trade_reads: recentReads,
  websocket_connected: connected,
  websocket_acked_market_ids: [...ack].sort((a,b)=>a-b),
  websocket_update_market_ids: [...updates].sort((a,b)=>a-b),
  websocket_ordinary_trade_rows: ordinaryTrades,
  websocket_liquidation_trade_rows: liquidationTrades,
  websocket_errors: errors.slice(0, 20),
  parse_failures: parseFailures,
  raw_trades_persisted: false,
  credentials_used: false,
}, null, 2));

if (!connected || parseFailures) process.exit(2);
