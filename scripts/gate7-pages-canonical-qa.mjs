import { createHash } from 'node:crypto';

const pageUrl = 'https://wave-alpha.pages.dev/?waveGate7Diag=1';
const assetUrl = 'https://wave-alpha.pages.dev/js/alpha-live-client.js?v=5';
const expectedAssetSha = 'ad60e58ce59f3b5323040ecdfe1b9425896b822624545d37f0ae5e5b2724e245';

const sleep = ms => new Promise(r => setTimeout(r, ms));

let passed = false;

for (let attempt = 1; attempt <= 6; attempt += 1) {
  const pageRes = await fetch(pageUrl, { headers: { 'cache-control': 'no-cache' } });
  const html = await pageRes.text();

  const canonicalPath = html.includes('/js/alpha-live-client.js?v=5');
  const wrongPath = html.includes('/public/js/alpha-live-client.js?v=5');
  console.log(`TRY=${attempt} PAGE_HTTP=${pageRes.status} CANONICAL_PATH=${canonicalPath} WRONG_PATH=${wrongPath}`);

  if (pageRes.ok && canonicalPath && !wrongPath) {
    const assetRes = await fetch(assetUrl, { headers: { 'cache-control': 'no-cache' } });
    const bytes = Buffer.from(await assetRes.arrayBuffer());
    const sha = createHash('sha256').update(bytes).digest('hex');
    console.log(`ASSET_HTTP=${assetRes.status}`);
    console.log(`ASSET_SHA256=${sha}`);
    console.log(`EXPECTED_SHA256=${expectedAssetSha}`);
    if (assetRes.ok && sha === expectedAssetSha) {
      passed = true;
      break;
    }
  }

  if (attempt < 6) await sleep(10000);
}

console.log('GATE7_CANONICAL_PAGES=' + (passed ? 'PASS' : 'NOT_YET'));
if (!passed) process.exitCode = 2;
