# Ambire Relayer — Reverse Engineering du calcul de gas

> Endpoint : `https://relayer.ambire.com/gasPrice/ethereum`  
> Données collectées : 463 observations, blocs 25 279 826 – 25 280 294  
> Source client : `AmbireTech/ambire-common` (public), `AmbireTech/relayer` (privé)

---

## Format de réponse du relayer

```json
{
  "success": true,
  "data": {
    "gasPrice": {
      "slow":   "<base_slow en wei>",
      "medium": "<base_slow × 1.021723>",
      "fast":   "<base_slow × 1.065169>",
      "ape":    "<base_slow × 1.195507>",
      "maxPriorityFeePerGas": {
        "slow":   0,
        "medium": "<tipUnit>",
        "fast":   "<tipUnit × 2>",
        "ape":    "<tipUnit × 3>"
      },
      "updated": "<timestamp ms>"
    }
  }
}
```

---

## Formules confirmées

### 1. Ratios inter-speeds (baseFee)

Hardcodés côté relayer, **constants et immuables** :

| Speed  | Multiplicateur de `base_slow` | Valeur exacte |
|--------|-------------------------------|---------------|
| slow   | × 1.000000                    | référence     |
| medium | × 1.021723                    | confirmé sur 463 obs. (σ < 0.0001) |
| fast   | × 1.065169                    | confirmé      |
| ape    | × 1.195507                    | confirmé      |

**Calcul** : `base_X = base_slow × ratio_X`

### 2. Tips (maxPriorityFeePerGas)

Structure linéaire **0 : 1 : 2 : 3** confirmée sur toutes les observations :

```
slow   = 0
medium = tipUnit          (= prio_medium)
fast   = tipUnit × 2
ape    = tipUnit × 3
```

`tipUnit` est la seule inconnue (voir section Inconnues).

### 3. Surplus client-side (signAccountOp.ts)

Appliqué **après** réception de la réponse relayer, côté wallet :

```typescript
// #addExtra(value, percentageIncrease) → value + value / (100 / percentageIncrease)
slow:   percentageIncrease = 5n  → value + value/20  → +5%
medium: percentageIncrease = 7n  → value + value/14  → +7.14%
fast:   percentageIncrease = 10n → value + value/10  → +10%
ape:    percentageIncrease = 20n → value + value/5   → +20%
```

Source : `ambire-common/src/controllers/signAccountOp/signAccountOp.ts`, méthode `#getIncreasedPrices()` (l. 2062–2086).

Speed par défaut : **Fast**.

---

## Reverse engineering de `base_slow`

### Méthode empirique (reverse_base.py)

Testé sur 50 computations uniques du relayer, 500+ combinaisons (fenêtre, statistique, multiplicateur) :

| Formule                              | MAPE  | Within 2% | Within 5% | Max err |
|--------------------------------------|-------|-----------|-----------|---------|
| `median(3 derniers baseFees) × 1.15` | 1.65% | 74%       | 92%       | 8%      |
| `median(5 derniers baseFees) × 1.15` | ~2%   | 70%       | 90%       | 10%     |
| `avg(3 derniers baseFees) × 1.15`    | 1.72% | 72%       | 91%       | 9%      |
| `baseFee[N] × 1.15`                  | 5.1%  | —         | —         | 28%     |
| `baseFee[N-1] × 1.15`                | 4.5%  | —         | —         | 32%     |

**Meilleure approximation** :

```
base_slow ≈ median(baseFees des 3 derniers blocs) × 1.15
```

### Méthode ratio direct (reverse_ratio.py)

Rapport `base_slow / baseFee` mesuré sur 60 observations :

| Source      | Mean    | Median  | Std     | CV%   |
|-------------|---------|---------|---------|-------|
| baseFee[N]  | 1.14673 | 1.14755 | 0.05885 | 5.13% |
| baseFee[N-1]| 1.14243 | 1.14294 | 0.05139 | 4.50% |

Le multiplicateur **× 1.14–1.15** est robuste quel que soit le bloc de référence.  
Le CV% de ~4–5% provient du fait que le relayer lisse sur une fenêtre (non un seul bloc).

### Implémentation dans gasPrice.js

```javascript
const BASEFEE_HISTORY_BLOCKS = 3

const feeHistory = await provider.send('eth_feeHistory', [
  '0x3', blockTag, [50]
])
const baseFees   = feeHistory.baseFeePerGas.map(x => BigInt(x))
const histFees   = baseFees.slice(0, -1)  // exclut le next-prédit
const sorted     = [...histFees].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
const median     = sorted[Math.floor(sorted.length / 2)]
const base_slow  = (median * 115n) / 100n  // × 1.15
```

---

## Inconnues

### `tipUnit`

- Formule inconnue — code dans `gasOracle.js` (repo privé `AmbireTech/relayer`)
- Testé et **échec** : `eth_feeHistory` percentiles (cv ≈ 47%), `avgGasPrice × factor` (cv ≈ 49–67%)
- Hypothèse : le relayer utilise les données **mempool** (non accessibles via RPC public)
- Valeurs observées fréquentes : 333M, 667M, 700M, 750M, 1000M wei — présence de planchers
- La seule piste ouverte : contacter Ambire directement

---

## Schéma de construction de la transaction EIP-1559

```
Relayer: base_slow = median(3 derniers blocs) × 1.15
         base_X    = base_slow × ratio_X
         prio_X    = tipUnit × {0, 1, 2, 3}

Wallet (signAccountOp.ts):
         maxFeePerGas         = base_X + base_X / (100 / percentageIncrease_X)
         maxPriorityFeePerGas = prio_X + prio_X / (100 / percentageIncrease_X)

Transaction EIP-1559 finale:
         maxFeePerGas         ← champ ci-dessus (speed = Fast par défaut)
         maxPriorityFeePerGas ← champ ci-dessus
```

### Particularité importante : maxFeePerGas ≠ baseFee + tip

La quasi-totalité des wallets construisent `maxFeePerGas` ainsi :

```
maxFeePerGas = baseFee × 2 + maxPriorityFeePerGas   ← MetaMask, Trust Wallet…
```

Ambire fait différemment : `maxFeePerGas` et `maxPriorityFeePerGas` sont calculés **indépendamment**. Le relayer retourne un champ `gasPrice` (prix total à la manière pré-EIP-1559) qui est mappé directement sur `maxFeePerGas` sans y additionner le tip :

```
maxFeePerGas         = base_fast × 1.10    (dérivé du relayer, indépendant du tip)
maxPriorityFeePerGas = tipUnit × 2 × 1.10  (calculé séparément)
```

Conséquence : `(maxFeePerGas - maxPriorityFeePerGas) / baseFee_parent ≈ 1.35` chez Ambire, alors que chez MetaMask cette valeur vaut ~2.0 par construction.

---

## Fingerprint on-chain

Pour identifier une transaction Ambire de type 2 (EIP-1559), on calcule :

```
fee_factor = maxFeePerGas / baseFee_parent
```

où `baseFee_parent` est le `baseFeePerGas` du bloc parent (bloc N−1 si la tx est dans le bloc N).

### Valeurs attendues par speed

| Speed  | fee_factor attendu | Calcul détaillé                          |
|--------|--------------------|------------------------------------------|
| Slow   | ≈ **1.21**         | 1.15 × 1.000000 × 1.05                  |
| Medium | ≈ **1.25**         | 1.15 × 1.021723 × 1.0714                |
| **Fast** (défaut) | ≈ **1.35** | 1.15 × 1.065169 × 1.10       |
| Ape    | ≈ **1.65**         | 1.15 × 1.195507 × 1.20                  |

La grande majorité des transactions Ambire sera à **≈ 1.35** (Fast par défaut).

### Autres marqueurs

- `(maxFeePerGas - maxPriorityFeePerGas) / baseFee_parent ≈ 1.35` (≠ ~2.0 pour MetaMask)
- Ratios inter-speeds sur le baseFee stritement constants : `1 : 1.021723 : 1.065169 : 1.195507`
- Structure des tips strictement linéaire : `slow=0, medium=T, fast=2T, ape=3T`
- Ces trois patterns combinés sont **uniques** parmi les wallets connus
