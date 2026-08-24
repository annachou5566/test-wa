import { chromium } from 'playwright';

const PAGE_URL = 'https://wave-alpha.pages.dev/';
const API_URL = 'https://mainnet.zklighter.elliot.ai/api/v1/orderBooks';
const WS_URL = 'wss://mainnet.zklighter.elliot.ai/stream?readonly=true';
const SHARDS = 4;
const TIMEOUT_MS = 45_000;

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.addInitScript(() => {
    globalThis.__waProbeNativeFetch = globalThis.fetch.bind(globalThis);
    globalThis.__waProbeNativeWebSocket = globalThis.WebSocket;
  });
  await page.goto(PAGE_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  const result = await page.evaluate(async ({ API_URL, WS_URL, SHARDS, TIMEOUT_MS }) => {
    const nativeFetch = globalThis.__waProbeNativeFetch;
    const NativeWebSocket = globalThis.__waProbeNativeWebSocket;
    if (typeof nativeFetch !== 'function' || typeof NativeWebSocket !== 'function') {
      throw new Error('native browser primitives unavailable');
    }

    const response = await nativeFetch(API_URL, { cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`metadata HTTP ${response.status}`);
    const payload = await response.json();
    const markets = (Array.isArray(payload?.order_books) ? payload.order_books : [])
      .filter(row => String(row?.market_type || '').toLowerCase() === 'perp'
        && String(row?.status || '').toLowerCase() === 'active'
        && Number.isInteger(Number(row?.market_id)))
      .map(row => Number(row.market_id))
      .sort((a, b) => a - b);
    const unique = [...new Set(markets)];
    if (!unique.length) throw new Error('no active perpetual markets');

    const shards = Array.from({ length: SHARDS }, () => []);
    unique.forEach((marketId, index) => shards[index % SHARDS].push(marketId));
    const confirmed = new Set();
    const sockets = [];
    let updateTradeMessages = 0;

    try {
      await Promise.race([
        Promise.all(shards.map((marketIds, shardIndex) => new Promise((resolve, reject) => {
          const expected = new Set(marketIds);
          const shardConfirmed = new Set();
          const socket = new NativeWebSocket(WS_URL);
          sockets.push(socket);
          socket.onerror = () => reject(new Error(`websocket error shard ${shardIndex + 1}`));
          socket.onclose = event => {
            if (shardConfirmed.size !== expected.size) reject(new Error(`websocket closed ${event.code} shard ${shardIndex + 1}`));
          };
          socket.onopen = async () => {
            for (const marketId of marketIds) {
              socket.send(JSON.stringify({ type: 'subscribe', channel: `trade/${marketId}` }));
              await new Promise(done => setTimeout(done, 30));
            }
          };
          socket.onmessage = event => {
            let message;
            try { message = JSON.parse(event.data); } catch (_) { return; }
            const type = String(message?.type || '').toLowerCase();
            if (type === 'update/trade') updateTradeMessages++;
            if (type !== 'subscribed/trade') return;
            const channel = String(message?.channel || '').toLowerCase();
            if (!channel.startsWith('trade:')) return;
            const marketId = Number(channel.slice(6));
            if (!expected.has(marketId)) return;
            shardConfirmed.add(marketId);
            confirmed.add(marketId);
            if (shardConfirmed.size === expected.size) resolve();
          };
        }))),
        new Promise((_, reject) => setTimeout(() => reject(new Error('browser subscription timeout')), TIMEOUT_MS)),
      ]);
    } finally {
      for (const socket of sockets) {
        try { socket.close(1000, 'probe complete'); } catch (_) {}
      }
    }

    return {
      pageOrigin: location.origin,
      metadataHttp: response.status,
      activePerpMarkets: unique.length,
      sockets: sockets.length,
      confirmedMarkets: confirmed.size,
      updateTradeMessages,
      credentialsUsed: false,
      rawRowsPersisted: false,
    };
  }, { API_URL, WS_URL, SHARDS, TIMEOUT_MS });

  console.log(JSON.stringify(result, null, 2));
  if (result.metadataHttp !== 200 || result.activePerpMarkets <= 0
    || result.sockets !== SHARDS || result.confirmedMarkets !== result.activePerpMarkets) {
    throw new Error('LIGHTER_BROWSER_ORIGIN_PROBE=FAIL');
  }
  console.log('LIGHTER_BROWSER_ORIGIN_PROBE=PASS');
} finally {
  await browser.close();
}
