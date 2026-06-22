/**
 * Serveur local reproduisant pimlico_getUserOperationGasPrice (bundler Alto).
 *
 * Formule (cf. PIMLICO_FORMULES.md, github.com/pimlicolabs/alto) :
 *   maxFee_raw   = floor(baseFee * 120/100) + maxPrio_raw   // viem base mult 1.20
 *   maxPrio_bump = floor(maxPrio_raw * 115/100)             // gasPriceBump
 *   maxFee_bump  = floor(maxFee_raw  * 115/100)
 *   tier(v, m)   = floor(v * m / 100)                       // m = 100/105/110
 *
 * La SEULE entrée non reproductible depuis un nœud public est `maxPrio_raw`
 * (l'oracle eth_maxPriorityFeePerGas du nœud de Pimlico ; les nœuds publics
 * renvoient 0.0001 Gwei en réseau calme). Source choisie via MAXPRIO_SOURCE :
 *   pimlico    (défaut) → maxPrio_raw = slow.maxPriorityFeePerGas / 1.15 de l'API,
 *                         + baseFee aligné sur le bon bloc → les 6 champs matchent au wei.
 *   feehistory          → médiane(FH_WINDOW blocs) du reward p{FH_PERCENTILE} (estimation).
 *   node                → eth_maxPriorityFeePerGas du nœud (0.0001 sur nœud public).
 *
 * Usage :   node server.js [PORT] [RPC_URL]
 * Endpoints : GET /  ou  GET /gasPrice/ethereum  → { slow, standard, fast } en HEX
 */

import http from 'node:http'
import { JsonRpcProvider } from 'ethers'

const PORT = process.argv[2] ? parseInt(process.argv[2]) : 3000
const RPC_URL = process.argv[3] ?? 'https://app.functori.com/reth'
const PIM_URL = process.env.PIM_URL
  ?? 'https://api.pimlico.io/v2/1/rpc?apikey=pim_JPU8iy5BTbfGchPMJXQ1uP'
const MAXPRIO_SOURCE = process.env.MAXPRIO_SOURCE ?? 'pimlico'
const FH_PERCENTILE = parseInt(process.env.FH_PERCENTILE ?? '60')
const FH_WINDOW = parseInt(process.env.FH_WINDOW ?? '4')

const provider = new JsonRpcProvider(RPC_URL)

// --- Constantes de l'instance hébergée (vérifiées au wei, cf. PIMLICO_FORMULES.md) ---
const VIEM_BASE_MULT = 120n
const GAS_PRICE_BUMP = 115n
const TIER_MULT = { slow: 100n, standard: 105n, fast: 110n }

const scale = (value, percent) => (value * percent) / 100n // floor (BigInt)
const toHex = (v) => '0x' + v.toString(16)
const maxBig = (a, b) => (a > b ? a : b)
const medianBig = (arr) => {
  const s = [...arr].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
  const m = s.length >> 1
  return s.length % 2 === 0 ? (s[m - 1] + s[m]) / 2n : s[m]
}

/** Reproduit le pipeline complet à partir des deux entrées brutes. */
function computeGasPrice(maxPrioRaw, baseFee) {
  const maxFeeRaw = scale(baseFee, VIEM_BASE_MULT) + maxPrioRaw
  const maxPrioBump = scale(maxPrioRaw, GAS_PRICE_BUMP)
  const maxFeeBump = maxBig(scale(maxFeeRaw, GAS_PRICE_BUMP), maxPrioBump)
  const out = {}
  for (const [tier, m] of Object.entries(TIER_MULT)) {
    out[tier] = {
      maxFeePerGas: toHex(scale(maxFeeBump, m)),
      maxPriorityFeePerGas: toHex(scale(maxPrioBump, m)),
    }
  }
  return out
}

/** Inverse floor(maxPrioRaw * 115/100) == slowPrio → candidats. */
function recoverMaxPrioRaw(slowPrio) {
  const lo = (slowPrio * 100n + GAS_PRICE_BUMP - 1n) / GAS_PRICE_BUMP // ceil
  const cands = []
  for (let c = lo - 1n; c <= lo + 2n; c++) {
    if (scale(c, GAS_PRICE_BUMP) === slowPrio) cands.push(c)
  }
  return cands
}

async function fetchRealPimlico() {
  try {
    const r = await fetch(PIM_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'pimlico_getUserOperationGasPrice', params: [] }),
      signal: AbortSignal.timeout(8000),
    })
    return (await r.json()).result ?? null
  } catch {
    return null
  }
}

/** Renvoie { maxPrioRaw, baseFee, blockNumber, src } selon MAXPRIO_SOURCE. */
async function resolveInputs(real) {
  const [block, fh] = await Promise.all([
    provider.getBlock('latest'),
    provider.send('eth_feeHistory', ['0x6', 'latest', [FH_PERCENTILE]]),
  ])
  const recentBaseFees = fh.baseFeePerGas.map((x) => BigInt(x)).reverse() // head/proj first
  const dbg = { blockNumber: block.number }

  if (MAXPRIO_SOURCE === 'pimlico' && real) {
    const slowPrio = BigInt(real.slow.maxPriorityFeePerGas)
    const slowFee = BigInt(real.slow.maxFeePerGas)
    const cands = recoverMaxPrioRaw(slowPrio)
    // aligne le baseFee : trouve (cand, bf) qui reproduit slow.maxFeePerGas de l'API.
    for (const bf of recentBaseFees) {
      for (const c of cands) {
        if (BigInt(computeGasPrice(c, bf).slow.maxFeePerGas) === slowFee) {
          return { maxPrioRaw: c, baseFee: bf, ...dbg, src: 'pimlico (aligné)' }
        }
      }
    }
    return { maxPrioRaw: cands[0], baseFee: block.baseFeePerGas, ...dbg, src: 'pimlico (head)' }
  }

  if (MAXPRIO_SOURCE === 'feehistory') {
    const rewards = fh.reward.slice(-FH_WINDOW).map((b) => BigInt(b[0]))
    return { maxPrioRaw: medianBig(rewards), baseFee: block.baseFeePerGas, ...dbg, src: `feehistory p${FH_PERCENTILE}/${FH_WINDOW}` }
  }

  // 'node'
  const mp = await provider.send('eth_maxPriorityFeePerGas', [])
  return { maxPrioRaw: BigInt(mp), baseFee: block.baseFeePerGas, ...dbg, src: 'node oracle' }
}

function printComparison(ours, real, dbg) {
  const W = 14
  console.log('\n' + '─'.repeat(74))
  console.log(`  block=${dbg.blockNumber}  baseFee=${dbg.baseFee}  maxPrio_raw=${dbg.maxPrioRaw}  [src: ${dbg.src}]`)
  console.log('─'.repeat(74))
  console.log(`  ${'champ'.padEnd(28)} ${'NOTRE (hex)'.padStart(W)} ${'PIMLICO (hex)'.padStart(W)}  ok`)
  console.log('─'.repeat(74))
  let allOk = true
  for (const tier of ['slow', 'standard', 'fast']) {
    for (const field of ['maxFeePerGas', 'maxPriorityFeePerGas']) {
      const o = ours[tier][field]
      const r = real ? real[tier][field] : null
      const ok = r != null ? BigInt(o) === BigInt(r) : null
      if (ok === false) allOk = false
      console.log(`  ${(tier + '.' + field).padEnd(28)} ${o.padStart(W)} ${(r ?? '?').padStart(W)}  ${ok == null ? '?' : ok ? '✓' : '✗'}`)
    }
  }
  console.log('─'.repeat(74))
  if (real) console.log(`  → ${allOk ? 'EXACT : les 6 champs identiques au wei' : 'écart (voir maxPrio_raw / alignement baseFee)'}`)
  console.log()
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)
  if (url.pathname !== '/' && !url.pathname.startsWith('/gasPrice')) {
    res.writeHead(404, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: 'Not found. Use / or /gasPrice/ethereum' }))
    return
  }
  try {
    const real = await fetchRealPimlico()
    const inputs = await resolveInputs(real)
    const tiers = computeGasPrice(inputs.maxPrioRaw, inputs.baseFee)
    printComparison(tiers, real, inputs)
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify(tiers, null, 4))
  } catch (err) {
    console.error('[error]', err.message)
    res.writeHead(500, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: err.message }))
  }
})

server.listen(PORT, () => {
  console.log(`pimlico-gasfees server  →  http://localhost:${PORT}/`)
  console.log(`RPC     : ${RPC_URL}`)
  console.log(`Compare : ${PIM_URL}`)
  console.log(`Source maxPrio_raw : ${MAXPRIO_SOURCE}`)
  console.log(`Formule : baseFee×1.20 → +maxPrio → bump×1.15 → tiers 100/105/110 (floor)`)
  console.log()
})
