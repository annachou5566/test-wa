import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const target = String(process.env.WA_PREVIEW_URL || '').trim().replace(/\/+$/, '');
if (!/^https:\/\/[a-z0-9-]+\.wave-alpha\.pages\.dev$/i.test(target)) throw new Error('invalid Preview origin');
const outDir = path.resolve('artifacts/liquidation-theme-polish-preview-qa-v2');
await fs.mkdir(outDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
const evidence = { schema:'wave-liquidation-theme-polish-preview-qa-v2', targetHost:new URL(target).hostname, generatedAt:new Date().toISOString(), rawRowsLogged:false, exchangeTriplesLogged:false, priceValuesLogged:false, accountSecretsLogged:false, desktop:null, mobile:null, pass:false };

async function activate(page) {
  await page.goto(`${target}/`, { waitUntil:'domcontentloaded', timeout:45_000 });
  await page.waitForFunction(() => typeof window.pluginSwitchTab === 'function' && typeof window.CryptoMarket?.onTabActivated === 'function', null, { timeout:30_000, polling:80 });
  await page.waitForFunction(() => Boolean(document.getElementById('crypto-market-view')), null, { timeout:30_000, polling:80 });
  await page.evaluate(() => window.pluginSwitchTab('crypto', true));
  await page.waitForFunction(() => {
    const view=document.getElementById('crypto-market-view');
    return Boolean(view && getComputedStyle(view).display !== 'none' && document.getElementById('cm-btn-liquidation'));
  }, null, { timeout:30_000, polling:80 });
  await page.waitForFunction(() => typeof window.WaveCryptoLiquidation?.activate === 'function' && window.WaveCryptoLiquidation?.mounted === true, null, { timeout:30_000, polling:80 });
  await page.locator('#cm-btn-liquidation').click({ timeout:10_000 });
  await page.waitForFunction(() => {
    const a=window.WaveLiquidationPageAudit?.snapshot?.();
    return a?.active===true && window.WaveLiquidationThemeUiSyncAudit && window.WaveLiquidationHistoryDefaultsAudit && window.WaveLiquidationGuideAudit?.snapshot?.()?.mounted===true && window.WaveLiquidationExchangeHeatmapAudit?.snapshot?.()?.mounted===true;
  }, null, { timeout:35_000, polling:100 });
  await page.waitForFunction(() => (window.WaveLiquidationExchangeHeatmapAudit?.snapshot?.()?.exchangeRows||0)>0, null, { timeout:25_000, polling:120 }).catch(()=>{});
}

async function inspect(name, viewport) {
  const context=await browser.newContext({viewport,deviceScaleFactor:1,isMobile:name==='mobile',hasTouch:name==='mobile'});
  await context.addInitScript(() => localStorage.setItem('wave_alpha_legal_accepted','qa-scope-fixture'));
  const page=await context.newPage(); const result={viewport,pass:false};
  try {
    await activate(page);
    result.initial=await page.evaluate(() => {
      const a=window.WaveLiquidationPageAudit?.snapshot?.()||{}, d=window.WaveLiquidationHistoryDefaultsAudit?.snapshot?.()||{}, s=window.WaveLiquidationThemeUiSyncAudit?.snapshot?.()||{};
      const guide=document.getElementById('cml-guide'), latest=document.getElementById('cml-guide-latest'), live=document.getElementById('cml-guide-snapshot'), body=guide?.querySelector('.cml-guide-card p');
      const fs=el=>el?parseFloat(getComputedStyle(el).fontSize||'0'):0;
      return { history:{tooltip:a.historyTooltipMode,style:a.historyChartStyle,layout:a.historyChartLayout}, defaults:d, sync:s, note:String(guide?.querySelector('.cml-guide-sub')?.textContent||'').trim(), fonts:{live:fs(live),latest:fs(latest),body:fs(body)}, latestWhiteSpace:latest?getComputedStyle(latest).whiteSpace:null, wavePicker:Boolean(window.WaveColorPicker?.open), qaConsolePresent:Boolean(document.getElementById('wa-liq-qa')) };
    });

    await page.locator('#wa-liq-theme-trigger').click({timeout:10_000});
    result.panel=await page.evaluate(() => {
      const p=document.getElementById('wa-liq-theme-panel'), r=p?.getBoundingClientRect(); if(!p||!r||p.hidden) return {visible:false};
      const x=Math.min(innerWidth-4,Math.max(4,r.left+Math.min(60,r.width/2))), y=Math.min(innerHeight-4,Math.max(4,r.top+Math.min(60,r.height/2))), hit=document.elementFromPoint(x,y);
      const toast=document.getElementById('wa-update-toast');
      return {visible:true,z:getComputedStyle(p).zIndex,hitInside:Boolean(hit&&p.contains(hit)),nativeVisible:[...p.querySelectorAll('input[type="color"]')].some(el=>getComputedStyle(el).display!=='none'),paletteButtons:p.querySelectorAll('[data-wa-liq-palette]').length,toastZ:toast?getComputedStyle(toast).zIndex:null};
    });

    await page.locator('[data-wa-liq-palette="long"]').click({timeout:10_000});
    result.wavePicker=await page.evaluate(() => ({shown:document.getElementById('wa-ucp')?.classList.contains('show')===true,overlay:getComputedStyle(document.getElementById('wa-ucp-overlay')).display!=='none'}));
    await page.evaluate(() => window.WaveColorPicker?.close?.());

    await page.locator('[data-wa-liq-preset="trader"]').click({timeout:10_000});
    await page.waitForFunction(() => window.WaveLiquidationTheme?.current?.preset==='trader',null,{timeout:5000}); await page.waitForTimeout(180);
    result.propagation=await page.evaluate(() => {
      const c=el=>el?getComputedStyle(el):null, green=v=>/14\s*,\s*203\s*,\s*129|rgb\(14,\s*203,\s*129\)|#0ECB81/i.test(String(v||'')), red=v=>/246\s*,\s*70\s*,\s*93|rgb\(246,\s*70,\s*93\)|#F6465D/i.test(String(v||''));
      const lc=document.querySelector('.cml-window-card[data-liq-dominant="long"]'), sc=document.querySelector('.cml-window-card[data-liq-dominant="short"]'), rank=document.querySelector('#cml-ranking-body tr'), ll=document.querySelector('.cml-live-row.long'), ls=document.querySelector('.cml-live-row.short'), xl=document.querySelector('.cmlx-long'), xs=document.querySelector('.cmlx-short'), bal=document.querySelector('.cmlx-balance-track'), hl=document.querySelector('#cml-history-chart [data-cml-bar="long"],#cml-history-chart [data-wc-liq-bar="long"],#cml-history-chart [data-wc-liq-stack="long"]'), hs=document.querySelector('#cml-history-chart [data-cml-bar="short"],#cml-history-chart [data-wc-liq-bar="short"],#cml-history-chart [data-wc-liq-stack="short"]'), css=document.getElementById('wa-liq-theme-ui-sync-css')?.textContent||'';
      return {rootLong:getComputedStyle(document.documentElement).getPropertyValue('--liq-long').trim(),rootShort:getComputedStyle(document.documentElement).getPropertyValue('--liq-short').trim(),longCard:lc?green(c(lc).backgroundImage+' '+c(lc).borderColor):null,shortCard:sc?red(c(sc).backgroundImage+' '+c(sc).borderColor):null,ranking:rank?green(c(rank).backgroundImage)&&red(c(rank).backgroundImage):null,liveLong:ll?green(getComputedStyle(ll,'::before').backgroundImage):null,liveShort:ls?red(getComputedStyle(ls,'::before').backgroundImage):null,exchangeLong:xl?green(c(xl).color):null,exchangeShort:xs?red(c(xs).color):null,exchangeBalance:bal?green(c(bal).backgroundImage)&&red(c(bal).backgroundImage):null,tokenContract:css.includes('.cmlpt-source-bar i:first-child{background:var(--liq-long)')&&css.includes('.cmlpt-source-bar i:last-child{background:var(--liq-short)'),historyLong:hl?green(c(hl).fill+' '+c(hl).stroke):null,historyShort:hs?red(c(hs).fill+' '+c(hs).stroke):null};
    });

    if(await page.locator('#wa-lt-fracture').isChecked()===false) await page.locator('#wa-lt-fracture').check();
    result.fracture=await page.evaluate(() => { const h=document.querySelector('.cmlpc-hero'),t=document.querySelector('.cmlxh .wa-hm-tile'); const hb=h?getComputedStyle(h,'::before').backgroundImage:'',tb=t?getComputedStyle(t,'::after').backgroundImage:''; return {hero:/data:image\/svg\+xml/i.test(hb),tile:t?/data:image\/svg\+xml/i.test(tb):null}; });
    await page.evaluate(() => window.WaveColorPicker?.close?.()); await page.locator('#wa-lt-close').click().catch(()=>{});
    await page.screenshot({path:path.join(outDir,`${name}-top.png`),fullPage:false}); await page.locator('#cml-guide').scrollIntoViewIfNeeded(); await page.screenshot({path:path.join(outDir,`${name}-guide.png`),fullPage:false});

    const observed=Object.values(result.propagation||{}).filter(v=>v===true||v===false);
    const panelZ=Number(result.panel?.z||0), toastZ=Number(result.panel?.toastZ||0);
    result.checks={syncLoaded:result.initial.sync?.installed===true,wavePickerOwner:result.initial.wavePicker===true&&result.initial.sync?.paletteOwner===true,qaConsoleAbsent:result.initial.qaConsolePresent===false,internalNoteRemoved:result.initial.note===''&&result.initial.sync?.guideInternalNoteVisible===false,guideReadable:result.initial.fonts.live>=13&&result.initial.fonts.latest>=13&&result.initial.fonts.body>=13,desktopLatestNoWrap:name!=='desktop'||result.initial.latestWhiteSpace==='nowrap',historyDefaults:result.initial.history.tooltip==='exchanges'&&result.initial.history.style==='bar'&&result.initial.history.layout==='stacked',panelAbove:result.panel?.visible===true&&result.panel?.hitInside===true&&(toastZ===0||panelZ>toastZ),nativePickerGone:result.panel?.nativeVisible===false&&result.panel?.paletteButtons===2,wavePickerOpens:result.wavePicker?.shown===true&&result.wavePicker?.overlay===true,traderTokens:result.propagation?.rootLong==='#0ECB81'&&result.propagation?.rootShort==='#F6465D',propagation:observed.length>=5&&observed.every(Boolean),fracture:result.fracture?.hero===true&&(result.fracture?.tile===true||result.fracture?.tile===null)};
    result.pass=Object.values(result.checks).every(Boolean);
  } catch(error){ result.error=String(error?.message||error||'').slice(0,500); }
  await context.close(); return result;
}

try{ evidence.desktop=await inspect('desktop',{width:1440,height:900}); evidence.mobile=await inspect('mobile',{width:390,height:844}); evidence.pass=evidence.desktop?.pass===true&&evidence.mobile?.pass===true; }catch(error){ evidence.error=String(error?.message||error||'').slice(0,500); }
await browser.close(); await fs.writeFile(path.join(outDir,'evidence.json'),JSON.stringify(evidence,null,2)); console.log(JSON.stringify(evidence,null,2)); process.exit(evidence.pass?0:1);
