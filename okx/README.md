# OKX Wallet — gas fees : formules, endpoint privé, outils

## Ce qu'on a établi

### 1. Formule de gas OKX (vérifiée au wei, mainnet ET testnet)
L'extension applique des multiplicateurs **côté client** par-dessus l'oracle :
```
slow   (min)    = floor(baseFee × 1.125) + safePriorityFee
normal (défaut) = floor(baseFee × 1.35 ) + proposePriorityFee
fast   (max)    = floor(baseFee × 1.70 ) + fastPriorityFee
suggestBaseFee  = floor(baseFee × 1.125)
```
Les priority fees (safe/propose/fast) viennent de percentiles ~p65/p80/p90 du reward d'un bloc récent
(caps `fast ≤ 5×propose`, `safe ≤ propose`). Détails : `FORMULES.md`.

### 2. Les 3 surfaces de gas OKX
- `web3.okx.com/api/v6/dex/pre-transaction/gas-price` — chemin DEX (clés OK-ACCESS du `.env`).
- `web3.okx.com/api/v5/wallet/pre-transaction/gas-price` — chemin wallet public (mêmes clés).
- `wallet.okex.org|okx.ac|web3.okx.com /priapi/v2/wallet/tx/signInfo` — **endpoint privé réel de l'extension**.
  Body `{coinId, fromAddr, chainId}`. **ETH mainnet = chainId 1, coinId 3** (Sepolia ETH = coinId 21100).

### 3. Auth de signInfo entièrement reversée (cf. SIGNING_REVERSE.md)
Tout est généré localement, aucun secret stocké, aucun handshake :
- `user-device-sign` = AES-128-ECB(clé en dur `H6379FIktyVeUAje`, `UA^SHA1(UA)^0^ts^plat^lang`)
- `ok-verify-token`  = `randomUUID()` jetable
- `ok-timestamp`     = `Date.now()` ms
- `ok-verify-sign`   = HMAC-SHA256(clé dérivée du token+ts, `path+body`)
- devid / fingerprint-id / tmx-session-id / x-id-group = réutilisés de la capture DevTools (stables/install)

## Outils

| Script | Rôle | Lancer |
|---|---|---|
| `collect_signinfo_db.py` | **Collecteur principal** : signInfo mainnet en boucle → SQLite `signinfo_live.db` AVEC `block_number` (pour reverser les priority fees ensuite) | `python collect_signinfo_db.py --interval 12` |
| `call_signinfo.js` | **Appel réel** de signInfo (1 coup, Node) | `node call_signinfo.js --chain 1 --coin 3` |
| `collect_signinfo.js` | Variante Node (log JSONL, pas de block_number) — *déprécié au profit du collecteur DB* | `node collect_signinfo.js` |
| `signinfo_stats.py` | Résume le log JSONL (variante Node) | `python signinfo_stats.py` |
| `decode_signinfo.py` | Valide/extrait les mults d'une réponse signInfo | `python decode_signinfo.py reponse.json` |
| `gas_suggest.py` | API gas-price publique v5/v6 (+ reconstruction signInfo) | `python gas_suggest.py --signinfo --chain 1` |
| `collect_signinfo_recon.py` | Poll API publique + reconstruction (sans auth privée) | `python collect_signinfo_recon.py` |
| `collect_gas_fees.py` | Collecte historique DEX v6 → `gas_fees_collected.db` | `python collect_gas_fees.py` |

## Prérequis
- `okx/.env` : `OKX_API_KEY/SECRET_KEY/PASSPHRASE/PROJECT_ID` (pour les `*.py` qui tapent l'API publique).
- Node ≥ 18 (les `*.js` n'ont aucune dépendance : `crypto`/`fetch` natifs).
- Les `call/collect_signinfo.js` réutilisent les IDs device de la capture (en tête de fichier) ; si invalidés, recapturer.
