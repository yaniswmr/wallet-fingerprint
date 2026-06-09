# Gas Fingerprint Explorer

Ce projet permet d'afficher et de filtrer des informations sur des transactions Ethereum associees a plusieurs wallets :

- MetaMask
- Ledger
- Trust Wallet
- OKX Wallet
- Ambire Wallet

La page web permet de parcourir les transactions, de filtrer par wallet ou par tier Ledger, de filtrer par type de transaction, de rechercher par hash ou adresse, et de comparer plusieurs champs lies au gas : `max_fee_gwei`, `max_priority_gwei`, `base_fee_gwei`, `fee_factor`, `gas_limit`, `estimated_gas` et `gas_limit_factor`.

## Donnees

Les donnees affichees ne doivent pas etre considerees comme certaines. Elles servent a l'analyse et a l'exploration de patterns de gas, mais elles peuvent contenir des erreurs, des approximations ou des transactions mal classees.

Les transactions Ledger sont obtenues selon les formules de gas pattern utilisees dans le projet. Elles sont classees par tiers (`slow`, `medium`, `fast`) a partir des valeurs calculees.

Pour MetaMask, Trust Wallet et OKX Wallet, les transactions sont identifiees en utilisant les contrats delegators lies a l'EIP-7702. Cette methode permet de retrouver des transactions associees a ces wallets, mais elle ne garantit pas une attribution parfaite.

## Interface web

L'application web se trouve dans le dossier `frontapp/`.

Pour la lancer :

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r frontapp/requirements.txt
python3 frontapp/app.py
```

Puis ouvrir :

```text
http://127.0.0.1:5050
```

## Bases de donnees

L'interface utilise deux bases SQLite presentes a la racine du projet :

- `gas.db` : transactions associees a MetaMask, Trust Wallet et OKX Wallet.
- `ledger.db` : transactions Ledger reconstruites a partir des formules de gas pattern.

