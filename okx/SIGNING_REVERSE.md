# OKX `signInfo` — reverse de la signature des requêtes ✅ RÉSOLU & TESTÉ LIVE

## 🟢 RÉSULTAT FINAL : l'endpoint est appelable par programme (HTTP 200 confirmé)
- Client autonome : **`okx/call_signinfo.js`** (Node, génère tous les headers, aucune dépendance).
  - `node call_signinfo.js --chain 1 --coin 3`        → **ETH mainnet** (HTTP 200, vraies données gas)
  - `node call_signinfo.js --chain 11155111 --coin 21100` → Sepolia (oracle)
- **coinId ETH mainnet = `3`** (Sepolia ETH = 21100 ; coinId requis dans le body).
- Token jetable : un `crypto.randomUUID()` frais par appel suffit (vérifié live).
- Headers réutilisés de la capture (stables/installation) : devid, fingerprint-id, tmx-session-id, x-id-group.
- **Multiplicateurs 1.125/1.35/1.70 reconfirmés sur la réponse MAINNET** (pas seulement Sepolia) :
  min=floor(base×1.125)+safe, normal=floor(base×1.35)+propose, max=floor(base×1.70)+fast — exacts au wei.
- Bonus mainnet : la réponse contient aussi `estimateTimeList` (gas/temps d'attente) et `isFacet`.

---

# (historique du reverse ci-dessous)

Cible : pouvoir appeler par programme
`POST https://wallet.okex.org/priapi/v2/wallet/tx/signInfo?t=<ms>` (chainId=1 mainnet).

Extension analysée : `~/.config/google-chrome/Default/Extensions/mcohilncbfahbmgdjkbpemcciiolgcge/4.4.0_0/scripts`
(bundles minifiés). Domaines miroirs équivalents : `wallet.okex.org`, `wallet.okx.ac`, `web3.okx.com`.

## Oracle (capture réelle à reproduire)
- ts = `1781702682825`
- ok-verify-token = `2c15dff6-9812-49dd-82b7-c40f6da91beb`
- ok-verify-sign  = `CRD7IoZEJBGkzRA3VSIjkINQYW+yy3eUXKEdXIYvQLg=`  (44 b64 = 32 octets)
- devid = device-token = fingerprint-id = `8f6d9778-9531-403d-a3ba-6a0e6ecf6c10`
- x-id-group = `1781702421441-c-137`
- tmx-session-id = session-id = `2w5n96xp0e5_1781702422349`
- body (91 o) = `{"coinId":21100,"fromAddr":"0x4524fc0edb972fa09c8af5859025c795c81287fc","chainId":11155111}`

## ✅✅ VALIDÉ (match exact contre l'oracle, via Node) : `user-device-sign`
Reproduit au caractère près avec ts=1781702682825 (== ok-timestamp !), platform="Linux x86_64", lang="fr-FR".
Le `Date.now()` interne == `ok-timestamp` → même timestamp pour toutes les signatures de la requête.
Node : `aes-128-ecb`, clé `H6379FIktyVeUAje` (16o), msg = `${ua}^${sha1hex(ua)}^0^${ts}^Linux x86_64^fr-FR`, base64. (sortie 192 o)

## ✅ RÉSOLU : `user-device-sign` (mécanique)
Intercepteur de requêtes `Bp` dans `lib/secureIframe.js` :
```js
e.headers["user-device-sign"] = Zv("H6379FIktyVeUAje",
  `${navigator.userAgent}^${Qv(navigator.userAgent)}^${navigator.webdriver||/headless/.test(ua)?1:0}^${Date.now()}^${navigator.platform}^${navigator.language}`, false)
```
- `Zv(e,t)` = **AES-128-ECB**, clé = `Utf8.parse(e)`, padding Pkcs7, sortie **base64** (CryptoJS `uo`).
- `Qv(e)` = **SHA1(e)** (CryptoJS, hex).
- Clé en dur `H6379FIktyVeUAje` → **aucune clé device stockée nécessaire**, entièrement reproductible.

Repro Python (à valider) :
```python
from Crypto.Cipher import AES; import base64, hashlib
def Zv(key, msg):
    c = AES.new(key.encode(), AES.MODE_ECB)
    pad = 16 - len(msg.encode())%16
    data = msg.encode() + bytes([pad])*pad
    return base64.b64encode(c.encrypt(data)).decode()
ua="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
sha1=hashlib.sha1(ua.encode()).hexdigest()
msg=f"{ua}^{sha1}^0^{1781702682825}^Linux x86_64^fr-FR"
print(Zv("H6379FIktyVeUAje", msg))
```

## ✅ Headers device (depuis storage, réutilisables)
`Bp` lit `j.get("devid")` (storage.local) → devid/device-token. `web-deviceId` idem.
`fingerprint-id`, `tmx-session-id`, `session-id` viennent de `lS()` (à confirmer).
risk-params = `fingerprint-id=${i}&fp-status=${a}&session-id=${o}`.
Ces IDs sont stables par installation → on peut **réutiliser les valeurs de la capture**.

## ✅✅ VALIDÉ (match exact contre l'oracle, via Node) : `ok-verify-sign` / `ok-timestamp` / `ok-verify-token`

Reproduit au caractère près : `CRD7IoZEJBGkzRA3VSIjkINQYW+yy3eUXKEdXIYvQLg=`.

### Où c'est posé (mécanique)
Les noms `Ok-Verify-Token` / `Ok-Timestamp` / `Ok-Verify-Sign` **existent bien comme littéraux** (casse capitalisée,
HTTP insensible à la casse) dans `lib/secureIframe.js`, posés par l'intercepteur **`aB`** (≠ `Bp`) :
```js
// aB({requestUrl:e, fetchConfig:t, header:r, ontConfig:n})
let {token:a, timestamp:s, signature:c} = await Xl.getTokenAndSign({url:o, fetchConfig:t, ontConfig:n});
r["Ok-Verify-Token"]=a; r["Ok-Timestamp"]=s; r["Ok-Verify-Sign"]=c;
```
`aB` est appelé depuis `Ax` quand `ontConfig.needSign` est vrai. `o` = `pathname+search` (pas le host).
`Xl = {getTokenAndSign: x2}`. **Tout est en JS pur (pas WASM)** — le WASM voisin ne sert qu'au fingerprint.

### Algorithme exact (`x2`)
1. **token** = `tokenForTest` sinon `crypto.randomUUID()` → **généré côté client par requête**.
   PAS de handshake serveur, PAS de stockage. La valeur capturée n'a aucune importance, n'importe quel UUID marche.
2. **timestamp (ok-timestamp)** = `Date.now()` en **ms** (appel séparé de celui de user-device-sign, mais même requête → ~même ms ; l'égalité observée dans l'oracle est attendue mais non garantie).
3. **dérivation de clé HMAC** à partir du token :
   - `b` = SHA-256(token) en **hex** (64 caractères).
   - `A` = `timestampForTest` sinon `Math.floor(Date.now()/1000)` (**secondes**).
   - `P = Math.floor(A/600 % 32)`, `k = Math.floor(A/3600 % 32)`.
   - pour `N` de 0 à 31 : `O = (P + (k+N)*N) % 32` ; `h += b[O]` → clé `h` = 32 caractères hex piochés dans le SHA-256.
4. **chaîne signée `w`** :
   - défaut (GET) : `w = url.replace("?","")` (remplace **uniquement le 1er `?`**).
   - POST/PUT : `h0 = url.split("?")[0]` (path sans query) ; `w = h0 + body`.
     (si body = FormData : `w = h0 + "{" + entries.map(\`${k}=${v}\`).join(",") + "}"`)
5. **signature (ok-verify-sign)** = `base64( HMAC-SHA256(key=h, msg=w) )` (32 octets → 44 b64).

### Repro Node (match exact)
```js
const crypto=require('crypto');
const token="2c15dff6-9812-49dd-82b7-c40f6da91beb", ts_ms=1781702682825;
const path="/priapi/v2/wallet/tx/signInfo";
const body='{"coinId":21100,"fromAddr":"0x4524fc0edb972fa09c8af5859025c795c81287fc","chainId":11155111}';
const A=Math.floor(ts_ms/1000);
const b=crypto.createHash('sha256').update(token,'utf8').digest('hex');
const P=Math.floor(A/600%32), k=Math.floor(A/3600%32);
let h=""; for(let N=0;N<32;N++) h+=b[(P+(k+N)*N)%32];   // -> 65be479abdd82cc28ddba974eb56e44e
const sig=crypto.createHmac('sha256',Buffer.from(h,'utf8')).update(path+body,'utf8').digest('base64');
console.log(sig==="CRD7IoZEJBGkzRA3VSIjkINQYW+yy3eUXKEdXIYvQLg=");  // true
```

### Génération d'une requête fraîche (auto-suffisante)
```js
const token=crypto.randomUUID();          // n'importe quel UUID
const ts_ms=Date.now();                    // -> Ok-Timestamp
const A=Math.floor(ts_ms/1000);
// ... dérive h comme ci-dessus, puis ...
const sig=HMAC_SHA256_b64(h, path+body);   // -> Ok-Verify-Sign ; Ok-Verify-Token=token
```
→ **Aucune valeur capturée à réutiliser** : le token est jetable, la clé en dépend, aucun secret en dur.

## 📋 Récapitulatif : tous les headers pour appeler signInfo
Tout est désormais reproductible sans credential store ni handshake :
| header | source | reproductible |
|---|---|---|
| `user-device-sign` | `Bp` : AES-128-ECB clé `H6379FIktyVeUAje` (cf. plus haut) | ✅ calculé |
| `ok-verify-token` | `x2` : `crypto.randomUUID()` jetable | ✅ généré |
| `ok-timestamp` | `Date.now()` ms | ✅ généré |
| `ok-verify-sign` | `x2` : HMAC-SHA256(clé dérivée du token+ts_sec, path+body) | ✅ calculé |
| `devid` / `device-token` / `web-deviceId` | storage.local (stable/installation) | ♻️ réutiliser la capture |
| `fingerprint-id` / `tmx-session-id` / `session-id` / `x-id-group` | `lS()` / storage (stable/installation) | ♻️ réutiliser la capture |
| `risk-params` | `fingerprint-id=…&fp-status=…&session-id=…` | ✅ reconstruit depuis les IDs |
| `plugin-version`, `platform`, `locale`, `x-cdn`, `x-api` | constantes/env | ✅ |
