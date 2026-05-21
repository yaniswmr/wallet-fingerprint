/**
 * Gas price calculation pour Ethereum mainnet.
 *
 * Deux algorithmes :
 *
 * 1. NOUVEAU (ambire-common/src/libs/gasPrice/gasPrice.ts) — utilisé par l'extension v2
 *    → IQR statistique sur le bloc, résultat non-linéaire entre speeds
 *    → Format de sortie : { maxFeePerGas, maxPriorityFeePerGas } par speed
 *
 * 2. ANCIEN (relayer.ambire.com / gasOracle.js — code privé) — utilisé par le relayer
 *    → Ratios fixes entre speeds : 1 : 1.021723 : 1.065169 : 1.195507 (baseFee)
 *    → Tips linéaires : 0 : 1 : 2 : 3 × tip_unit
 *    → Format : { slow, medium, fast, ape, maxPriorityFeePerGas: {slow,medium,fast,ape} }
 *    → L'algorithme exact pour slow et tip_unit est dans le code privé du relayer.
 *    → Ici on approche slow par la moyenne des derniers blocs, tip_unit par la médiane IQR.
 */

// Ratios inter-speeds hardcodés dans le relayer (reverse-engineered, constants, toujours identiques)
// Source : observations répétées du relayer.ambire.com
const RELAYER_SPEED_RATIOS = {
  slow:   1_000_000n,
  medium: 1_021_723n,
  fast:   1_065_169n,
  ape:    1_195_507n,
}
const RATIO_DENOM = 1_000_000n

// Nombre de blocs à moyenner pour estimer "slow" (baseFee)
const BASEFEE_HISTORY_BLOCKS = 10

// Speeds pour l'algo nouveau (extension v2)
const SPEEDS = [
  { name: 'slow',   baseFeeAddBps: 0n    },
  { name: 'medium', baseFeeAddBps: 500n  },
  { name: 'fast',   baseFeeAddBps: 1000n },
  { name: 'ape',    baseFeeAddBps: 1500n }
]

// ─── Helpers ──────────────────────────────────────────────────────────────────

function filterOutliers(data) {
  if (!data.length) return []
  data.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
  const q1 = data[Math.floor(data.length / 4)]
  const end = Math.min(Math.ceil(data.length * 3 / 4), data.length - 1)
  const q2 = data[end]
  if (!q1 || !q2) return data
  const iqr = q2 - q1
  return data.filter(x => x <= q2 + (iqr * 15n) / 10n && x >= q1 - (iqr * 15n) / 10n)
}

function nthGroup(data, n, outOf = 4) {
  const step = Math.max(1, Math.floor(data.length / outOf))
  const at = n * step
  const end = n !== 3 || data.length < 4 ? at + step : data.length
  return data.slice(at, end)
}

function average(data) {
  if (!data.length) return 0n
  return data.reduce((a, b) => a + b, 0n) / BigInt(data.length)
}

// ─── Fetch de bloc ─────────────────────────────────────────────────────────────

async function fetchBlock(provider, blockTag) {
  const tag = typeof blockTag === 'number'
    ? '0x' + blockTag.toString(16)
    : blockTag

  const raw = await provider.send('eth_getBlockByNumber', [tag, true])
  if (!raw) return null

  return {
    baseFeePerGas: raw.baseFeePerGas ? BigInt(raw.baseFeePerGas) : null,
    gasLimit: BigInt(raw.gasLimit),
    gasUsed: BigInt(raw.gasUsed),
    number: parseInt(raw.number, 16),
    hash: raw.hash,
    timestamp: parseInt(raw.timestamp, 16),
    prefetchedTransactions: (raw.transactions ?? [])
      .filter(tx => typeof tx !== 'string')
      .map(tx => ({
        gasPrice: tx.gasPrice ? BigInt(tx.gasPrice) : null,
        maxPriorityFeePerGas: tx.maxPriorityFeePerGas ? BigInt(tx.maxPriorityFeePerGas) : null
      }))
  }
}

// ─── Algo NOUVEAU (extension v2) ───────────────────────────────────────────────
// Copié fidèlement depuis ambire-common/src/libs/gasPrice/gasPrice.ts

export async function getNewAlgoRecommendations(provider, blockTag = 'latest') {
  const block = await fetchBlock(provider, blockTag)
  if (!block) throw new Error(`Block not found: ${blockTag}`)
  if (!block.baseFeePerGas || block.baseFeePerGas === 0n)
    throw new Error('Non-EIP-1559 block')

  const gasTarget = block.gasLimit / 2n
  const baseFee = block.baseFeePerGas
  const getBaseFeeDelta = delta => (baseFee * delta) / gasTarget / 8n

  let expectedBaseFee = baseFee
  if (block.gasUsed > gasTarget) {
    const delta = getBaseFeeDelta(block.gasUsed - gasTarget)
    expectedBaseFee += delta === 0n ? 1n : delta
  }

  const tips = filterOutliers(
    block.prefetchedTransactions
      .map(tx => tx.maxPriorityFeePerGas)
      .filter(t => t !== null && t > 0n)
  )

  const result = []
  let prev = null
  SPEEDS.forEach(({ name, baseFeeAddBps }, i) => {
    const baseFee_speed = expectedBaseFee + (expectedBaseFee * baseFeeAddBps) / 10000n
    let tip = average(nthGroup(tips, i))
    if (tip < 100000n) tip = 100000n
    if (prev) {
      const divider = name === 'ape' ? 2n : 8n
      const min = prev + prev / divider
      if (tip < min) tip = min
    }
    result.push({ name, baseFeePerGas: baseFee_speed, maxPriorityFeePerGas: tip })
    prev = tip
  })

  return { speeds: result, block }
}

// ─── Algo ANCIEN (relayer.ambire.com) ──────────────────────────────────────────
//
// Format de sortie identique au relayer :
//   { slow, medium, fast, ape, maxPriorityFeePerGas: { slow, medium, fast, ape } }
//
// Ratios inter-speeds pour le baseFee : CONFIRMED par reverse-engineering
//   slow×1.0, medium×1.021723, fast×1.065169, ape×1.195507
//
// Calcul de "slow" (baseFee de référence) : approximation par la moyenne des N derniers blocs
// → Le vrai algorithme (gasOracle.js) est privé ; il utilise probablement un EMA ou max récent
//
// Calcul de tip_unit : approximation par la médiane IQR des tips du bloc
// → Le vrai algorithme utilise une formule 0.05×avgGasPrice (cf commentaire gasPrice.ts)
//   mais avec un avgGasPrice calculé différemment

export async function getRelayerAlgoRecommendations(provider, blockTag = 'latest') {
  const block = await fetchBlock(provider, blockTag)
  if (!block) throw new Error(`Block not found: ${blockTag}`)
  if (!block.baseFeePerGas || block.baseFeePerGas === 0n)
    throw new Error('Non-EIP-1559 block')

  // ── slow (baseFee) : moyenne des N derniers blocs via eth_feeHistory ──────────
  // Plus proche du relayer qu'un seul bloc car le relayer lisse probablement les valeurs
  const feeHistory = await provider.send('eth_feeHistory', [
    '0x' + BASEFEE_HISTORY_BLOCKS.toString(16),
    typeof blockTag === 'number' ? '0x' + blockTag.toString(16) : blockTag,
    [50]
  ])

  const baseFees = feeHistory.baseFeePerGas.map(x => BigInt(x))
  // Inclure le prochain baseFee prédit (dernier élément de l'array)
  const allBaseFees = baseFees
  const slowBaseFee = allBaseFees.reduce((a, b) => a + b, 0n) / BigInt(allBaseFees.length)

  // ── tip_unit : médiane IQR des tips du bloc ────────────────────────────────────
  // Les tips bruts du bloc
  const rawTips = block.prefetchedTransactions
    .map(tx => tx.maxPriorityFeePerGas)
    .filter(t => t !== null && t > 0n)

  // eth_feeHistory reward p50 sur les 10 derniers blocs → plus stable
  const rewardP50 = feeHistory.reward.map(r => BigInt(r[0]))
  const tipUnit = average(filterOutliers(rewardP50))

  // Fallback si pas de tips
  const finalTipUnit = tipUnit > 0n ? tipUnit : (rawTips.length > 0n ? average(filterOutliers(rawTips)) : 100000n)

  // ── Application des ratios fixes ──────────────────────────────────────────────
  const gasPrice = {}
  const maxPriorityFeePerGas = {}

  for (const [speed, ratio] of Object.entries(RELAYER_SPEED_RATIOS)) {
    gasPrice[speed] = Number(slowBaseFee * ratio / RATIO_DENOM)
  }

  // Tips : 0 : 1 : 2 : 3 × tip_unit
  maxPriorityFeePerGas.slow   = 0
  maxPriorityFeePerGas.medium = Number(finalTipUnit)
  maxPriorityFeePerGas.fast   = Number(finalTipUnit * 2n)
  maxPriorityFeePerGas.ape    = Number(finalTipUnit * 3n)

  return {
    gasPrice: {
      ...gasPrice,
      maxPriorityFeePerGas,
      updated: Date.now()
    },
    block,
    _debug: {
      slowBaseFee: Number(slowBaseFee),
      tipUnit: Number(finalTipUnit),
      baseFeeHistoryBlocks: BASEFEE_HISTORY_BLOCKS
    }
  }
}

// Multiplicateurs client-side (#addExtra dans signAccountOp.ts)
const SPEED_SURPLUS_DIVISOR = { slow: 20n, medium: 14n, fast: 10n, ape: 5n }

export function applyClientSurplus(speedData) {
  const result = {}
  for (const [speed, divisor] of Object.entries(SPEED_SURPLUS_DIVISOR)) {
    const base = BigInt(speedData.gasPrice[speed])
    const tip = BigInt(speedData.gasPrice.maxPriorityFeePerGas[speed])
    result[speed] = {
      maxFeePerGas: Number(base + base / divisor),
      maxPriorityFeePerGas: Number(tip + tip / divisor)
    }
  }
  return result
}
