# Pimlico (bundler Alto) — Reverse Engineering du calcul de gas

> Endpoint : `POST https://api.pimlico.io/v2/<chainId>/rpc?apikey=<KEY>`
> Méthode : `pimlico_getUserOperationGasPrice` (params `[]`)
> Code source : **open-source** — `github.com/pimlicolabs/alto`
> Collecte : `collect_gas_fees_v2.py` → `gas_fees_collected_v2.db`
> Statut : **FORMULE COMPLÈTE CRACKÉE & VÉRIFIÉE AU WEI (2026-06-22)**

> ⚠️ À ne pas confondre avec le relayer Ambire (`relayer.ambire.com`, voir `infos.md`).
> C'est l'endpoint *corrigé* : les vraies suggestions de gas mainnet viennent d'ici.

---

## TL;DR — la formule complète

```
# Entrées (depuis le nœud de Pimlico) :
maxPrio_raw = eth_maxPriorityFeePerGas()          # oracle du nœud (reth/geth)
baseFee     = block("latest").baseFeePerGas

# 1) Construction du prix de base (viem estimateFeesPerGas) :
maxFee_raw   = floor(baseFee × 1.20) + maxPrio_raw

# 2) bumpTheGasPrice  (config.gasPriceBump = 115) :
maxPrio_bump = floor(maxPrio_raw × 1.15)
maxFee_bump  = floor(maxFee_raw  × 1.15)

# 3) Trois tiers  (config.gasPriceMultipliers = 100 / 105 / 110) :
#    scale(v, m) = floor(v × m / 100)
slow     = { maxPriorityFeePerGas: scale(maxPrio_bump, 100), maxFeePerGas: scale(maxFee_bump, 100) }
standard = { maxPriorityFeePerGas: scale(maxPrio_bump, 105), maxFeePerGas: scale(maxFee_bump, 105) }
fast     = { maxPriorityFeePerGas: scale(maxPrio_bump, 110), maxFeePerGas: scale(maxFee_bump, 110) }
```

**Toute l'arithmétique est entière (floor)** — `scaleBigIntByPercent(v, p) = v * p / 100n`.

---

## 1. Origine de chaque constante (dans le code Alto)

| Constante | Valeur (instance hébergée) | Où, dans le code |
|-----------|----------------------------|------------------|
| Multiplicateur baseFee | **× 1.20** | `estimateDynamicGasPrice`/viem : `scaleBigIntByPercent(latestBaseFee, 120)` |
| `gasPriceBump`         | **× 1.15** | `bumpTheGasPrice()` ← `config.gasPriceBump` (défaut OSS = 100) |
| Multiplicateurs tiers  | **100 / 105 / 110** | `pimlico_getUserOperationGasPrice.ts` ← `config.gasPriceMultipliers` (défaut OSS = `100,100,100` → instance hébergée = `100,105,110`) |
| `scaleBigIntByPercent` | `v × p // 100` (floor) | `src/utils/bigInt.ts` |

Fichiers :
- `src/rpc/methods/pimlico_getUserOperationGasPrice.ts` — applique les 3 multiplicateurs de tiers.
- `src/handlers/gasPriceManager.ts` — `getGasPrice()` → `estimateGasPrice()` (viem) → `bumpTheGasPrice()`.
- `src/utils/bigInt.ts` — `scaleBigIntByPercent`.

---

## 2. Les tiers : slow / standard / fast

Les 3 niveaux sont **le même prix de base** (`maxPrio_bump`, `maxFee_bump`) multiplié par un pourcentage :

```
slow     = base × 100 / 100   (= base, inchangé)
standard = base × 105 / 100   (= +5 %)
fast     = base × 110 / 100   (= +10 %)
```

Conséquences directes, vérifiées **au wei, 0 erreur**, sur 2 datasets :

```
standard.maxPriorityFeePerGas = floor(slow.maxPriorityFeePerGas × 105 / 100)
fast.maxPriorityFeePerGas     = floor(slow.maxPriorityFeePerGas × 110 / 100)
standard.maxFeePerGas         = floor(slow.maxFeePerGas × 105 / 100)
fast.maxFeePerGas             = floor(slow.maxFeePerGas × 110 / 100)
```

Ratios constants : `standard/slow = 1.05`, `fast/slow = 1.10` (sur maxFee ET maxPrio).
**Universel** : identique sur mainnet, Sepolia, Base, Arbitrum, Optimism.

---

## 3. Le `maxFeePerGas`

Avant les tiers, le prix de base est :

```
maxFee_bump = floor( ( floor(baseFee × 1.20) + maxPrio_raw ) × 1.15 )
```

Donc, par tier :

```
(maxFee_tier − maxPrio_tier) / baseFee  ≈  1.20 × 1.15 × {1.0, 1.05, 1.10}
                                        =  1.38 / 1.449 / 1.518
```

**Vérifié au wei sur 418 lignes de la DB** — médiane EXACTE :

| Tier | `(maxFee − prio)/baseFee` médian | = 1.20 × 1.15 × … |
|------|----------------------------------|-------------------|
| slow     | **1.38000** | × 1.00 |
| standard | **1.44900** | × 1.05 |
| fast     | **1.51800** | × 1.10 |

(La variance min 1.23 / max 1.55 = bruit de timing : le `baseFee` enregistré dans la DB
n'est pas toujours celui que Pimlico a utilisé, à cause de son cache — voir §5.)

---

## 4. Le `maxPriorityFeePerGas` de base (`maxPrio_raw`)

C'est le **seul élément non reproductible offline** :

```
maxPrio_raw = eth_maxPriorityFeePerGas()  du nœud de Pimlico
```

- C'est l'oracle natif du client d'exécution (reth/geth), qui calcule ~le **55-60ᵉ percentile**
  des tips des blocs récents. Notre fit empirique antérieur — *« médiane sur 4 blocs du p55
  d'`eth_feeHistory` »* — n'était qu'une **approximation** de cet oracle.
- Le **× 1.15** qu'on mesurait sur `slow` n'était PAS un percentile : c'est `gasPriceBump`.
- On ne peut pas reproduire `maxPrio_raw` exactement sans interroger le nœud de Pimlico
  (chaque nœud voit un mempool légèrement différent). **Mais** comme le multiplicateur du tier
  `slow` est 100, l'API nous le redonne directement :

```
slow.maxPriorityFeePerGas == maxPrio_bump == floor(maxPrio_raw × 1.15)
```

→ on récupère donc `maxPrio_raw` (à ±1 wei près) par inversion du bump, ce qui suffit à
**tout recalculer et valider** (voir `pimlico_gas_replica.py`).

### Chemin NON utilisé en prod : `estimateDynamicGasPrice`

Le code contient un second chemin (`dynamicGasPrice=true`) basé sur `eth_feeHistory` :
percentiles `[40,50,60,70]` choisis selon le remplissage moyen des blocs
(`<0.5→40e`, `0.5-0.7→50e`, `0.7-0.9→60e`, `>0.9→70e`), puis médiane sur N blocs.
**Testé (`verify_alto_algo.py`) : il donne des valeurs ~15× trop basses en mainnet calme**
→ l'instance hébergée a `dynamicGasPrice=false` et utilise donc le chemin viem ci-dessus.

---

## 5. Le cache de Pimlico (piège de validation)

Pimlico met sa réponse en cache et la recalcule sur sa propre horloge (≈ 1×/bloc).
Conséquences observées :
- La même valeur `slow` se répète sur plusieurs polls / plusieurs blocs
  (ex. `0.115 Gwei` vu 29× dans la DB).
- Pour reverser, il faut **collapser en change-points** (1ʳᵉ apparition de chaque valeur) et
  aligner au bloc frais — sinon l'alignement bloc↔valeur est faux.
- Pour valider `maxFee`, tester le `baseFee` des **derniers blocs** (head, head-1…) : Pimlico
  calcule contre l'un d'eux.

---

## 6. Validation live — le réplica

`pimlico_gas_replica.py` reproduit le pipeline entier en arithmétique entière et compare,
champ par champ, au serveur. Résultat : **6/6 champs identiques au wei, 12/12 échantillons.**

```
┌─ Échantillon #1 ──────────────────────────────
│ Inputs : maxPrio_raw = 500000000   baseFee = 690231775 [head]
│ champ                CALCULÉ (réplica)   SERVEUR (API)  ok
│ slow.maxPriorityFee          575000000       575000000  ✓
│ slow.maxFeePerGas           1527519849      1527519849  ✓
│ standard.maxPriorityFee      603750000       603750000  ✓
│ standard.maxFeePerGas       1603895841      1603895841  ✓
│ fast.maxPriorityFee          632500000       632500000  ✓
│ fast.maxFeePerGas           1680271833      1680271833  ✓
└────────────────────────────────────────────────
```

Usage :
```bash
python3 pimlico_gas_replica.py --n 10 --interval 6     # 10 échantillons live
python3 pimlico_gas_replica.py --demo                  # un exemple figé
python3 pimlico_gas_replica.py --pim 'https://api.pimlico.io/v2/11155111/rpc?apikey=...'  # Sepolia
```

---

## 7. Testnet

Le code de `gasPriceManager.ts` est **chain-agnostic** (sauf Polygon / Arbitrum / Optimism /
Mantle / Hedera / Citrea, qui ont des managers dédiés). **Sepolia utilise exactement la même
formule** que le mainnet (vérifié live : ratios de tiers 1.05/1.10 identiques). Seul change
le niveau du `maxPrio_raw` (mempool propre à la chaîne).

---

## 8. Scripts produits

| Script | Rôle |
|--------|------|
| `collect_gas_fees_v2.py` | collecteur Pimlico → `gas_fees_collected_v2.db` (+ baseFee du nœud) |
| `pimlico_gas_replica.py` | **réplica + validateur live** (affichage champ par champ) |
| `reverse_slow_tip.py` | reverse offline depuis la DB (fenêtre/percentile/scale) |
| `capture_slow_live.py` | capture live rapide (collapse en change-points) → `slow_live.jsonl` |
| `analyze_slow_live.py` | analyse des change-points vs `eth_feeHistory` |
| `verify_alto_algo.py` | test du chemin `estimateDynamicGasPrice` (prouve qu'il n'est PAS utilisé) |

---

## 9. Fingerprint Pimlico

- Multiplicateur baseFee effectif sur `maxFee` : **1.38 / 1.449 / 1.518** (= 1.20 × 1.15 × tiers).
- Tiers strictement **×1.00 / ×1.05 / ×1.10** (sur maxFee ET maxPrio), troncature entière.
- `maxFee = floor(1.15 × (floor(baseFee×1.20) + tip))` — le tip n'est PAS additionné « brut »
  comme chez la plupart des wallets : tout est bumpé ×1.15 ensemble.
- À comparer : MM 1.25/1.43/1.43, OKX 1.125/1.35/1.70, Rabby 1.20/1.28/1.32,
  Trust 1.092/1.20/1.56, Ambire-relayer 1.21/1.25/1.35/1.65.
