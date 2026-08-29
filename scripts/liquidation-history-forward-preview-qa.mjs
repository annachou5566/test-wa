import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');

const QA = 'hyperliquid-full-test-v2';
const expectedMembers = new Set([
  'binance-usdm','bybit-linear','okx-swap','gate-futures','bitget-usdt-futures','aster-perp',
  'htx-usdt-swap','coinex-futures','pacifica-perp','backpack-perp','bitfinex-derivatives','deribit-futures',
  'hyperliquid-perp',
]);
const ranges = ['7d','30d','90d','all'];
const dates = ['2026-08-25','2026-08-26','2026-08-27','2026-08-28','2026-08-29'];
const sameSet = (a,b) => a.size === b.size && [...a].every(x => b.has(x));

const browser = await chromium.launch({ headless:true });
const context = await browser.newContext({ viewport:{ width:1024, height:768 } });
const page = await context.newPage();
const providerHosts = [];
page.on('request', req => {
  try {
    const h = new URL(req.url()).hostname;
    if (/hyperliquid\.xyz$/i.test(h) || /api\.hyperliquid/i.test(h)) providerHosts.push(h);
  } catch (_) {}
});

const shell = `${target}/__wave_alpha_qa_shell__`;
await page.route(shell, route => route.fulfill({ status:200, contentType:'text/html; charset=utf-8', body:'<!doctype html><title>QA</title>' }));
await page.goto(shell, { waitUntil:'load', timeout:45000 });
await page.unroute(shell);
if (new URL(page.url()).origin !== new URL(target).origin) throw new Error('origin mismatch');

const result = await page.evaluate(async ({ ranges, dates, QA }) => {
  const headers = { Accept:'application/json', 'X-Wave-Client':'liquidation-history-v1', 'X-Wave-Preview-QA':QA };
  async function get(path, timeout=12000) {
    const started = performance.now();
    const c = new AbortController();
    const timer = setTimeout(() => c.abort(), timeout);
    try {
      const r = await fetch(path, { headers, cache:'no-store', credentials:'same-origin', signal:c.signal });
      const b = await r.json().catch(() => null);
      return { http:r.status, elapsedMs:Math.round(performance.now()-started), body:b,
        mode:r.headers.get('x-wave-history-read-mode'), projection:r.headers.get('x-wave-history-projection') };
    } catch (e) {
      return { http:0, elapsedMs:Math.round(performance.now()-started), exception:String(e?.name||e).slice(0,80), body:null };
    } finally { clearTimeout(timer); }
  }

  const source = await get('/api/liquidations/hyperliquid-preview-history?mode=source-diagnostic&from=2026-08-25&to=2026-08-29');
  const rangeResults = [];
  for (const range of ranges) {
    const r = await get(`/api/liquidations/hyperliquid-preview-history?range=${encodeURIComponent(range)}&exchange=ALL`);
    const b = r.body;
    rangeResults.push({
      range, http:r.http, elapsedMs:r.elapsedMs, mode:r.mode, projection:r.projection,
      schema:String(b?.schema||''), rowCount:Number.isInteger(Number(b?.rowCount))?Number(b.rowCount):null,
      actualRows:Array.isArray(b?.rows)?b.rows.length:null,
      members:Array.isArray(b?.aggregateMembers)?b.aggregateMembers.map(String):[],
      historyOnly:Array.isArray(b?.historyOnlyMembers)?b.historyOnlyMembers.map(String):[],
      standalone:Array.isArray(b?.standaloneExcludedFromAll)?b.standaloneExcludedFromAll.map(String):[],
      aggregateComplete:typeof b?.historyAggregateComplete==='boolean'?b.historyAggregateComplete:null,
      realtimeStatus:String(b?.historySourceStatus?.realtime||''),
      hyperliquidStatus:String(b?.historySourceStatus?.hyperliquid||''),
      error:typeof b?.error==='string'?b.error.slice(0,100):null,
    });
  }

  const dayResults=[];
  const aliases={
    'binance-usdm':'binance','bybit-linear':'bybit','okx-swap':'okx','gate-futures':'gate','bitget-usdt-futures':'bitget',
    'aster-perp':'aster','htx-usdt-swap':'htx','coinex-futures':'coinex','pacifica-perp':'pacifica','backpack-perp':'backpack',
    'bitfinex-derivatives':'bitfinex','deribit-futures':'deribit','hyperliquid-perp':'hyperliquid'
  };
  const validTriple = v => Array.isArray(v) && v.length>=3 && v.slice(0,3).every(x=>x!=null&&Number.isSafeInteger(Number(x))&&Number(x)>=0)
    && Math.abs(Number(v[0])+Number(v[1])-Number(v[2]))<=1;
  for (const date of dates) {
    const r=await get(`/api/liquidations/hyperliquid-preview-history?date=${encodeURIComponent(date)}`);
    const b=r.body;
    const desc=Array.isArray(b?.exchanges)?b.exchanges:[];
    const vec=Array.isArray(b?.exchangeTriples)?b.exchangeTriples:[];
    const byId=new Map(desc.map((x,i)=>[String(x?.id||x?.liveExchangeId||''),vec[i]]));
    const members=Array.isArray(b?.aggregateMembers)?b.aggregateMembers.map(String):[];
    let valid=0;
    for (const id of members) if (validTriple(byId.get(aliases[id]||id))) valid++;
    const totals=Array.isArray(b?.totals)?b.totals.slice(0,3):[];
    const totalsState=totals.length===3&&totals.every(x=>x==null)?'null':totals.length===3&&totals.every(x=>x!=null&&Number.isSafeInteger(Number(x))&&Number(x)>=0)?'numeric':'mixed';
    dayResults.push({ date,http:r.http,elapsedMs:r.elapsedMs,mode:r.mode,schema:String(b?.schema||''),members,
      historyOnly:Array.isArray(b?.historyOnlyMembers)?b.historyOnlyMembers.map(String):[],standalone:Array.isArray(b?.standaloneExcludedFromAll)?b.standaloneExcludedFromAll.map(String):[],
      aggregateComplete:b?.historyAggregateComplete===true,validRequiredMemberCount:valid,missingRequiredMemberCount:Math.max(0,members.length-valid),totalsState,
      basis:String(b?.basis||''),error:typeof b?.error==='string'?b.error.slice(0,100):null });
  }
  return { source, rangeResults, dayResults };
}, { ranges, dates, QA });

const sourceBody=result.source?.body;
const sourceMembers=Array.isArray(sourceBody?.realtime?.members)?sourceBody.realtime.members:[];
const sourceSummary={
  http:result.source?.http||0, elapsedMs:result.source?.elapsedMs||0,
  realtimeStatus:String(sourceBody?.realtime?.status||''), realtimeReason:sourceBody?.realtime?.reason==null?null:String(sourceBody.realtime.reason),
  realtimeRange:String(sourceBody?.realtime?.range||''), realtimeMemberCount:sourceMembers.length,
  realtimeOkMembers:sourceMembers.filter(x=>x?.status==='ok').length,
  hyperliquidOk:sourceBody?.hyperliquid?.ok===true, hyperliquidError:String(sourceBody?.hyperliquid?.errorCode||''),
  hyperliquidRowCount:Number(sourceBody?.hyperliquid?.rowCount)||0, valuesExposed:sourceBody?.valuesExposed===true,
  mutationScope:String(sourceBody?.mutationScope||''),
};

let pass=sourceSummary.http===200 && sourceSummary.realtimeStatus==='ok' && sourceSummary.realtimeMemberCount===12
  && sourceSummary.realtimeOkMembers===12 && sourceSummary.hyperliquidOk && !sourceSummary.valuesExposed && sourceSummary.mutationScope==='none-read-only';

const rangesOut=result.rangeResults.map(x=>{
  const ok=x.http===200&&x.mode==='preview-qa-forced'&&x.schema==='wave-liquidation-daily-chart-v1'&&x.rowCount===x.actualRows&&x.rowCount>0
    && sameSet(new Set(x.members),expectedMembers)&&x.historyOnly.length===1&&x.historyOnly[0]==='hyperliquid-perp'&&x.standalone.includes('lighter')
    && x.realtimeStatus==='ok'&&x.hyperliquidStatus==='ok'&&x.elapsedMs<=12000;
  if(!ok) pass=false;
  return { range:x.range,http:x.http,elapsedMs:x.elapsedMs,rowCount:x.rowCount,memberCount:x.members.length,aggregateComplete:x.aggregateComplete,
    realtimeStatus:x.realtimeStatus,hyperliquidStatus:x.hyperliquidStatus,mode:x.mode,projection:x.projection,pass:ok,error:x.error };
});

const daysOut=result.dayResults.map(x=>{
  const semantic=x.aggregateComplete?(x.totalsState==='numeric'&&x.validRequiredMemberCount===13&&x.missingRequiredMemberCount===0):(x.totalsState==='null'&&x.missingRequiredMemberCount>0);
  const ok=x.http===200&&x.mode==='preview-qa-forced'&&x.schema==='wave-liquidation-day-detail-v1'&&sameSet(new Set(x.members),expectedMembers)
    &&x.historyOnly.length===1&&x.historyOnly[0]==='hyperliquid-perp'&&x.standalone.includes('lighter')&&x.elapsedMs<=12000&&semantic;
  if(!ok) pass=false;
  return { date:x.date,http:x.http,elapsedMs:x.elapsedMs,aggregateComplete:x.aggregateComplete,validRequiredMemberCount:x.validRequiredMemberCount,
    missingRequiredMemberCount:x.missingRequiredMemberCount,totalsState:x.totalsState,basis:x.basis,mode:x.mode,pass:ok,error:x.error };
});
if(providerHosts.length) pass=false;

console.log(JSON.stringify({ schema:'wave-liquidation-forced-preview-qualification-v1',targetHost:new URL(target).hostname,
  rawRowsLogged:false,moneyValuesLogged:false,source:sourceSummary,ranges:rangesOut,days:daysOut,providerRequestCount:providerHosts.length,pass },null,2));
await context.close();
await browser.close();
process.exit(pass?0:1);
