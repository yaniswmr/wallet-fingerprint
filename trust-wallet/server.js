/**
 * Serveur local reproduisant l'estimateur 3-niveaux de Trust Wallet
 * (slow / normal / fast → mappés sur low / medium / high de l'UI).
 *
 * Formules : cf. trust-wallet/FORMULES.md (ESTIMATEUR 3-NIVEAUX).
 *
 *   tips :  eth_feeHistory(20, "pending", [10, 60, 99])
 *           low_tip    = mean( reward[p10] sur 20 blocs )
 *           medium_tip = mean( reward[p60] sur 20 blocs )
 *           high_tip   = mean( reward[p99] sur 20 blocs )
 *
 *   baseFee : eth_getBlockByNumber("latest", false).baseFeePerGas   (bloc MINÉ, pas pending)
 *
 *   maxFeePerGas :  base × facteur(tier) + tip(tier)
 *           low    = base × 0.8 + low_tip
 *           medium = base × 1.0 + medium_tip
 *           high   = base × 1.3 + high_tip
 *
 *   coût trx (ETH) = maxFeePerGas × gasLimit / 1e18
 *
 * Usage :
 *   node server.js [PORT] [RPC_URL]
 *
 * Endpoint :
 *   GET /gas                  → low/medium/high { maxFeePerGas, maxPriorityFeePerGas, costEth }
 *   GET /gas?gasLimit=21000   → gasLimit personnalisé (défaut 21000 = transfert ETH simple)
 */

import http from 'node:http'
import { writeFileSync, appendFileSync } from 'node:fs'

const PORT = process.argv[2] ? parseInt(process.argv[2]) : 3100
const RPC_URL = process.argv[3] ?? 'https://ethereum-rpc.publicnode.com'

// Fichiers de log (à côté de server.js) pour comparer avec la réponse de Trust
const LAST_FH_FILE = new URL('./last_feehistory.json', import.meta.url)
const FH_LOG_FILE = new URL('./feehistory_log.jsonl', import.meta.url)

// Facteurs base-fee par tier (défaut FeeFactor de Trust Wallet) : low ×0.8, medium ×1.0, high ×1.3
// Exprimés en fraction num/den pour rester en BigInt.
// Modèle vérifié (bundle, 2 étages) :
//   maxFee_tier = baseFee(latest) × 1.20 × facteur_tier + tip_tier
//   ×1.20 = inflation globale de l'étage 1 (Cr(base,20)) ; facteurs Ethereum : low 0.91, medium 1.0, high 1.3
// → multiplicateur EFFECTIF sur baseFee = 1.20 × facteur_tier :
const FACTORS = {
  low:    { num: 1092n, den: 1000n },  // 1.20 × 0.91
  medium: { num: 1200n, den: 1000n },  // 1.20 × 1.00
  high:   { num: 1560n, den: 1000n },  // 1.20 × 1.30
}

// Mapping tier → index du percentile dans la requête feeHistory [10, 60, 99]
const TIER_PCT_INDEX = { low: 0, medium: 1, high: 2 }

const FEEHISTORY_BLOCKS = 20
const WEI_PER_ETH = 1_000_000_000_000_000_000n

let rpcId = 0
async function rpc(method, params) {
  const res = await fetch(RPC_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: ++rpcId, method, params }),
    signal: AbortSignal.timeout(10000),
  })
  const json = await res.json()
  if (json.error) throw new Error(`${method}: ${json.error.message}`)
  return json.result
}

// baseFee projeté du bloc N+1 (formule EIP-1559) — fallback si le RPC ne sert pas de bloc "pending"
function projectNextBaseFee(block) {
  const base = BigInt(block.baseFeePerGas)
  const gasUsed = BigInt(block.gasUsed)
  const gasTarget = BigInt(block.gasLimit) / 2n
  if (gasUsed === gasTarget) return base
  if (gasUsed > gasTarget) {
    const delta = (base * (gasUsed - gasTarget)) / gasTarget / 8n
    return base + (delta === 0n ? 1n : delta)
  }
  const delta = (base * (gasTarget - gasUsed)) / gasTarget / 8n
  return base - delta
}

// Agrégation tip d'un tier — fidèle à la fn `Tg` du bundle Trust (background.js) :
//   tip = somme( reward[bloc][idx] sur la fenêtre ) / 20   (diviseur FIXE 20, division ENTIÈRE)
// PAS /N : si le RPC renvoie <20 blocs, Trust divise quand même par 20 → tip plus bas.
// Aucun filtre, aucun plancher (sur Ethereum / chaîne par défaut).
function tipFromRewards(rewards, idx) {
  const sum = rewards.reduce((acc, r) => acc + (r[idx] ?? 0n), 0n)
  return sum / BigInt(FEEHISTORY_BLOCKS)
}

const toGwei = (wei) => (Number(wei) / 1e9).toFixed(4)

// wei (BigInt) → ETH avec 18 décimales pleines (pas de perte de précision)
function weiToEth(wei) {
  const intPart = wei / WEI_PER_ETH
  const frac = (wei % WEI_PER_ETH).toString().padStart(18, '0').replace(/0+$/, '')
  return frac ? `${intPart}.${frac}` : `${intPart}`
}

async function computeEstimate(gasLimit, baseFeeSource = 'latest', pinnedBlock = null, includeRaw = false) {
  // Mode épinglé (?block=N) : fenêtre déterministe et reproductible pour comparer à l'app
  // à un bloc précis. Sinon mode live ("pending"/"latest").
  const isPinned = pinnedBlock !== null
  // Tag du bloc le plus récent de la fenêtre feeHistory.
  // ⚠️ "latest" (pas "pending") : Trust appelle "pending" mais son nœud (twnodes) renvoie une fenêtre
  // finissant au dernier bloc MINÉ. publicnode interprète "pending" comme +1 bloc (ajoute un pending
  // volatil) → fenêtre décalée d'un cran → p60 imprécis. "latest" aligne la fenêtre sur celle de Trust
  // (20 blocs minés, percentiles déterministes et identiques entre nœuds).
  const newestTag = isPinned ? '0x' + pinnedBlock.toString(16) : 'latest'

  // 1) baseFee : Trust appelle le RPC avec "pending" → baseFee projeté du bloc N+1
  //    (plus élevé que le latest quand les blocs sont >50% pleins). On garde latest pour debug.
  //    En mode épinglé : "latest" = bloc N, "pending" = bloc N+1 (déjà miné → valeur réelle).
  const latestTag = isPinned ? '0x' + pinnedBlock.toString(16) : 'latest'
  const pendingTag = isPinned ? '0x' + (pinnedBlock + 1).toString(16) : 'pending'
  const [latestBlock, pendingBlock] = await Promise.all([
    rpc('eth_getBlockByNumber', [latestTag, false]),
    rpc('eth_getBlockByNumber', [pendingTag, false]).catch(() => null),
  ])
  if (!latestBlock || !latestBlock.baseFeePerGas) throw new Error(`Bloc ${latestTag} non-EIP-1559 ou indisponible`)
  const baseFeeLatest = BigInt(latestBlock.baseFeePerGas)

  // baseFee du pending si disponible, sinon projection N+1 calculée depuis latest
  let baseFeePending
  if (pendingBlock && pendingBlock.baseFeePerGas) {
    baseFeePending = BigInt(pendingBlock.baseFeePerGas)
  } else {
    baseFeePending = projectNextBaseFee(latestBlock) // fallback si le RPC ne sert pas de pending
  }

  const baseFee = baseFeeSource === 'latest' ? baseFeeLatest : baseFeePending
  const blockNumber = parseInt(latestBlock.number, 16)

  // 2) tips : eth_feeHistory(20, <newest>, [10, 60, 99]) → moyenne du percentile sur 20 blocs
  const fh = await rpc('eth_feeHistory', [
    '0x' + FEEHISTORY_BLOCKS.toString(16),
    newestTag,
    [10, 60, 99],
  ])

  // ── LOG : on écrit la réponse BRUTE de feeHistory (format identique à Trust) ───────────
  // last_feehistory.json = le dernier appel (écrasé) → à differ directement avec la réponse Trust.
  // feehistory_log.jsonl = historique horodaté de chaque poll → pour retrouver le bon instant.
  try {
    const rawWrapped = { jsonrpc: '2.0', id: 0, result: fh }
    writeFileSync(LAST_FH_FILE, JSON.stringify(rawWrapped, null, 2))
    const oldestDec = fh.oldestBlock ? parseInt(fh.oldestBlock, 16) : null
    const logLine = {
      at: new Date().toISOString(),
      newestTag,
      window: oldestDec != null ? { oldest: oldestDec, newest: oldestDec + (fh.reward?.length ?? 0) - 1 } : null,
      latestBlock: blockNumber,
      baseFeeLatestWei: baseFeeLatest.toString(),
      baseFeeLatestGwei: toGwei(baseFeeLatest),
    }
    appendFileSync(FH_LOG_FILE, JSON.stringify(logLine) + '\n')
    console.log(`[log] feeHistory → last_feehistory.json  (window ${logLine.window?.oldest}..${logLine.window?.newest}, baseFee ${logLine.baseFeeLatestGwei} Gwei)`)
  } catch (e) {
    console.error('[log] échec écriture:', e.message)
  }

  // fh.reward = [ [p10,p60,p99], ... ] par bloc
  const rewards = (fh.reward ?? []).map((r) => r.map((x) => BigInt(x)))

  const tiers = {}
  for (const [tier, factor] of Object.entries(FACTORS)) {
    const tip = tipFromRewards(rewards, TIER_PCT_INDEX[tier])
    // base × facteur + tip
    const maxFeePerGas = (baseFee * factor.num) / factor.den + tip
    const costWei = maxFeePerGas * gasLimit
    tiers[tier] = {
      maxFeePerGas: maxFeePerGas.toString(),
      maxPriorityFeePerGas: tip.toString(),
      maxFeePerGasGwei: toGwei(maxFeePerGas),
      maxPriorityFeePerGasGwei: toGwei(tip),
      costWei: costWei.toString(),
      costEth: weiToEth(costWei),
    }
  }

  return {
    tiers,
    _debug: {
      mode: isPinned ? `pinned@${pinnedBlock}` : 'live',
      baseFeeSource, // "pending" (défaut, = bloc N+1) ou "latest"
      baseFeePerGas: baseFee.toString(),
      baseFeePerGasGwei: toGwei(baseFee),
      baseFeeLatestGwei: toGwei(baseFeeLatest),
      baseFeePendingGwei: toGwei(baseFeePending),
      blockNumber,
      // fenêtre des 20 blocs réellement échantillonnés pour les tips (reproductibilité)
      feeHistoryWindow: fh.oldestBlock
        ? { oldest: parseInt(fh.oldestBlock, 16), newest: parseInt(fh.oldestBlock, 16) + rewards.length - 1 }
        : null,
      gasLimit: gasLimit.toString(),
      feeHistoryBlocks: rewards.length,
      formula: 'maxFee = baseFee(latest) × 1.20 × facteur + tip',
      factors: { low: '0.91', medium: '1.0', high: '1.3', global: '1.20' },
      // ?raw=1 : reward[bloc] = [p10,p60,p99] en wei, pour diff ligne-à-ligne avec la réponse Trust.
      // La DERNIÈRE ligne est le bloc pending (local au nœud) — c'est elle qui diffère entre nœuds.
      rewardsByBlock: includeRaw && fh.oldestBlock
        ? rewards.map((r, i) => ({
            block: parseInt(fh.oldestBlock, 16) + i,
            isPending: !isPinned && i === rewards.length - 1,
            reward: r.map((x) => x.toString()),
          }))
        : undefined,
      updated: new Date().toISOString(),
    },
  }
}

function printTable(est) {
  const d = est._debug
  console.log('\n' + '─'.repeat(78))
  console.log(`  baseFee=${d.baseFeePerGasGwei} Gwei  (bloc latest #${d.blockNumber})  gasLimit=${d.gasLimit}  fenêtre=${d.feeHistoryBlocks} blocs`)
  console.log('─'.repeat(78))
  console.log(`  ${'tier'.padEnd(8)} ${'maxFee (Gwei)'.padStart(15)} ${'prio (Gwei)'.padStart(13)} ${'coût (ETH)'.padStart(22)}`)
  console.log('─'.repeat(78))
  for (const tier of ['low', 'medium', 'high']) {
    const t = est.tiers[tier]
    console.log(`  ${tier.padEnd(8)} ${t.maxFeePerGasGwei.padStart(15)} ${t.maxPriorityFeePerGasGwei.padStart(13)} ${t.costEth.padStart(22)}`)
  }
  console.log('─'.repeat(78) + '\n')
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)

  // CORS — pour pouvoir requêter depuis un navigateur
  res.setHeader('Access-Control-Allow-Origin', '*')
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS')
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }

  if (!url.pathname.startsWith('/gas')) {
    res.writeHead(404, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: 'Not found. Use GET /gas?gasLimit=21000' }))
    return
  }

  try {
    const gasLimitRaw = url.searchParams.get('gasLimit') ?? '21000'
    const gasLimit = BigInt(gasLimitRaw)
    const baseFeeSource = url.searchParams.get('basefee') === 'pending' ? 'pending' : 'latest'

    // ?block=N (déc ou hex) → fenêtre épinglée et reproductible ; absent → mode live (pending)
    const blockRaw = url.searchParams.get('block')
    let pinnedBlock = null
    if (blockRaw && blockRaw !== 'latest' && blockRaw !== 'pending') {
      pinnedBlock = blockRaw.startsWith('0x') ? parseInt(blockRaw, 16) : parseInt(blockRaw, 10)
      if (Number.isNaN(pinnedBlock)) throw new Error(`block invalide: "${blockRaw}"`)
    }

    const includeRaw = url.searchParams.get('raw') === '1'

    const est = await computeEstimate(gasLimit, baseFeeSource, pinnedBlock, includeRaw)
    printTable(est)

    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(est, null, 2))
  } catch (err) {
    console.error('[error]', err.message)
    res.writeHead(500, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: err.message }))
  }
})

server.listen(PORT, () => {
  console.log(`trust-wallet gas server  →  http://localhost:${PORT}/gas`)
  console.log(`RPC : ${RPC_URL}`)
  console.log(`Défaut gasLimit = 21000 (transfert ETH). Override : /gas?gasLimit=...`)
  console.log()
})
