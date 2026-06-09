# Stratégie Playwright — Passage d'ordres Bourse Direct

> Document de référence issu de l'exploration live du site BD (juin 2026).
> Sert à coder/corriger les passages d'ordres en mode Playwright connecté.

## 1. Architecture API confirmée

**Base** : `https://www.boursedirect.fr/hub/trading`
**Auth** : cookies de session (X-Auth-Token) — fonctionne via `page.evaluate(fetch, credentials:'include')`
**Transport** : axios (XMLHttpRequest), pas fetch

### Endpoints
| Endpoint | Rôle |
|---|---|
| `POST /order/create` | Crée+valide l'ordre, calcule frais, retourne `order_id`. **Ne soumet PAS au marché.** |
| `POST /order/send` | Soumet l'ordre standard au marché (irréversible) |
| `POST /order/execute/strategy` | Soumet un ordre Expert (smart/meta) |
| `POST /order/calcul-fees` | Calcule les frais (order_id, login) |
| `POST /order/context-validate` | Valide le contexte (instrument, compte) |
| `POST /order/messages` | ACK des messages/avertissements |
| `/order/deal/*` | Variantes pour opérations sur titre (corporate actions) |

## 2. Payload réel /order/create (capturé en live)

```json
{
  "login": "<BD_LOGIN>",
  "mic": "XPAR",
  "ticker": "AI",
  "currency": "EUR",
  "quantity": "1",
  "portfolio": "<BD_ACCOUNT>",
  "type": "limit",
  "side": "buy",
  "validity": "day",
  "validityDate": "2026-06-10T00:00:00.000Z",
  "settlement": "cash",
  "limit": 168.0,
  "stop": null,
  "position_effect": "close",
  "globex": false,
  "comment": null,
  "brokerage": null
}
```

### CORRECTIONS à apporter à bourse_direct_orders.py
1. **`login` en MAJUSCULES** : le form envoie `<BD_LOGIN>` (BD_LOGIN.upper())
2. **`ticker` SANS préfixe `E:`** ← ERREUR ACTUELLE dans TICKER_MAP.
   - Le vrai ticker API = mnémonique de la place : `AI` (XPAR), `AIL` (XETR), `ILMN` (XNAS)
   - Donc EXENS.PA → ticker `EXENS` + mic `XPAR` (PAS `E:EXENS`)
3. **`quantity` = string** ("1" pas 1)
4. **`validityDate` = ISO 8601** avec heure (`2026-06-10T00:00:00.000Z`), requis si validity≠day ; sinon null/jour courant
5. **Champs requis ajoutés** : `position_effect` ("close"/"open"), `globex` (false), `comment` (null), `brokerage` (null)

### Validité (select validity)
`day` (Jour) | `revocation` (Révocation) | `end_of_year` (Fin d'année, XPAR only) | `other` (Autre + date)

### Types d'ordres (select order_type) — selon la place
- XPAR (Euronext) : limit, market, best_limit, stop, stop_limit, tal
- XETR (Xetra) : limit, stop, stop_limit (pas de market)
- Champs prix : `limit` (cours limité), `stop` (seuil déclenchement)

## 3. Réponses API
- Succès : retourne objet ordre avec `id`/`order_id` + `fees`
- Erreur métier : `{"code":403,"message":"Veuillez acheter cet instrument sur le marché Euronext"}`
  → IMPORTANT : pour les valeurs FR, toujours utiliser MIC `XPAR`, pas `XETR`

## 4. Ordre Expert (smart)

Flow UI : Étape 1 choix "Ordre expert" → config (compte/sens/qté/validité/type) →
étape 2 stratégie : **STOP LOSS / TAKE PROFIT / STOP SUIVEUR** (chacun ACHAT ou VENTE).

Payload (depuis le bundle buildCreateOrderPayload) :
```json
{
  ...champs standard...,
  "nature": "smart",
  "type": "meta",          // si side=sell ET strategy=take_profit
  "smart": {
    "strategy": "take_profit",   // ou "stop_loss" ou "trailing"(stop suiveur)
    "stop_loss": 57.20,
    "take_profit": 72.90,
    "variation": null            // pour stop suiveur (trailing %)
  }
}
```
Puis confirmer via `POST /order/execute/strategy` (au lieu de /order/send).

Les ordres Expert apparaissent dans "Mes ordres" comme l'EXENS actuel
(Take Profit : Seuil 57.20 / Profit 72.90, statut En cours).

## 5. Recherche de valeur
- Champ : `#searchbar-input` (placeholder "ISIN, mnémo, nom de valeur, poser une question")
- Résultats : `.main-content-result` (cliquable) → mène à la fiche valeur
- Format résultat : `MNEMO | Nom | PLACE - ISIN | cours | var%`
- URL fiche : `/fr/marche/{place}/{nom}-{ISIN}-{mnemo}-{devise}-{MIC}/seance`
  - Forcer Euronext Paris : `/fr/marche/euronext-paris/...-XPAR/seance`

## 6. Formulaire d'ordre (sélecteurs, sur fiche valeur)
- Conteneur : `#ordertrade`
- Choix Classic/Expert étape 1 : 2 boutons `Sélectionner`
- Champs : `select[name=account]`, `input#buy`/`input#sell`, `select[name=settlement]`,
  `select[name=validity]`, `input[name=quantity]`, `select[name=order_type]`,
  `input[name=limit]`, `input[name=stop]`
- Bouton : `button:has-text("VALIDER MON ORDRE")` / `"VALIDER MON ORDRE EXPERT"`
- Stratégies Expert : `.strategy-choice-item` (3 items)
- ⚠️ Remplissage : utiliser l'API directe (fetch) plutôt que l'UI Vue.js,
  bien plus fiable (les champs Vue gardent des valeurs résiduelles).

## 7. Stratégie de codage recommandée

**Passage d'ordre = appel API direct, PAS manipulation du formulaire UI.**
On a le payload exact + l'auth par cookies. C'est :
- Plus fiable (pas de bug Vue.js sur les champs)
- Plus rapide
- Plus facile à tester

Flow : `create_order()` (POST /order/create) → afficher recap+frais →
confirmation user → `send_order()` (POST /order/send) ou `execute_strategy()`.

## 8. Annulation d'ordre — CONFIRMÉ
Endpoint : `POST /hub/trading/order/cancel` (trouvé dans portfolio.js)
Payload probable : `{order_id, login, csrf}` (même forme que send_order).
→ Permet d'annuler un ordre Take Profit / Stop Loss en cours depuis Telegram.

## 9. Possibilités en mode connecté (au-delà des ordres)
- **Lecture temps réel** : cours, carnet d'ordres, seuils ✅ (reader fait)
- **Annulation d'ordre** : /order/cancel ✅ (à câbler)
- **Historique exécutions** : pourrait remplacer le Gmail sync (détection auto des
  clôtures SL/TP en lisant le carnet — statut passe de "En cours" à "Exécuté")
- **Investissements programmés** : DCA auto sur ETF (0€ frais) — création via UI
- **Alertes de cours** : MobilAlert (priv/mobilalert.php)
- **Fiche valeur enrichie** : PTO/VTO, seuils haut/bas séance, capi, news société

## 10. Idées de features mode connecté
1. `/ordre` exécute réellement (create → recap → /oui → send) — code prêt à tester
2. `/annuler_bd <ticker>` : annule l'ordre TP/SL en cours sur BD
3. Auto-détection clôture : au /sync, si un ordre passe "Exécuté" → clôture la
   position dans le bot automatiquement (P&L calculé sur seuil/profit de l'ordre)
4. `/scan` → bouton "passer l'ordre" qui pré-remplit /ordre avec le bon ticker BD
5. Vérification post-ordre : après /oui, relire le carnet pour confirmer présence
