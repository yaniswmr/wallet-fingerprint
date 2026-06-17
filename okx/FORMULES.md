# OKX — Reverse-engineering des formules de gas (priority fee)

Source : `gas_fees_collected.db` (~26 700 polls de l'endpoint
`/api/v6/dex/pre-transaction/gas-price`, chainIndex=1, ~1 poll / 12 s),
recoupé avec `eth_feeHistory` du nœud Ethereum (Functori reth).

## ⭐ FORMULE COMPLÈTE (confirmée via `priapi/v2/wallet/tx/signInfo`)

La réponse `signInfo` de l'extension expose les valeurs **brutes** (`ori*`) ET
**affichées** → la formule exacte (vérifiée au wei près sur les 3 tiers) :

```
ori_tier  = baseFee + priority_tier                 # oracle brut (mult base = 1.0)

slow   (min)    = floor(baseFee × 1.125) + safePriorityFee
normal (défaut) = floor(baseFee × 1.35 ) + proposePriorityFee
fast   (max)    = floor(baseFee × 1.70 ) + fastPriorityFee

suggestBaseFee  = floor(baseFee × 1.125)            # = base du tier slow
```

**Le multiplicateur de base dépend du tier : `k = {slow 1.125, normal 1.35, fast 1.70}`.**
C'est appliqué **côté extension** (client-side), PAS par l'API `gas-price` publique
(où `suggestBaseFee == baseFee`, mult 1.0). D'où l'écart constaté entre l'API et l'on-chain.

Le `priority_tier` (safe/propose/fast) vient des percentiles du reward d'un bloc récent
(voir plus bas : p65/p80/p90, caps `fast ≤ 5×propose`, `safe ≤ propose`).

→ le pattern dominant `maxFee = baseFee × 1.35 + 0.5 Gwei` observé dans `gas.db` =
**tier average/default** (k=1.35, +proposePriorityFee ≈ 0.5 Gwei à ce moment-là).

Décodeur/validateur : `decode_signinfo.py` (prend une réponse signInfo, vérifie les k).
Capture de référence : Sepolia (chainId 11155111) — multiplicateurs client-side donc
identiques sur mainnet (à reconfirmer avec 1 capture mainnet si besoin).

## Modèle retenu

```
baseFee_OKX   = suggestBaseFee = baseFee            # AUCUNE inflation de base (mult = 1.0)

tip_safe     = p65  des reward d'un bloc récent
tip_propose  = p80  des reward d'un bloc récent
tip_fast     = p90  des reward d'un bloc récent

safePriorityFee     = min( tip_safe , proposePriorityFee )     # plafonné à propose
proposePriorityFee  = tip_propose                               # l'ANCRE
fastPriorityFee     = min( tip_fast , 5 × proposePriorityFee )  # plafonné à 5×propose

maxFeePerGas_tier   = suggestBaseFee + priorityFee_tier = baseFee + priorityFee_tier
```

Identités legacy vérifiées dans la réponse : `min = base+safe`, `normal = base+propose`,
`max = base+fast`.

## Faits CERTITUDE (exacts sur tout le jeu de données)

| Fait | Mesure |
|------|--------|
| `suggestBaseFee == baseFee` | 26684 / 26684 lignes (100 %) → **base_mult = 1.0** |
| `fast = 5 × propose` (cap atteint) | ~47 % des lignes, ratio max **exactement 5.0** |
| `safe ≤ propose` (cap) | ratio safe/propose max **exactement 1.0**, moyenne 0.41 |
| Classement des tiers | toujours `safe < propose < fast` |
| Fenêtre | **1 seul bloc** (pas de moyenne multi-blocs) |

Ces deux **caps** (`fast = 5×propose`, `safe ≤ propose`) sont la signature OKX la
plus fiable : ils sont exacts et indépendants du timing.

## Percentiles (estimation)

À **offset de bloc libre par ligne** (pour neutraliser le timing) et percentile fixe,
le meilleur percentile par tier :

| Tier | Percentile | err. médiane | match < 10 % |
|------|-----------|-------------|-------------|
| safe    | **p65** (60–70) | ~19 % | ~32 % |
| propose | **p80** (75–80) | ~18 % | ~38 % |
| fast    | **p90** (85–95) | ~14 % | ~42 % |

## Limite irréductible : pas de numéro de bloc

L'API gas-price d'OKX **ne renvoie pas le bloc de référence**. Le `block_number`
enregistré est le head du nœud Functori lu *avant* l'appel OKX → décalage variable.

Preuves :
- Pour un même `block_number` enregistré, OKX renvoie parfois des valeurs
  différentes selon le poll (12 s d'écart) → OKX suit sa propre horloge/head.
- En cherchant le meilleur (offset, percentile) **par ligne**, l'erreur tombe à
  **0.5–3 %** : chaque valeur OKX EST bien le percentile d'un seul bloc récent…
- …mais l'**offset gagnant est réparti uniformément sur ±4 blocs** (aucun offset
  dominant). On ne peut donc pas fixer le bloc exact ni, par voie de conséquence,
  le percentile au point près — d'où le résidu ~15–20 % à percentile fixe.

**Pour aller plus loin** : il faudrait que `collect_gas_fees.py` enregistre, à
chaque poll, le percentile p65/p80/p90 du dernier bloc *au moment exact de la
réponse OKX* (et idéalement plusieurs blocs récents), afin d'aligner sans deviner.

## Scripts

- `reverse_priority_fee.py` — version initiale (grille percentile/fenêtre, offset fixe).
- `reverse_v2.py` — récup feeHistory 1×/bloc + tranchage mémoire ; `--bestoffset`
  révèle le percentile sous le bruit d'alignement (→ fenêtre = 1 bloc).
- `reverse_final.py` — histogramme des offsets/percentiles gagnants par ligne.
- `validate_model.py` — valide le modèle p65/p80/p90 + caps.