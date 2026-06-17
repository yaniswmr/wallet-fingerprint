#!/usr/bin/env node
/*
 * collect_signinfo.js — Poll en boucle le VRAI endpoint privé OKX signInfo
 * (ETH mainnet, chainId=1, coinId=3) et logge chaque réponse + les multiplicateurs
 * empiriques par tier, pour confirmer la stabilité de 1.125 / 1.35 / 1.70 dans le temps.
 *
 * Auth générée localement (cf. okx/SIGNING_REVERSE.md) — aucun secret stocké.
 * Sortie : okx/signinfo_live.jsonl (1 ligne JSON par poll).
 *
 * Usage:
 *   node collect_signinfo.js                 # poll toutes les 12 s
 *   node collect_signinfo.js --interval 6
 *   node collect_signinfo.js --once
 *   node collect_signinfo.js --out /chemin.jsonl
 */
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const DEV = {
  devid: '8f6d9778-9531-403d-a3ba-6a0e6ecf6c10',
  fp:    '8f6d9778-9531-403d-a3ba-6a0e6ecf6c10',
  sess:  '2w5n96xp0e5_1781702422349',
  xid:   '1781702421441-c-137',
  ua: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
};
const HOST = 'wallet.okex.org';
const PATH = '/priapi/v2/wallet/tx/signInfo';
const EXPECT = { slow: 1.125, normal: 1.35, fast: 1.70 };

function userDeviceSign(ts) {
  const ua = DEV.ua;
  const sha1 = crypto.createHash('sha1').update(ua).digest('hex');
  const msg = `${ua}^${sha1}^0^${ts}^Linux x86_64^fr-FR`;
  const c = crypto.createCipheriv('aes-128-ecb', Buffer.from('H6379FIktyVeUAje'), null);
  return Buffer.concat([c.update(msg, 'utf8'), c.final()]).toString('base64');
}
function okVerifySign(p, body, tsMs, token) {
  const A = Math.floor(tsMs / 1000);
  const b = crypto.createHash('sha256').update(token, 'utf8').digest('hex');
  const P = Math.floor((A / 600) % 32), k = Math.floor((A / 3600) % 32);
  let h = '';
  for (let N = 0; N < 32; N++) h += b[(P + (k + N) * N) % 32];
  return crypto.createHmac('sha256', Buffer.from(h, 'utf8')).update(p + body, 'utf8').digest('base64');
}

async function pollOnce() {
  const tsMs = Date.now();
  const token = crypto.randomUUID();
  const body = JSON.stringify({ coinId: 3, fromAddr: '0x4524fc0edb972fa09c8af5859025c795c81287fc', chainId: 1 });
  const headers = {
    'accept': 'application/json', 'content-type': 'application/json',
    'app-type': 'web', 'platform': 'plugin', 'plugin-version': '4.4.0', 'plugin-build-version': 'publish',
    'devid': DEV.devid, 'device-token': DEV.devid, 'fingerprint-id': DEV.fp,
    'tmx-session-id': DEV.sess, 'risk-params': `fingerprint-id=${DEV.fp}&fp-status=0&session-id=${DEV.sess}`,
    'x-id-group': DEV.xid, 'x-locale': 'fr_FR', 'x-utc': '2',
    'ok-timestamp': String(tsMs), 'ok-verify-token': token,
    'ok-verify-sign': okVerifySign(PATH, body, tsMs, token),
    'user-device-sign': userDeviceSign(tsMs),
    'user-agent': DEV.ua, 'origin': 'chrome-extension://mcohilncbfahbmgdjkbpemcciiolgcge',
  };
  const r = await fetch(`https://${HOST}${PATH}?t=${tsMs}`, { method: 'POST', headers, body });
  const j = await r.json();
  if (j.code !== 0 || !j?.data?.info?.gasPrice) {
    throw new Error(`code=${j.code} msg=${j.msg || ''}`);
  }
  const gp = j.data.info.gasPrice;
  const base = +gp.baseFee, safe = +gp.safePriorityFee, prop = +gp.proposePriorityFee, fast = +gp.fastPriorityFee;
  const kSlow = (+gp.min - safe) / base, kNorm = (+gp.normal - prop) / base, kFast = (+gp.max - fast) / base;
  const near = (a, b) => Math.abs(a - b) < 1e-4;
  return {
    ts: Math.floor(tsMs / 1000),
    base_fee: gp.baseFee, suggest_base_fee: gp.suggestBaseFee,
    safe_prio: gp.safePriorityFee, propose_prio: gp.proposePriorityFee, fast_prio: gp.fastPriorityFee,
    min: gp.min, normal: gp.normal, max: gp.max,
    k_slow: +kSlow.toFixed(6), k_normal: +kNorm.toFixed(6), k_fast: +kFast.toFixed(6),
    stable: near(kSlow, EXPECT.slow) && near(kNorm, EXPECT.normal) && near(kFast, EXPECT.fast),
  };
}

function arg(name, def) { const i = process.argv.indexOf('--' + name); return i >= 0 ? process.argv[i + 1] : def; }

async function main() {
  const interval = parseFloat(arg('interval', '12')) * 1000;
  const once = process.argv.includes('--once');
  const out = arg('out', path.join(__dirname, 'signinfo_live.jsonl'));
  const G = x => (Number(x) / 1e9).toFixed(3);
  console.log(`signInfo mainnet -> ${out} (interval ${interval / 1000}s). Ctrl-C pour arrêter.`);
  let n = 0, anomalies = 0;
  for (;;) {
    try {
      const row = await pollOnce();
      fs.appendFileSync(out, JSON.stringify(row) + '\n');
      n++;
      const flag = row.stable ? 'ok' : '⚠ DÉRIVE';
      if (!row.stable) anomalies++;
      const t = new Date().toTimeString().slice(0, 8);
      console.log(`[${t}] #${n} base=${G(row.base_fee)}G k=[${row.k_slow}/${row.k_normal}/${row.k_fast}] `
        + `normal=${G(row.normal)}G ${flag}${anomalies ? `  (anomalies cumulées: ${anomalies})` : ''}`);
    } catch (e) {
      console.log(`[${new Date().toTimeString().slice(0, 8)}] warn: ${e.message}`);
    }
    if (once) break;
    await new Promise(r => setTimeout(r, interval));
  }
}
main().catch(e => { console.error(e); process.exit(1); });
