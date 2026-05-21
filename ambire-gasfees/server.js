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
 *
 * Réponse (identique au relayer) :
 *   {
 *     "gasPrice": {
 *       "slow":   <baseFee>,
 *       "medium": <baseFee × 1.021723>,
 *       "fast":   <baseFee × 1.065169>,
 *       "ape":    <baseFee × 1.195507>,
 *       "maxPriorityFeePerGas": { "slow": 0, "medium": X, "fast": 2X, "ape": 3X },
 *       "updated": <timestamp ms>
 *     }
 *   }
 */

import http from 'node:http'
import { JsonRpcProvider } from 'ethers'
import { getRelayerAlgoRecommendations } from './gasPrice.js'

const PORT = process.argv[2] ? parseInt(process.argv[2]) : 3000
const RPC_URL = process.argv[3] ?? 'https://ethereum-rpc.publicnode.com'

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

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`)

  if (!url.pathname.startsWith('/gasPrice/ethereum')) {
    res.writeHead(404, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: 'Not found. Use /gasPrice/ethereum' }))
    return
  }

  try {
    const blockParam = parseBlockParam(url.searchParams.get('block') ?? 'latest')
    const { gasPrice } = await getRelayerAlgoRecommendations(provider, blockParam)

    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ gasPrice }, bigintReplacer, 2))
  } catch (err) {
    res.writeHead(500, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: err.message }))
  }
})

server.listen(PORT, () => {
  console.log(`ambire-gasfees server running on http://localhost:${PORT}`)
  console.log(`RPC: ${RPC_URL}`)
  console.log(``)
  console.log(`  GET http://localhost:${PORT}/gasPrice/ethereum`)
  console.log(`  GET http://localhost:${PORT}/gasPrice/ethereum?block=21500000`)
  console.log(`  GET http://localhost:${PORT}/gasPrice/ethereum?block=0x148B550`)
})
