# MetaMask — formules gas (reverse-engineered)

API source : `GET https://gas.api.cx.metamask.io/networks/1/suggestedGasFees`
Valeurs en **Gwei**. Agrégation = **médiane** sur les **5 derniers blocs** d'`eth_feeHistory`.

## Requête eth_feeHistory

```json
{
  "jsonrpc": "2.0", "id": 1, "method": "eth_feeHistory",
  "params": ["0x64", "latest", [10, 50, 80, 99]]
}
```
- `"0x64"` = **100 blocs** demandés (pour les ranges historiques)
- `[10, 50, 80, 99]` = percentiles → low (p10) / medium (p50) / high (p80) / range max (p99)
- seuls les **5 derniers blocs** servent à la médiane des priority fees ; les 100 servent aux ranges historiques

## Base fee

```
estimatedBaseFee = base fee du dernier bloc miné (head)
```

## Priority fee (suggestedMaxPriorityFeePerGas)

```
low    = median(p10)                      # "dust", pas de plancher
medium = max(2.0, median(p50))
high   = max(2.0, median(p80))
```
- percentiles `eth_feeHistory` : **10 / 50 / 80**
- planchers : low ≈ 0 · medium = 2.0 · high = 2.0 Gwei
- `low` == `latestPriorityFeeRange[min]` (85 % des cas)

## Max fee (suggestedMaxFeePerGas)

```
maxFee = estimatedBaseFee × mult + priority

mult :  low = 1.25   medium = 1.43   high = 1.43
```

## Récap

| tier   | base mult | percentile | plancher |
|--------|-----------|------------|----------|
| low    | 1.25      | p10        | —        |
| medium | 1.43      | p50        | 2.0 Gwei |
| high   | 1.43      | p80        | 2.0 Gwei |

---

# gas_server_copy.py

Serveur Flask qui **reproduit exactement** le serveur gas de MetaMask à partir des
formules ci-dessus (via `eth_feeHistory`). Appelé au même instant que l'API
MetaMask réelle, il renvoie les **mêmes** `estimatedBaseFee`, `suggestedMaxFeePerGas`
et `suggestedMaxPriorityFeePerGas` pour low / medium / high.

```bash
python gas_server_copy.py --port 8000          # ETH_RPC_URL lu depuis ../.env
python gas_server_copy.py --port 8000 --rpc <URL>

curl http://localhost:8000/networks/1/suggestedGasFees
```

Champs **exacts** : `estimatedBaseFee`, les 3 `suggestedMaxFeePerGas`, les 3
`suggestedMaxPriorityFeePerGas`.
Champs **approximés** (propriétaires, non reproductibles) : `networkCongestion`,
`latestPriorityFeeRange`, `historicalPriorityFeeRange`, `historicalBaseFeeRange`,
`priorityFeeTrend`, `baseFeeTrend`, `*WaitTimeEstimate`.

## Autres scripts

| script | rôle |
|--------|------|
| `collect_gas_fees.py`     | collecte l'API MetaMask → `gas_fees_collected.db` |
| `reverse_priority_fee.py` | retrouve les percentiles (grid-search `eth_feeHistory`) |
| `watch_multipliers.py`    | suit en direct les multiplicateurs base-fee |