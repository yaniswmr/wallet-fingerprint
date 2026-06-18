/**
 * Calculateur hors-ligne — applique les formules Trust Wallet à une réponse
 * eth_feeHistory COLLÉE (celle réellement envoyée par l'app), pour comparer
 * tier par tier avec ce que l'app affiche.
 *
 * Formules (cf. FORMULES.md / bundle décompilé) :
 *   tip(tier)        = Σ reward[bloc][idx] / 20      (diviseur FIXE 20, division ENTIÈRE)
 *                      idx 0=p10→low, 1=p60→medium, 2=p99→high
 *   maxFeePerGas     = baseFee × facteur + tip       (low ×0.8, medium ×1.0, high ×1.3)
 *   coût (ETH)       = maxFeePerGas × gasLimit / 1e18
 *
 * Usage :
 *   node calc.js <feehistory.json> [--basefee=<valeur>] [--gaslimit=21000]
 *
 *   --basefee : baseFee de Trust. Accepte wei ("315281344") ou gwei ("0.315gwei" / "12.3gwei").
 *               Sans ça : seuls les tips sont calculés (pas de maxFee ni coût).
 *   --gaslimit: défaut 21000.
 *
 * Le JSON peut être la réponse RPC complète {jsonrpc,result:{...}} ou juste l'objet result.
 */

import { readFileSync } from 'node:fs'

// maxFee_tier = baseFee(latest) × 1.20 × facteur_tier + tip  → multiplicateur effectif = 1.20 × facteur
// facteurs Ethereum : low 0.91, medium 1.0, high 1.3
const FACTORS = { low: { num: 1092n, den: 1000n }, medium: { num: 1200n, den: 1000n }, high: { num: 1560n, den: 1000n } }
const TIER_IDX = { low: 0, medium: 1, high: 2 }
const DIVISOR = 20n
const WEI_PER_ETH = 1_000_000_000_000_000_000n

// ── args ────────────────────────────────────────────────────────────────────
const args = process.argv.slice(2)
const file = args.find((a) => !a.startsWith('--'))
const getOpt = (name) => {
  const a = args.find((x) => x.startsWith(`--${name}=`))
  return a ? a.split('=')[1] : null
}
if (!file) {
  console.error('Usage: node calc.js <feehistory.json> [--basefee=<wei|N gwei>] [--gaslimit=21000]')
  process.exit(1)
}

function toBig(hexOrDec) {
  const s = String(hexOrDec).trim()
  return s.startsWith('0x') ? BigInt(s) : BigInt(s)
}

// baseFee : accepte wei brut, ou "...gwei"
function parseBaseFee(raw) {
  if (raw == null) return null
  const s = String(raw).trim().toLowerCase()
  if (s.endsWith('gwei')) {
    const g = parseFloat(s.replace('gwei', '').trim())
    return BigInt(Math.round(g * 1e9))
  }
  return toBig(s)
}

const toGwei = (wei) => (Number(wei) / 1e9).toFixed(6)
function weiToEth(wei) {
  const i = wei / WEI_PER_ETH
  const f = (wei % WEI_PER_ETH).toString().padStart(18, '0').replace(/0+$/, '')
  return f ? `${i}.${f}` : `${i}`
}

// ── parse feeHistory ──────────────────────────────────────────────────────────
const parsed = JSON.parse(readFileSync(file, 'utf8'))
const result = parsed.result ?? parsed
if (!result.reward) {
  console.error('Pas de champ "reward" dans le JSON. Attendu : réponse eth_feeHistory.')
  process.exit(1)
}
const rewards = result.reward.map((r) => r.map((x) => toBig(x)))
const oldest = result.oldestBlock ? parseInt(result.oldestBlock, 16) : null

// ── calcul ──────────────────────────────────────────────────────────────────
const baseFee = parseBaseFee(getOpt('basefee'))
const gasLimit = BigInt(getOpt('gaslimit') ?? '21000')

function tip(idx) {
  return rewards.reduce((acc, r) => acc + (r[idx] ?? 0n), 0n) / DIVISOR
}

// ── ESTIMATEUR SINGLE : feeHistory(10, …, [5]) → une seule suggestion ───────────
//   priorityFee  = max( médiane(reward triés) , 1 Gwei )
//   maxFeePerGas = baseFee × 1.20 + priorityFee
const ONE_GWEI = 1_000_000_000n
function runSingle() {
  const vals = rewards.map((r) => r[0] ?? 0n).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
  const median = vals[Math.floor(vals.length / 2)] ?? 0n
  const priorityFee = median > ONE_GWEI ? median : ONE_GWEI

  console.log('\n' + '─'.repeat(78))
  console.log(`  ESTIMATEUR SINGLE — reward rows = ${rewards.length}` + (oldest != null ? `   window ${oldest}..${oldest + rewards.length - 1}` : ''))
  console.log(`  reward triés (Gwei) : [${vals.map((v) => toGwei(v)).join(', ')}]`)
  console.log(`  médiane = ${toGwei(median)} Gwei   → plancher 1 Gwei appliqué : ${median > ONE_GWEI ? 'non' : 'OUI'}`)
  console.log(`  priorityFee (maxPriorityFeePerGas) = ${toGwei(priorityFee)} Gwei`)
  // baseFee : --basefee sinon dernier baseFeePerGas du feeHistory (= bloc N+1 projeté)
  let base = baseFee
  let baseSrc = '--basefee'
  if (base == null && result.baseFeePerGas?.length) {
    base = toBig(result.baseFeePerGas[result.baseFeePerGas.length - 1])
    baseSrc = 'feeHistory.baseFeePerGas[dernier] (bloc N+1 projeté)'
  }
  if (base != null) {
    const maxFee = (base * 120n) / 100n + priorityFee
    const cost = maxFee * gasLimit
    console.log(`  baseFee = ${toGwei(base)} Gwei  [${baseSrc}]   gasLimit = ${gasLimit}`)
    console.log('─'.repeat(78))
    console.log(`  maxFeePerGas         = base×1.20 + prio = ${toGwei(maxFee)} Gwei`)
    console.log(`  maxPriorityFeePerGas = ${toGwei(priorityFee)} Gwei`)
    console.log(`  coût trx             = ${weiToEth(cost)} ETH`)
  } else {
    console.log(`  (pas de baseFee dispo → maxFee/coût non calculés)`)
  }
  console.log('─'.repeat(78) + '\n')
}

if (args.includes('--single')) { runSingle(); process.exit(0) }

console.log('\n' + '─'.repeat(78))
console.log(`  reward rows = ${rewards.length}` + (oldest != null ? `   window ${oldest}..${oldest + rewards.length - 1}` : ''))
if (rewards.length !== 20) console.log(`  ⚠️  ${rewards.length} lignes ≠ 20 : Trust divise quand même par 20 → tips plus bas`)
if (baseFee != null) console.log(`  baseFee = ${toGwei(baseFee)} Gwei (${baseFee} wei)   gasLimit = ${gasLimit}`)
else console.log(`  (pas de --basefee → tips seuls)`)
console.log('─'.repeat(78))

const head = baseFee != null
  ? `  ${'tier'.padEnd(8)} ${'tip (Gwei)'.padStart(14)} ${'maxFee (Gwei)'.padStart(16)} ${'coût (ETH)'.padStart(22)}`
  : `  ${'tier'.padEnd(8)} ${'tip (Gwei)'.padStart(14)} ${'tip (wei)'.padStart(20)}`
console.log(head)
console.log('─'.repeat(78))

for (const tier of ['low', 'medium', 'high']) {
  const t = tip(TIER_IDX[tier])
  if (baseFee != null) {
    const f = FACTORS[tier]
    const maxFee = (baseFee * f.num) / f.den + t
    const cost = maxFee * gasLimit
    console.log(`  ${tier.padEnd(8)} ${toGwei(t).padStart(14)} ${toGwei(maxFee).padStart(16)} ${weiToEth(cost).padStart(22)}`)
  } else {
    console.log(`  ${tier.padEnd(8)} ${toGwei(t).padStart(14)} ${t.toString().padStart(20)}`)
  }
}
console.log('─'.repeat(78) + '\n')
