#!/usr/bin/env node
/*
 * call_signinfo.js — Appelle l'endpoint privé OKX signInfo en générant TOUS les
 * headers d'auth localement (reverse complet, cf. okx/SIGNING_REVERSE.md).
 *
 *   ok-verify-token = randomUUID() jetable
 *   ok-timestamp    = Date.now() ms
 *   ok-verify-sign  = base64(HMAC-SHA256(clé dérivée du token+ts_sec, path+body))
 *   user-device-sign= base64(AES-128-ECB("H6379FIktyVeUAje", "UA^SHA1(UA)^0^ts^plat^lang"))
 *   devid / fingerprint-id / tmx-session-id / x-id-group = réutilisés de la capture (stables/install)
 *
 * Usage:
 *   node call_signinfo.js                       # mainnet ETH (chainId 1)
 *   node call_signinfo.js --chain 11155111 --coin 21100   # rejeu Sepolia (oracle)
 *   node call_signinfo.js --addr 0x....
 */
const crypto = require('crypto');

// --- IDs spécifiques à l'installation (depuis la capture DevTools) ---
const DEV = {
  devid:        '8f6d9778-9531-403d-a3ba-6a0e6ecf6c10',
  fingerprintId:'8f6d9778-9531-403d-a3ba-6a0e6ecf6c10',
  sessionId:    '2w5n96xp0e5_1781702422349',   // = tmx-session-id
  xIdGroup:     '1781702421441-c-137',
  ua: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
  platform: 'Linux x86_64',
  language: 'fr-FR',
  pluginVersion: '4.4.0',
};

function userDeviceSign(ts) {
  const ua = DEV.ua;
  const sha1 = crypto.createHash('sha1').update(ua).digest('hex');
  const msg = `${ua}^${sha1}^0^${ts}^${DEV.platform}^${DEV.language}`;
  const c = crypto.createCipheriv('aes-128-ecb', Buffer.from('H6379FIktyVeUAje'), null);
  return Buffer.concat([c.update(msg, 'utf8'), c.final()]).toString('base64');
}

function okVerify(path, body, tsMs, token) {
  const A = Math.floor(tsMs / 1000);
  const b = crypto.createHash('sha256').update(token, 'utf8').digest('hex'); // 64 hex
  const P = Math.floor((A / 600) % 32), k = Math.floor((A / 3600) % 32);
  let h = '';
  for (let N = 0; N < 32; N++) h += b[(P + (k + N) * N) % 32];
  const sig = crypto.createHmac('sha256', Buffer.from(h, 'utf8')).update(path + body, 'utf8').digest('base64');
  return sig;
}

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 ? process.argv[i + 1] : def;
}

async function main() {
  const chainId = parseInt(arg('chain', '1'), 10);
  const coinId  = parseInt(arg('coin', '0'), 10);   // 0 = omis
  const addr    = arg('addr', '0x4524fc0edb972fa09c8af5859025c795c81287fc');
  const host    = arg('host', 'wallet.okex.org');

  const bodyObj = { fromAddr: addr, chainId };
  if (coinId) bodyObj.coinId = coinId;
  // ordre observé dans la capture : coinId, fromAddr, chainId
  const ordered = {};
  if (coinId) ordered.coinId = coinId;
  ordered.fromAddr = addr; ordered.chainId = chainId;
  const body = JSON.stringify(ordered);

  const tsMs = Date.now();
  const token = crypto.randomUUID();
  const path = '/priapi/v2/wallet/tx/signInfo';
  const url = `https://${host}${path}?t=${tsMs}`;

  const headers = {
    'accept': 'application/json',
    'content-type': 'application/json',
    'app-type': 'web',
    'platform': 'plugin',
    'plugin-version': DEV.pluginVersion,
    'plugin-build-version': 'publish',
    'devid': DEV.devid,
    'device-token': DEV.devid,
    'fingerprint-id': DEV.fingerprintId,
    'tmx-session-id': DEV.sessionId,
    'risk-params': `fingerprint-id=${DEV.fingerprintId}&fp-status=0&session-id=${DEV.sessionId}`,
    'x-id-group': DEV.xIdGroup,
    'x-locale': 'fr_FR',
    'x-utc': '2',
    'x-zkdex-env': '0',
    'ok-timestamp': String(tsMs),
    'ok-verify-token': token,
    'ok-verify-sign': okVerify(path, body, tsMs, token),
    'user-device-sign': userDeviceSign(tsMs),
    'user-agent': DEV.ua,
    'origin': 'chrome-extension://mcohilncbfahbmgdjkbpemcciiolgcge',
  };

  const r = await fetch(url, { method: 'POST', headers, body });
  const text = await r.text();
  console.log(`POST ${url}`);
  console.log(`body: ${body}`);
  console.log(`HTTP ${r.status}`);
  try {
    const j = JSON.parse(text);
    console.log(JSON.stringify(j, null, 2).slice(0, 1500));
    const gp = j?.data?.info?.gasPrice;
    if (gp) {
      const G = x => (Number(x) / 1e9).toFixed(4);
      console.log(`\n>>> base=${G(gp.baseFee)}G  normal(maxFee)=${G(gp.normal)}G  min=${G(gp.min)}G  max=${G(gp.max)}G`);
    }
  } catch { console.log(text.slice(0, 800)); }
}
main().catch(e => { console.error(e); process.exit(1); });
