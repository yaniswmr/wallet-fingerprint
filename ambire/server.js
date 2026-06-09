/**
 * Serveur local reproduisant le endpoint GET /gasPrice/ethereum d'Ambire.
 *
 * Usage :
 *   node server.js [PORT] [RPC_URL]
 *
 * Endpoints :
 *   GET /gasPrice/ethereum           → même format que relayer.ambire.com/gasPrice/ethereum
 *   GET /gasPrice/ethereum?block=latest
 *   GET /gasPrice/ethereum?block=21500000   → bloc spécifique (entier décimal)
 *   GET /gasPrice/ethereum?block=0x1482F30  → bloc spécifique (hex)
 */

import http from 'node:http'
import { JsonRpcProvider } from 'ethers'
import { getRelayerAlgoRecommendations } from './gasPrice.js'

const PORT = process.argv[2] ? parseInt(process.argv[2]) : 3000
const RPC_URL = process.argv[3] ?? 'https://ethereum-rpc.publicnode.com'
const REAL_RELAYER = 'https://relayer.ambire.com/gasPrice/ethereum'

const provider = new JsonRpcProvider(RPC_URL)

function bigintReplacer(_key, value) {
  return typeof value === 'bigint' ? value.toString() : value
}

function parseBlockParam(raw) {
  if (!raw || raw === 'latest' || raw === 'pending') return raw ?? 'latest'
  if (raw.startsWith('0x')) return raw
  const n = parseInt(raw, 10)
  if (isNaN(n)) throw new Error(`Invalid block parameter: "${raw}"`)
  return n
}

async function fetchRealRelayer() {
  try {
    const r = await fetch(REAL_RELAYER, { signal: AbortSignal.timeout(5000) })
    const json = await r.json()
    // Le relayer réel enveloppe dans { success, data: { gasPrice } }
    return (json.data ?? json).gasPrice ?? null
  } catch {
    return null
  }
}

function printComparison(ours, real, debug) {
  const speeds = ['slow', 'medium', 'fast', 'ape']
  const w = 18

  console.log('\n' + '─'.repeat(72))
  console.log(`  medianFee=${debug.slowBaseFee} Wei  tipUnit=${debug.tipUnit} Wei  (window=${debug.baseFeeHistoryBlocks} blocs)`)
  console.log('─'.repeat(72))
  console.log(`  ${'speed'.padEnd(8)} ${'NOTRE (Wei)'.padStart(w)} ${'RELAYER (Wei)'.padStart(w)} ${'Δ%'.padStart(8)}`)
  console.log('─'.repeat(72))

  for (const s of speeds) {
    const o = BigInt(ours[s])
    const rVal = real ? BigInt(real[s]) : null
    const delta = rVal ? ((Number(o - rVal) / Number(rVal)) * 100).toFixed(2) + '%' : 'N/A'
    const marker = rVal && Math.abs(Number(o - rVal) / Number(rVal)) > 0.05 ? ' !' : '  '
    console.log(`${marker} ${s.padEnd(8)} ${String(o).padStart(w)} ${rVal ? String(rVal).padStart(w) : '?'.padStart(w)} ${delta.padStart(8)}`)
  }

  console.log('─'.repeat(36) + ' tips ' + '─'.repeat(30))
  const ourPrio = ours.maxPriorityFeePerGas
  const realPrio = real?.maxPriorityFeePerGas ?? {}
  for (const s of speeds) {
    const o = BigInt(ourPrio[s] ?? 0)
    const rVal = realPrio[s] != null ? BigInt(realPrio[s]) : null
    const delta = rVal ? ((Number(o - rVal) / (Number(rVal) || 1)) * 100).toFixed(2) + '%' : 'N/A'
    const marker = rVal && Number(rVal) > 0 && Math.abs(Number(o - rVal) / Number(rVal)) > 0.10 ? ' !' : '  '
    console.log(`${marker} tip_${s.padEnd(5)} ${String(o).padStart(w)} ${rVal != null ? String(rVal).padStart(w) : '?'.padStart(w)} ${delta.padStart(8)}`)
  }
  console.log('─'.repeat(72) + '\n')
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)

  if (!url.pathname.startsWith('/gasPrice/ethereum')) {
    res.writeHead(404, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: 'Not found. Use /gasPrice/ethereum' }))
    return
  }

  try {
    const blockParam = parseBlockParam(url.searchParams.get('block') ?? 'latest')

    const [result, realGp] = await Promise.all([
      getRelayerAlgoRecommendations(provider, blockParam),
      fetchRealRelayer()
    ])

    const { gasPrice, _debug } = result

    printComparison(gasPrice, realGp, _debug)

    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ gasPrice }, bigintReplacer, 2))
  } catch (err) {
    console.error('[error]', err.message)
    res.writeHead(500, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: err.message }))
  }
})

server.listen(PORT, () => {
  console.log(`ambire-gasfees server  →  http://localhost:${PORT}/gasPrice/ethereum`)
  console.log(`RPC : ${RPC_URL}`)
  console.log(`Compares against : ${REAL_RELAYER}`)
  console.log()
})
