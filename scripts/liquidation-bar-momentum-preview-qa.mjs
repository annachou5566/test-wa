import { chromium } from 'playwright';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
await context.addInitScript(() => {
  localStorage.setItem('wa-liquidation-chart-style', 'bar');
  localStorage.setItem('wa-liquidation-chart-layout', 'diverging');
  localStorage.setItem('wa-liquidation-chart-view', 'bar');
});
const page = await context.newPage();
const evidence = { schema:'wave-liquidation-bar-momentum-preview-qa-v1', targetHost:new URL(target).hostname, rawRowsLogged:false, priceValuesLogged:false, pass:false };
try {
  await page.goto(`${target}/?liquidationTest=1`, { waitUntil:'domcontentloaded', timeout:45000 });
  await page.waitForFunction(() => typeof window.CryptoMarket?.onTabActivated === 'function' && typeof window.WaveCryptoLiquidation?.activate === 'function', null, { timeout:25000, polling:80 });
  await page.evaluate(() => {
    const v=document.getElementById('crypto-market-view'); if(!v) throw new Error('crypto view missing');
    v.style.display='block'; window.CryptoMarket.onTabActivated(); window.WaveCryptoLiquidation.activate();
  });
  await page.waitForFunction(() => { const a=window.WaveCryptoLiquidation?.audit?.(); return a?.active===true && a?.historyLoading===false && a?.historyChartStyle==='bar' && a?.historyChartLayout==='diverging' && Number(a?.historyRowCount||0)>0; }, null, { timeout:25000, polling:100 });
  const result=await page.evaluate(() => {
    const audit=window.WaveCryptoLiquidation?.audit?.()||{};
    const svg=document.querySelector('#cml-history-chart svg.wc-svg');
    const rects=[...(svg?.querySelectorAll('rect[data-wc-liq-bar][data-column]')||[])].map(r=>({side:r.getAttribute('data-wc-liq-bar'),column:r.getAttribute('data-column'),x:Number(r.getAttribute('x')),width:Number(r.getAttribute('width'))}));
    const legacyPaths=[...(svg?.querySelectorAll('path[data-cml-bar]')||[])].length;
    const groups=new Map();
    for(const r of rects){ if(!groups.has(r.column)) groups.set(r.column,{}); groups.get(r.column)[r.side]=r; }
    let pairCount=0,misaligned=0;
    for(const pair of groups.values()) if(pair.short&&pair.long){ pairCount++; if(Math.abs(pair.short.x-pair.long.x)>0.01||Math.abs(pair.short.width-pair.long.width)>0.01) misaligned++; }
    return { momentumVersion:audit.momentumVersion||null, style:audit.historyChartStyle||null, layout:audit.historyChartLayout||null, rectCount:rects.length, pairCount, misaligned, legacyPaths, mergedBarPairs:Number(audit.mergedBarPairs||0), barsShareOneColumn:audit.barsShareOneColumn===true };
  });
  Object.assign(evidence,result);
  evidence.momentumPass=result.momentumVersion==='wave-liq-momentum-v3-closed-observed-minutes';
  evidence.barDomPass=result.style==='bar'&&result.layout==='diverging'&&result.rectCount>0&&result.pairCount>0&&result.misaligned===0;
  evidence.pass=evidence.momentumPass&&evidence.barDomPass;
} catch(error){ evidence.error=String(error?.message||error||'').slice(0,360); }
await context.close(); await browser.close();
console.log(JSON.stringify(evidence,null,2));
process.exit(evidence.pass?0:1);
