# TradingBot

**Assistant de trading personnel pour Bourse Direct, piloté par Telegram.**

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Telegram-26A5E4?logo=telegram&logoColor=white)
![AI](https://img.shields.io/badge/IA-Groq%20%7C%20Gemini%20%7C%20Anthropic%20%7C%20OpenAI%20%7C%20Mistral-orange)
![Broker](https://img.shields.io/badge/Courtier-Bourse%20Direct-navy)
![Mode](https://img.shields.io/badge/Mode-Classic%20%7C%20Playwright%20%7C%20Autonome-purple)

Bourse Direct ne dispose pas d'API publique. TradingBot comble ce manque : il analyse votre portefeuille chaque matin, surveille vos positions, passe vos ordres réels depuis Telegram, et peut même opérer en totale autonomie sur un budget isolé — le tout piloté depuis votre iPhone.

**Trois modes de fonctionnement :**
- **Mode Classic** (défaut) : données via Yahoo Finance + import par captures d'écran — aucun accès à votre compte BD requis.
- **Mode Playwright** (optionnel) : le bot se connecte à Bourse Direct via un navigateur headless, lit vos données en temps réel et **passe des ordres Expert réels depuis Telegram** (achat + SL + TP en un seul ordre, avec confirmation).
- **Mode Autonome** (optionnel, nécessite Playwright) : le bot gère un **budget isolé en totale autonomie** — il scanne le marché, entre en position, relève le SL au PRU à +6%, et vous notifie pour chaque action.

---

## Démo rapide — 30 secondes pour comprendre le flux

```
/start          → message de bienvenue : nb de positions, cash disponible

/cash 1500      → enregistre votre cash disponible

/add GNFT.PA 100 8.51 7.66 9.79
                → ajoute une position (ticker, qté, PRU, SL, TP)

/setup LBIRD 48 24.46
                → génère les 2 instructions d'ordres à saisir sur Bourse Direct
                   (stop-loss à 22.01€ + take-profit à 28.13€)

/status         → portefeuille complet avec P&L en temps réel

📸 photo        → envoyez une capture d'écran de votre app Bourse Direct
                   → le bot lit les positions et les importe automatiquement

/morning        → briefing IA : état du portefeuille + macro + opportunités
```

**Avec le mode Playwright activé :**

```
/connect                                    → connexion à Bourse Direct (code TOTP)
/ordre acheter TTE.PA 3 expert 54.2 49.0 61.0  → ordre Expert achat (entrée+SL+TP)
/oui                                        → envoie l'ordre au marché
/auto on 500                                → active le trading autonome sur 500€
```

---

## Prérequis

| | |
|---|---|
| **Python** | 3.10 ou supérieur (`python3 --version`) |
| **Git** | Pour cloner le projet (`git --version`) |
| **OS testé** | macOS, Linux. Windows fonctionne mais non testé en prod |
| **Compte Telegram** | Pour créer votre bot via @BotFather |
| **Clé API IA** | Groq ou Gemini sont gratuits (voir étape 3) |
| **Bourse Direct** | Compte actif |
| **Dépendances système** | Aucune — tout est installé via `pip` |

**Stabilité en production :** le bot tourne en arrière-plan (polling Telegram, scheduler). Il est stable sur un Mac allumé en permanence ou un serveur Linux. Pour un usage continu, `./bot.sh autostart` installe un service `launchd`/`systemd` qui le relance au boot et après un crash.

### Installer Python et Git (macOS)

Sur Mac, la façon la plus simple est d'utiliser **Homebrew** — le gestionnaire de paquets de référence pour macOS.

**1. Installer Homebrew** (si pas déjà installé) :

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

> Homebrew permet d'installer et mettre à jour des outils en ligne de commande en une seule commande `brew install ...`. C'est l'équivalent de `apt` sur Ubuntu ou `pip` pour Python — indispensable sur Mac pour tout développement.

**⚠️ Étape critique — À la fin de l'installation, Homebrew affiche 3 commandes à exécuter pour s'ajouter au PATH.** Exécutez-les avant de continuer, sinon `brew` restera introuvable :

```bash
echo >> ~/.zprofile
echo 'eval "$(/opt/homebrew/bin/brew shellenv zsh)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv zsh)"
```

**2. Installer Python et Git via Homebrew :**

```bash
brew install python git
```

> **Note :** macOS inclut Python 3.9 par défaut — trop ancien pour ce bot (3.10 minimum requis). `brew install python` installe la version récente (3.12+). Si après l'installation `python3 --version` affiche encore 3.9, ouvrez un **nouveau terminal** avant de continuer.

Vérifiez l'installation :

```bash
python3 --version   # doit afficher Python 3.10 ou + (ex: 3.13.x)
git --version       # doit afficher git version 2.x
```

### Installer Python et Git (Linux)

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install python3 python3-pip python3-venv git

# Fedora / RHEL
sudo dnf install python3 python3-pip git
```

### Installer Python et Git (Windows)

Téléchargez Python depuis [python.org](https://www.python.org/downloads/) (cochez "Add to PATH" à l'installation) et Git depuis [git-scm.com](https://git-scm.com/download/win).

---

## 🚀 Installation — 5 étapes

> **Lisez cette section en entier avant de lancer le bot.** Vous aurez besoin d'un bot Telegram fonctionnel pour utiliser les commandes — configurez tout ici d'abord.

---

### Étape 1 — Créez votre bot Telegram

1. Ouvrez Telegram et cherchez **@BotFather**
2. Envoyez `/newbot`
3. Choisissez un nom (ex : `Mon Trading Bot`)
4. Choisissez un username (ex : `montrading_bot`) — doit se terminer par `bot`
5. BotFather vous envoie un **token** : `***REMOVED***:ABCDefGhIJKlmNoPQRsTUVwXYZ`

⚠️ **Copiez ce token — vous en aurez besoin à l'étape 4.**

---

### Étape 2 — Obtenez votre Chat ID

Votre Chat ID limite le bot à vous seul — il refuse tous les autres utilisateurs.

1. Cherchez **@userinfobot** sur Telegram
2. Envoyez n'importe quel message
3. Il vous répond avec votre **Id** numérique (ex : `***REMOVED***`)

⚠️ **Copiez cet ID — vous en aurez besoin à l'étape 4.**

---

### Étape 3 — Choisissez votre provider IA

| Provider | Gratuit ? | Modèle par défaut | Inscription |
|---|---|---|---|
| **groq** | ✅ **Oui — recommandé** | llama-3.3-70b-versatile | [console.groq.com](https://console.groq.com) |
| **gemini** | ✅ **Oui** | gemini-1.5-flash | [aistudio.google.com](https://aistudio.google.com) |
| `anthropic` | Payant | claude-sonnet-4-6 | [console.anthropic.com](https://console.anthropic.com) |
| `openai` | Payant | gpt-4o-mini | [platform.openai.com](https://platform.openai.com) |
| `mistral` | Payant | mistral-small-latest | [console.mistral.ai](https://console.mistral.ai) |

**Conseil pour débuter :** utilisez **Groq** — tier gratuit généreux, aucune carte bancaire requise.

---

### Étape 4 — Clonez et configurez

```bash
git clone https://github.com/myopencomm/Tradingbot.git
cd Tradingbot
```

Créez un environnement virtuel et installez les dépendances :

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env
cp positions.example.json positions.json
```

Éditez `.env` avec vos vraies valeurs :

```env
AI_PROVIDER=groq
GROQ_API_KEY=gsk_...

TELEGRAM_TOKEN=***REMOVED***
CHAT_ID=***REMOVED***
```

Éditez `positions.json` pour définir votre cash de départ :

```json
{
  "cash_available": 500,
  "positions": {},
  "pending_orders": {}
}
```

---

### Étape 5 — Lancez le bot

```bash
venv/bin/python3 main.py
```

Envoyez `/start` à votre bot sur Telegram — vous devez recevoir un message de bienvenue.

✅ **Le bot est opérationnel.**

> ⚠️ **Lancé ainsi, le bot s'arrête si vous fermez la fenêtre de terminal.** Pour un usage durable, utilisez plutôt `./bot.sh start` (arrière-plan) et `./bot.sh autostart` (relance auto au boot et après crash) — voir [Lancer en tâche de fond](#lancer-en-tâche-de-fond).

---

## Fonctionnalités

| | |
|---|---|
| **Briefing matinal 9h05** | Analyse IA : état des positions + contexte macro + top opportunités |
| **Surveillance 4×/jour + sync horaire** | Checks 9h / 12h / 15h / 17h (alertes SL/TP) + sync BD silencieux chaque heure : détection automatique des exécutions |
| **Séance US prolongée** | Wall Street tournant jusqu'à 22h Paris, le bot prolonge la surveillance des positions US (checks 18h / 20h / 21h40, alertes seules) et lance un **scan US** à 16h — plus seulement au briefing de 9h05 (`US_EXTENDED_HOURS`) |
| **Trailing stop automatique** | À +5% (manuel) / +6% (autonome, `AUTO_BREAKEVEN_PCT`), l'ordre Expert est **remplacé sur BD** avec le SL au PRU (P&L ≥ 0 garanti) |
| **Ordres Expert réels** | `/ordre acheter TTE.PA 3 expert 54.2 49.0 61.0` — achat+SL+TP en un seul ordre, envoyé à BD (Euronext + marchés US) |
| **Validité des ordres** | Par séance, max (fin d'année Euronext / fin de mois US), ou date précise JJ/MM/AAAA |
| **Mode Autonome** | Budget isolé géré en totale autonomie : scan → entrée → SL au PRU à +6% → sortie détectée → réinvestissement. Ordres d'entrée non exécutés à la clôture : annulés auto (anti-sélection) |
| **Positions HOLD long terme** | `/hold TICKER` : sortie du périmètre bot (pas d'alertes, hors P&L trading, jamais proposée à la vente) |
| **Sélection momentum validée** | Momentum 12 mois (hors dernier mois) + cours > MM200 + entrée sur repli sain (RSI 35-65) — voir [Stratégie](#stratégie-de-sélection--validée-par-la-recherche-académique) |
| **Sizing par le risque** | Perte au SL = 1% du budget autonome, SL ≈ 2×ATR, taille réduite si volatilité élevée, série de pertes, ou corrélation forte avec une position déjà détenue (entrée bloquée au-delà de 0.85) |
| **Mode gain réduit** (opt-in) | Si rien ne passe à +10%, trades courts (TP +3-8%, 1-5 jours) — désactivé par défaut (`SMALL_GAIN_MODE=on` pour l'activer) |
| **Dashboard visuel** | http://localhost:8642 (accès Tailscale possible) + `/dashboard` Telegram : P&L cumulé, cash engagé, ROI, trades filtrables |
| **Coûts API dans le bilan** | Chaque appel IA enregistre ses tokens réels (`api_costs.json`) ; `/stats` et le dashboard affichent le coût cumulé et le **P&L net après coûts IA** — bilan honnête de l'efficacité du bot |
| **Instructions d'ordres** | Format Bourse Direct step-by-step, prêt à saisir sur mobile ou web |
| **Import screenshot** | Envoyez vos captures d'écran — le bot lit et importe automatiquement |
| **Import CSV** | Envoyez l'export Bourse Direct — importe avec SL/TP par défaut |
| **IA pluggable** | 5 providers : Groq, Gemini (gratuits), Anthropic, OpenAI, Mistral |
| **Indicateurs techniques** | RSI 14j, momentum 1 mois et 12-1, MM200, ATR 14j, volatilité réalisée, ratio volume — filtre avant analyse IA |
| **Catalyseurs imminents** | Recherche résultats, contrats, OPA, rachats — signaux +10% et plus |
| **Sentiment marché temps réel** | VIX + CNN Fear & Greed injectés dans chaque briefing |
| **Sync Gmail Bourse Direct** | Détecte auto les emails "Finalisation de votre stratégie" et clôture les positions |
| **Menu de commandes Telegram** | Les commandes dans le menu natif (bouton bas-gauche) |
| **Contexte personnel** | Fichier de contexte IA pour des conseils adaptés à votre situation |

---

## Architecture — pour les devs

```
TradingBot/
├── main.py                  Point d'entrée : lance le scheduler + le polling Telegram
├── config.py                Variables d'env centralisées (lues depuis .env)
├── telegram_bot.py          Polling Telegram, routing des commandes, buffer photo
├── analysis.py              Prompts IA : briefing, scan, indicateurs techniques, catalyseurs
├── monitor.py               Vérification SL/TP 4×/jour, envoi des alertes, cycle autonome
├── autonomous_engine.py     Mode Autonome : scan → entrée Expert → breakeven → sortie
├── orders.py                Génère les instructions texte format Bourse Direct
├── portfolio.py             CRUD positions.json + import CSV + config autonome
├── prices.py                Prix temps réel + indicateurs techniques (RSI, momentum, volume)
├── ai_provider.py           Abstraction multi-providers avec vision (5 providers)
├── research.py              Recherche web DuckDuckGo : marché, actions, catalyseurs imminents
├── gmail_sync.py            Sync IMAP Gmail : détecte les ordres BD finalisés et clôture auto
├── stats.py                 Historique des trades, P&L, win rate, profit factor
│
├── bot_mode.py              [Playwright] Gestion Classic/Playwright + persistance bot_state.json
├── playwright_session.py    [Playwright] Singleton Chromium headless — lifecycle start/stop
├── bourse_direct_auth.py    [Playwright] Login BD + relay code TOTP 6 digits via Telegram
├── bourse_direct_reader.py  [Playwright] Lecture portefeuille CTO, cash, cours depuis BD
├── bourse_direct_orders.py  [Playwright] Passage d'ordres via API hub/trading (create + send)
├── sync_engine.py           [Playwright] Synchronisation BD → positions.json
│
├── .env.example                      Template de configuration
├── positions.example.json            Exemple de portefeuille
├── CLAUDE_TRADING_CONTEXT.example.md Template de contexte IA personnel
│
├── positions.json             Votre portefeuille — ignoré par git ✅
├── bot_state.json             Mode actif (classic/playwright) — ignoré par git ✅
├── CLAUDE_TRADING_CONTEXT.md  Votre contexte personnel — ignoré par git ✅
└── .env                       Vos secrets — ignoré par git ✅
```

**Flux de données :**
`positions.json` est la source de vérité en mode Classic. En mode Playwright, `bourse_direct_reader.py` synchronise les données réelles de BD dans ce même fichier — les deux modes sont compatibles. Les positions autonomes sont taguées `"autonomous": true` dans ce même fichier.

**Scheduler :** `schedule` (Python) — 4 checks SL/TP/jour + briefing 9h05. À chaque check, `autonomous_engine` est invoqué pour surveiller les positions autonomes et tenter de nouvelles entrées si Playwright est connecté. **Séance US** (`US_EXTENDED_HOURS=on`, défaut) : checks positions/ordres **US uniquement** à `US_CHECK_TIMES` (18h/20h/21h40, alertes seules — silencieux sans position US) + scan US à `US_SCAN_TIME` (16h). Les entrées/trailing autonomes, eux, tournent déjà chaque heure jusqu'à 22h via le sync horaire.

**IA :** chaque provider expose `complete(prompt)` et `complete_with_image(prompt, bytes)`. Ajouter un provider = hériter de `AIProvider` dans `ai_provider.py`.

**Dépendances clés :**
- `yfinance` — prix et historiques (Yahoo Finance, gratuit, sans clé)
- `duckduckgo-search` — recherche web (gratuit, sans clé)
- `schedule` — scheduler léger (cron-like en Python pur)
- `playwright` *(optionnel)* — navigateur Chromium headless pour le mode Playwright

---

## Commandes Telegram

### Portefeuille

| Commande | Description |
|---|---|
| `/status` | Portefeuille complet avec P&L temps réel, alertes SL/TP |
| `/cash [montant]` | Voir ou mettre à jour le cash disponible |
| `/stats` | Bilan des trades : win rate, P&L réalisé, profit factor, **coûts API IA et P&L net** |
| `/fallback [provider] [clé]` | IA de secours : `/fallback gemini CLE_API` teste la clé, l'enregistre dans `.env`, **supprime le message du chat** et active la bascule auto si le provider principal échoue. `/fallback` = état, `/fallback off` = désactiver |
| `/dashboard` | Graphique P&L cumulé + résumé visuel (image) — voir section [Dashboard](#dashboard-visuel) |
| `/lessons` | Ce que le bot a appris de ses trades passés + garde-fous actifs — voir section [Apprentissage](#boucle-dapprentissage) |

### Positions

| Commande | Description |
|---|---|
| `/add TICKER QTY PRU SL TP` | Ajouter une position manuellement |
| `/remove TICKER` | Supprimer une position |
| `/hold TICKER [off]` | Marquer HOLD long terme : **hors gestion bot** — plus d'alertes SL/TP ni trailing, exclu du P&L trading (`/stats`), jamais proposé à la vente/swap par l'IA. Le sync BD continue de suivre qté/PRU. Sans argument : liste les HOLD |
| `/sl TICKER PRIX` | Mettre à jour le stop-loss |
| `/tp TICKER PRIX` | Mettre à jour le take-profit |

### Ordres Bourse Direct (texte — Mode Classic)

| Commande | Description |
|---|---|
| `/setup TICKER QTY PRU` | Générer les instructions SL+TP à saisir après un achat |
| `/buy TICKER QTY PRU` | Générer un ordre Expert complet (achat + SL + TP groupés) |
| `/order buy\|sell TICKER QTY PRIX` | Générer une instruction d'ordre simple |
| `/attente NOM TICKER QTY PRIX [SL TP]` | Ordre en attente : réserve le cash, alerte quand le cours est atteint |
| `/annuler NOM` | Annuler un ordre en attente (côté bot) |

### Analyse IA

| Commande | Description |
|---|---|
| `/morning` | Déclencher manuellement le briefing matinal |
| `/scan` | Scanner des opportunités — catalyseurs + indicateurs techniques |
| `/scan us` | Scan limité aux **36 valeurs US** — plus rapide et bien moins coûteux en appels IA que le scan complet (~100 tickers). Utile pendant la séance de Wall Street (15h35-22h), le scan US automatique ne tournant qu'à `US_SCAN_TIME` (16h). Contrairement au scan planifié, il n'applique **pas** le plancher de cash : une demande explicite répond toujours |
| `/research TICKER` | Analyse approfondie : RSI, momentum, catalyseurs imminents |

### Clôture de trades

| Commande | Description |
|---|---|
| `/vendu NOM [PRIX]` | Clôturer une position — prix TP automatique si omis |
| `/close TICKER QTY PRIX [FRAIS]` | Clôturer avec frais de courtage |
| `/syncmail` | Vérifier Gmail pour les ordres Bourse Direct finalisés |

### Mode Playwright — Connexion

| Commande | Description |
|---|---|
| `/mode` | Afficher le mode actuel (Classic ou Playwright) et l'état de la session |
| `/connect` | Activer le mode Playwright — connexion à Bourse Direct avec relay TOTP |
| `/disconnect` | Fermer la session Playwright et revenir en mode Classic |
| `/sync` | Synchroniser le portefeuille depuis Bourse Direct — détecte et clôture automatiquement les ventes exécutées (TP/SL touchés), ajoute les positions issues d'achats exécutés. Les messages d'étape sont **éphémères** (supprimés dès que le résultat s'affiche) ; ils ne restent que si le sync échoue |
| `/trailing` | Forcer une vérification du trailing stop (SL au PRU) **maintenant**, avec le détail de chaque position et la raison d'un non-déclenchement. Le cycle automatique tourne chaque heure à **:35** (jours de marché, 9h-22h, session BD connectée) et dès qu'une position franchit son seuil aux checks 9h/12h/15h/17h — mais il reste silencieux s'il n'a rien à faire, contrairement à `/trailing` qui répond toujours |
| `/testordre TICKER` | Diagnostic : teste 5 variantes de payload d'ordre contre l'API BD (validation seule, rien n'est envoyé au marché) |
| `/capture` | Diagnostic générique : trace dans le log toutes les requêtes API que le site BD envoie. Lancer `/capture`, **puis refaire à la main l'action que le bot rate** (passer un ordre, annuler, modifier un SL/TP…) **dans la fenêtre Chromium du bot**. Une action faite sur téléphone ou dans un autre navigateur n'est pas capturée |

> **Sync automatique** : en plus du `/sync` manuel, un sync silencieux tourne **toutes les heures** (à :35, 9h-22h les jours de marché) — il ne vous notifie que si une exécution (achat ou vente) est détectée. Un sync est aussi déclenché 8 s après chaque passage d'ordre pour capter les exécutions immédiates.

### Ordres réels (Mode Playwright uniquement)

| Commande | Description |
|---|---|
| `/ordre acheter TICKER QTE marche [validite]` | Achat au marché |
| `/ordre acheter TICKER QTE limite PRIX [validite]` | Achat à cours limité |
| `/ordre acheter TICKER QTE expert ENTREE SL TP [validite]` | Achat Expert — entrée + stop-loss + take-profit en un seul ordre |
| `/ordre vendre TICKER QTE marche [validite]` | Vente au marché |
| `/ordre vendre TICKER QTE limite PRIX [validite]` | Vente à cours limité |
| `/ordre vendre TICKER QTE expert SL TP [validite]` | Vente Expert — stop-loss + take-profit en un seul ordre |
| `/oui` | Confirmer et envoyer l'ordre affiché (irréversible) |
| `/non` | Annuler l'ordre affiché |
| `/annuler_bd TICKER` | Annuler un ordre en cours sur Bourse Direct |

> **Validité (paramètre optionnel en dernier) :** `seance` (expire fin de séance) | `max` (défaut — fin d'année sur Euronext, révocation fin de mois sur les marchés US) | `JJ/MM/AAAA` (date précise)
>
> **Format des tickers :** utilisez le format Yahoo Finance — `TTE.PA`, `ASML.AS`, `AAPL`, `BP.L`, `SAP.DE`. La conversion vers le format interne Bourse Direct est automatique, y compris la résolution du code marché des titres US (NASDAQ=XNGS, NYSE=XNYS, détectée dynamiquement).
>
> **Pas de cotation :** si BD rejette un prix hors pas de cotation, le bot ré-arrondit automatiquement (SL vers le haut, TP vers le bas, règle conservatrice) et retente.
>
> **Flow :** `/ordre ...` → le bot affiche recap + montant prévisionnel → `/oui` pour envoyer, `/non` pour annuler (timeout 120s).

### Mode Autonome (Playwright requis)

| Commande | Description |
|---|---|
| `/auto on 500` | Activer avec un budget fixe de 500€ |
| `/auto on 20%` | Activer avec 20% du cash disponible |
| `/auto off` | Désactiver (les positions autonomes existantes restent surveillées) |
| `/auto pause` | Suspendre les nouvelles entrées sans changer le budget |
| `/auto positions 3` | Nombre max de positions autonomes **simultanées** (défaut 2). Le budget total ne change pas : plus de places = positions plus petites, mais risque cumulé plus élevé (chaque ligne peut perdre `RISK_PER_TRADE_PCT` % au SL) |
| `/auto status` | État complet + P&L en temps réel des positions autonomes |

> Le bot opère entièrement seul sur ce budget : il exploite les opportunités validées par le briefing/`/scan` (cycle d'entrée **toutes les heures** + à chaque check), entre en position via un ordre Expert (SL+TP garantis sur BD), et vous notifie pour chaque action. Maximum 2 positions simultanées ; les **ordres en attente comptent dans le budget** (fonds réservés). La position n'est créée qu'à l'**exécution réelle** de l'ordre, détectée par le sync.
>
> **Horaires par marché** : un titre US n'est acheté qu'entre 15h35 et 21h55 Paris (Euronext : 9h05-17h25) — les opportunités US validées le matin attendent l'ouverture de Wall Street. Depuis la **séance US prolongée**, le bot ne se contente plus des opportunités du matin : il **scanne les valeurs US à 16h** et **surveille les positions US jusqu'à 21h40** (alertes SL/TP), pendant les heures les plus actives de Wall Street (`US_EXTENDED_HOURS`, `US_CHECK_TIMES`, `US_SCAN_TIME`).
>
> **Mode gain réduit** (désactivé par défaut — `SMALL_GAIN_MODE=on` pour l'activer) : quand aucune opportunité à +10% ne passe la validation, le bot re-teste les meilleurs candidats en trade court (TP +3 à +8%, horizon 1-5 jours). Désactivé car forcer un trade quand rien ne passe est le schéma « overtrading » documenté (Barber & Odean 2000) — zéro trade est un résultat acceptable.
>
> **Trailing stop réel** : dès **+6%** (autonome, `AUTO_BREAKEVEN_PCT` — le backtest montre qu'à +3% le trail scratchait les futurs gagnants) ou **+BREAKEVEN_THRESHOLD%** (manuel), le bot **remplace l'ordre Expert sur BD** avec le SL remonté au PRU — automatique, TP inchangé, uniquement pour les positions protégées par un ordre Expert actif (les positions historiques sans ordre ne sont jamais touchées).

### Import & Aide

| Commande | Description |
|---|---|
| 📸 Photo | Captures d'écran du portefeuille Bourse Direct — import automatique |
| `/import` | Guide import CSV Bourse Direct |
| `/help` | Liste complète des commandes |
| `/tuto` | Guide interactif de configuration |
| `/update` | Afficher le commit en cours et la date de mise à jour |

---

## Mode Playwright — Connexion Bourse Direct

Le mode Playwright est **entièrement optionnel**. Le mode Classic (défaut) reste pleinement fonctionnel sans rien configurer.

### Ce que ça apporte

| | Mode Classic | Mode Playwright |
|---|---|---|
| Source des données marché | Yahoo Finance (différé 15 min) | Yahoo Finance + cours BD live |
| Source du portefeuille | Captures d'écran + saisie manuelle | Lecture automatique depuis BD |
| Cash disponible | Mis à jour manuellement | Synchronisé depuis BD |
| Passage d'ordres | Instructions texte à saisir vous-même | Ordres réels envoyés depuis Telegram |
| Expert (achat+SL+TP) | Texte d'instructions | Ordre réel posé sur BD en un seul appel |
| Mode Autonome | Non disponible | Disponible |

### Prérequis

```bash
venv/bin/pip install playwright
venv/bin/playwright install chromium
```

> **Dépannage — `/connect` répond « Impossible de lancer Playwright »**
> Le navigateur Chromium est manquant ou corrompu (ça arrive si un téléchargement précédent a été interrompu). Réinstallez-le depuis le dossier du bot :
> ```bash
> venv/bin/python3 -m playwright install chromium
> ```
> puis relancez `/connect`. N'utilisez jamais `pip install ...` seul : le bot tourne dans son propre `venv/`, pas dans le Python du système (et sur macOS la commande s'appelle `pip3`, pas `pip`).

### Configuration

Ajoutez dans votre `.env` :

```env
BD_LOGIN=votre_identifiant_bourse_direct
BD_PASSWORD=votre_mot_de_passe
```

### Utilisation

**Activer le mode Playwright :**
```
/connect
```
Bourse Direct utilise une authentification **TOTP** (application d'authentification à 6 chiffres — Google Authenticator, Authy...).

```
Bot : "Code 2FA Bourse Direct reçu par ton app ? Envoie-le ici (90 secondes) :"
Vous : 847291
Bot : "Mode Playwright actif ✅ Connecté à Bourse Direct"
```

**Passer un ordre Expert achat (entrée + SL + TP) :**
```
/ordre acheter TTE.PA 3 expert 54.20 49.00 61.00
```
→ Le bot crée l'ordre Expert sur BD, affiche le récapitulatif + montant prévisionnel
```
/oui   → envoie l'ordre au marché (irréversible)
/non   → annule
```

**Types d'ordres disponibles :**
- `marche` — au marché, exécution immédiate
- `limite PRIX` — ordre à cours limité
- `expert ENTREE SL TP` (achat) / `expert SL TP` (vente) — ordre Expert Bourse Direct : stop-loss + take-profit en un seul ordre

**Validité (optionnelle, en dernier argument) :**
- `seance` — expire en fin de séance
- `max` (défaut) — jusqu'à fin d'année pour Euronext, révocable pour les autres marchés
- `JJ/MM/AAAA` — date précise

### Comportement au redémarrage

Le bot démarre **toujours en mode Classic**, même si le mode Playwright était actif avant. La session Playwright ne survit pas à un redémarrage — relancez `/connect` manuellement.

### État du développement

| Fonctionnalité | Statut |
|---|---|
| Connexion BD + TOTP | ✅ Testé et fonctionnel |
| Lecture portefeuille CTO (`/sync`) | ✅ Testé et fonctionnel |
| Ordre au marché / limite | ✅ Testé et fonctionnel |
| Ordre Expert vente (SL + TP) | ✅ Testé et fonctionnel |
| Ordre Expert achat (entrée + SL + TP) | ✅ Testé et fonctionnel |
| Annulation d'ordre (`/annuler_bd`) | ✅ Testé et fonctionnel |
| Mode Autonome (`/auto`) | ✅ Fonctionnel — en cours d'affinage |

### Note sur les CGU

L'automatisation d'un site web via navigateur headless est techniquement en zone grise dans les CGU de la plupart des courtiers. Ce mode est conçu pour un usage personnel, sur votre propre compte. Utilisez-le en connaissance de cause.

---

## Mode Autonome — Trading géré par le bot

Le mode Autonome permet au bot de gérer un **budget isolé** en totale indépendance, sans aucune intervention de votre part.

### Ce que le bot fait seul

1. **Recherche** — À chaque check planifié (9h, 12h, 15h, 17h), il filtre ~150 actions selon la stratégie validée par la recherche (momentum 12 mois hors dernier mois, cours > MM200, zone d'entrée RSI 35-65), puis valide les meilleurs candidats avec l'IA (contrôle qualitatif : news, OPA, événements binaires)
2. **Entrée** — Place un ordre Expert achat (entrée + SL + TP) sur Bourse Direct — le SL et le TP sont garantis côté BD
3. **Trailing stop** — Quand la position atteint **+6%** du PRU (`AUTO_BREAKEVEN_PCT`), relève le SL au PRU (P&L ≥ 0 garanti)
4. **Sortie** — Les ordres Expert sur BD gèrent les sorties automatiquement (SL ou TP atteint). Le bot détecte la sortie et vous notifie

### Limites de sécurité

- **Maximum 2 positions simultanées** — jamais plus, même si le budget le permet
- **SL/TP obligatoires** — aucune entrée sans protection Expert BD
- **Playwright requis pour les entrées** — si la session expire, le bot ne peut plus entrer mais les positions existantes restent protégées par leurs ordres Expert sur BD
- **Marché ouvert uniquement** — aucune entrée en dehors des heures 9h05–17h35
- **Annulation auto des ordres d'entrée périmés** — un ordre d'achat limite non exécuté à la clôture du marché du titre est **annulé sur BD** (vérifié à chaque cycle d'entrée + sync horaire). Un limite qui traîne ne se remplit que quand le cours retombe à travers — c'est-à-dire quand la thèse momentum est déjà morte (anti-sélection). Idem si une validation ultérieure rend EXCLUS sur le même titre : l'ordre en attente est annulé immédiatement

### Exemple d'utilisation

```
/connect                  → connexion à BD
/auto on 500              → active avec 500€ de budget

[Au prochain check planifié]
Bot : "🤖 MODE AUTONOME — Entrée en cours
       ASML.AS | 1 titre @ 720€
       SL : 664€ (-7.8%) | TP : 800€ (+11.1%)
       Coût : 720€ | Setup technique momentum"

Bot : "✅ ORDRE AUTONOME PLACÉ SUR BD
       ASML.AS | 1 titre @ 720€
       SL : 664€ | TP : 800€
       Coût : 720€ | Budget restant : 0€

       💡 EN BREF
       ASML fabrique les machines qui gravent les puces les plus avancées.
       Quasi-monopole mondial : personne d'autre ne sait faire ces machines EUV.
       Momentum 12 mois solide, au-dessus de sa MM200, demande IA en forte hausse."

[Quelques jours plus tard, à +6%]
Bot : "🤖 AUTO BREAKEVEN — ASML
       Position à +3.4% | SL relevé au PRU (720€)
       P&L garanti ≥ 0"

/auto status              → voir P&L en temps réel
/auto off                 → désactiver les nouvelles entrées
```

---

## Sync automatique des ordres Bourse Direct

Bourse Direct envoie un email "Finalisation de votre stratégie" dès qu'un ordre expert est exécuté. Le bot peut détecter ces emails via IMAP et clôturer automatiquement les positions concernées.

### Configuration (une seule fois)

**1. Activez l'accès IMAP dans Gmail :**
> Paramètres Gmail → Voir tous les paramètres → Transfert et POP/IMAP → Activer IMAP

**2. Créez un mot de passe d'application :**
> Lien direct : [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)

**3. Ajoutez dans votre `.env` :**
```env
GMAIL_USER=votre@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

### Utilisation

| | |
|---|---|
| **Automatique** | Le bot vérifie Gmail à chaque check (9h, 12h, 15h, 17h) |
| **À la demande** | `/syncmail` — vérifie immédiatement |
| **Manuel** | `/vendu NOM [PRIX]` — si Gmail n'est pas configuré |

---

## Boucle d'apprentissage

Un LLM n'apprend pas par entraînement ici, mais le bot **accumule et réutilise** l'expérience de ses trades en trois temps :

1. **Capture** — à chaque décision d'achat (scan, briefing, gain réduit, ordre manuel), le *pourquoi* est mémorisé : thèse, régime de marché, RSI/momentum/volume à l'entrée, source. Le contrôle pré-achat autonome rafraîchit ce contexte au moment réel de l'achat, et un **filet de sécurité** dans le passage d'ordre capture a minima les indicateurs techniques si aucun chemin amont ne l'a fait — aucun trade ne se clôture plus avec un contexte vide.
2. **Post-mortem** — à la clôture, le bot croise le contexte d'entrée avec le résultat et tague automatiquement le défaut (ex. « entrée en surchauffe RSI ≥ 70 », « gap sous le SL — titre peu liquide »). Un contexte manquant est tagué comme **bug de capture**, jamais comme « perte sans signal d'alerte » — une leçon fausse est pire que pas de leçon.
3. **Leçons réinjectées** — les schémas perdants sont agrégés et rappelés à l'IA dans **tous** les prompts de validation, pour éviter de répéter les mêmes erreurs.

**Garde-fous pilotés par les données** (indépendants de l'IA) :
- **Cooldown 10 jours** : pas de re-entrée sur un titre qui vient de perdre.
- **Réduction de taille en série de pertes** : 2 pertes → 75 %, 3 → 50 %, 4+ → 35 % du budget.
- **Corrélation avec le portefeuille détenu** : corrélation des rendements quotidiens (90j) contre chaque position déjà gérée par le bot — au-delà de 0.85, entrée bloquée (même pari, aucune diversification) ; entre 0.6 et 0.85, taille réduite de moitié. Un score quant indépendant sur deux titres du même thème (ex. AIR + SAF, aéro) ne protège pas de la corrélation réelle des cours.

`/lessons` affiche à tout moment ce que le bot a retenu et les garde-fous actifs.

> **Source de décision unique** : le jugement ACHAT/EXCLUS est produit par **une seule fonction** (`validate_candidate`) que tous les chemins appellent (scan, briefing, gain réduit, contrôle pré-achat autonome). Même stratégie d'analyse, mêmes règles, mêmes leçons partout — seul l'objectif de TP varie selon le mode.

---

## Dashboard visuel

Un tableau de bord local est servi en permanence par le bot : **http://localhost:8642**

- **Cartes de synthèse** : P&L réalisé / latent / total, win rate, profit factor, cash, performance en €/jour depuis le premier trade, ROI sur cash engagé
- **Courbe du P&L cumulé** sur axe temporel réel — la taille de chaque point est proportionnelle au cash engagé sur le deal
- **P&L par trade** : une barre par trade avec nom, date, cash engagé et résultat annoté
- **Tableau des trades filtrable** (texte, WIN/LOSS) avec colonnes Investi et ROI
- **Positions ouvertes** : **PRU en devise de cotation ET PRU en euros**, cours, variation, P&L latent, SL/TP, badge `auto` (mode autonome) ou `hors bot` (position `hold`). Le marqueur `ᴮᴰ` signale un chiffre relevé sur Bourse Direct au dernier sync plutôt qu'un cours live — c'est ce qui donne un **P&L aux titres que yfinance ne cote plus** (GVN, MCPHY : faillite, cotation suspendue). Les positions hors gestion affichent **⛔ en SL et TP** : ces seuils ne sont surveillés par personne

La page se régénère à chaque visite — les données sont toujours fraîches. Sur Telegram, `/dashboard` envoie la même vue en image avec le résumé chiffré.

### Accès à distance (Tailscale)

Le dashboard n'a **pas d'authentification** — ne l'exposez jamais à Internet. Deux options sûres :

**Option recommandée — `tailscale serve`** (tailnet uniquement, HTTPS automatique, aucun changement de config) :

```bash
tailscale serve --bg 8642
# → https://votre-mac.votre-tailnet.ts.net accessible depuis vos appareils Tailscale
tailscale serve --https=443 off   # pour désactiver
```

**Alternative — bind réseau** (tailnet + réseau local) : dans `.env`, ajoutez `DASHBOARD_BIND=0.0.0.0` puis `./bot.sh restart`. Le dashboard devient accessible via l'IP Tailscale du Mac (`http://100.x.y.z:8642`). À réserver à un réseau de confiance.

---

## Personnaliser les conseils IA

Par défaut l'IA ne connaît que votre portefeuille temps réel et le contexte macro du jour. Pour des conseils adaptés à votre situation réelle :

```bash
cp CLAUDE_TRADING_CONTEXT.example.md CLAUDE_TRADING_CONTEXT.md
# Éditez avec votre situation — objectif, positions hors-bot, règles, historique récent
```

Ce fichier est injecté automatiquement dans chaque prompt IA (`/morning`, `/scan`, `/research`). Il est dans `.gitignore` — jamais publié.

---

## Mettre à jour le bot

```bash
./bot.sh update
```

Une seule commande : `git pull` + installation des nouvelles dépendances + redémarrage. Vérifiez ensuite les nouveautés via `/update` dans Telegram.

---

## Changelog

### 2026-07-29 — Ordres et PRU des valeurs US : lecture BD corrigée
- **Un ordre US était illisible** : `/sync` affichait `? : Achat Take Profit ⚠️ SL/TP non lus` alors que l'ordre JNJ était bien présent sur BD. Deux causes, toutes deux dans `bourse_direct_reader.py` :
  - **les montants US ne sont pas libellés en euros** — BD écrit `Seuil255.60 $US`, `Lim. 268.65 $US`, cours `267.430 USD`. Les regex n'acceptaient que `€` → seuil, profit **et prix d'exécution** revenaient vides. Les montants sont désormais reconnus dans toutes les devises BD (`€`, `$US`, `USD`, `£`, `CHF`) et l'ordre porte sa devise (`currency`), affichée dans le message de sync (`SL 255.6$`)
  - **le nom tombait sur `?`** — la classe de caractères du nom n'acceptait ni `&` ni parenthèses : `Johnson & Johnson(XNYS)` ne matchait pas. Corrigée, avec suppression du code marché accolé (`(XNYS)`, ` XNYS`)
- **Conséquence silencieuse la plus grave : le prix d'exécution non lu** — c'est lui qui donne le prix de sortie réel lors d'une clôture automatique. Une vente US déclenchée serait tombée sur un fallback de prix
- **PRU : BD convertit en euros ce que le bot stocke en devise de cotation.** L'onglet « Mes positions » affiche `PRU : 238,65 €` pour JNJ et `PRU : 317,1087 €` pour ILMN (cotée 192,52 USD) — écrit tel quel dans `positions.json`, à côté d'un SL/TP en dollars, il faussait le P&L latent et les distances au SL/TP. Preuve que BD raisonne bien en EUR : son `-46,74 %` sur ILMN ne tombe juste que si cours **et** PRU sont convertis
  - Le PRU n'est plus ignoré : il est **converti** dans la devise de cotation (`fx_to_eur`) puis écrit normalement. C'est la meilleure source de prix d'entrée (frais inclus)
  - **Pas de dérive du fx** : la valeur brute de BD est mémorisée (`bd_pru_raw`) et la conversion n'est refaite que si **BD** change son PRU (renfort, correction). Sans ce garde-fou, `entry_price` aurait bougé à chaque sync au rythme du taux de change, et le P&L avec lui
  - Si le taux est indisponible, `fx_to_eur` renvoie `1.0` en repli : la conversion est alors **refusée** (elle donnerait un PRU faux de ~14 % sur l'USD) et le sync le signale. Repli : prix d'exécution de l'ordre BD, puis prix de l'ordre autonome
- **Pourquoi convertir plutôt que garder le PRU en euros** : tout le suivi compare `entry_price` au cours yfinance, qui est en **devise de cotation** (`stats`, `monitor`, trailing, breakeven, dashboard). Un PRU en euros face à un cours en dollars donnait, sur ILMN, **-39,30 % / -3 610 €** au lieu de **-46,7 % / -4 892 €** (chiffres BD) — 1 280 € d'erreur. La valeur euro n'est pas perdue pour autant : elle est conservée telle quelle dans `bd_pru_raw` et **affichée dans le dashboard**. La conversion inverse (euro → devise) reste faite au moment de poser un ordre SL/TP, où les bornes doivent être dans la devise du marché
- **Dashboard : colonnes `PRU` et `PRU €`** côte à côte, la seconde marquée `ᴮᴰ` quand elle vient directement de Bourse Direct. Visibles aussi sur mobile (le tableau défile horizontalement)
- **Dashboard : les titres en faillite ont enfin un P&L.** GVN et MCPHY sont suspendus — yfinance ne les cote plus, leur ligne restait vide. Trois sources en cascade, dans cet ordre :
  1. cours live yfinance ;
  2. dernier relevé de **Bourse Direct** — le sync mémorise cours, valorisation et ±value tels que BD les affiche (`bd_price`, `bd_value_eur`, `bd_pnl_eur`), marqués `ᴮᴰ` ;
  3. **le PRU seul** (`worthless: true`) : pour un titre acté sans valeur, la perte, c'est tout le capital engagé — aucun cours n'est nécessaire. GVN **-133 €**, MCPHY **-768 €**, marqués `≈` car c'est un calcul et non un relevé (il ignore le résidu, 0,26 € sur GVN). Le drapeau est posé automatiquement par le sync dès que BD valorise la ligne à moins de 1 % de son coût — et retiré si la valeur revient
- Les relevés BD sont sauvegardés sans jamais être annoncés comme une « modification » (ils bougent à chaque cotation, ce n'est pas un événement)
- **Dashboard : ⛔ sur les SL et TP des positions `hold`** (GVN, MCPHY, ILMN) et badge `hors bot` — afficher des seuils que le bot ne surveille pas laissait croire à une protection active
- **Messages de `/sync` éphémères** : « Synchronisation en cours » et les traces de lecture sont supprimés dès que le résultat s'affiche (`progress_fn`). Ils ne subsistent qu'en cas d'échec — c'est alors le seul message qui explique pourquoi
- **Les lignes de position brutes ne partent plus dans Telegram** : la trace ajoutée pour ce diagnostic passait par `log()`, qui envoie AUSSI au chat — le résultat du sync était noyé sous les lignes brutes. Elle va désormais dans le fichier de log uniquement (`trace()`), comme celle des ordres

### 2026-07-29 — Univers découvert automatiquement : 36 → ~550 valeurs US
- **`market_universe.py`** : l'univers US n'est plus écrit à la main. Source officielle **Nasdaq Trader** (fichiers publiés quotidiennement, libres, sans clé) → 5 149 actions ordinaires → filtre de liquidité → indicateurs calculés **par lot**. Pipeline complet mesuré : **3,8 min** pour 2 558 valeurs
- **`compute_indicators_bulk()`** produit exactement les mêmes indicateurs que `get_technicals` (contrôle d'équivalence : 0 écart sur AIR.PA, GLE.PA, UNA.AS, OR.PA, ASML.AS). Indispensable : en unitaire, un an d'historique par ticker fait rate-limiter yfinance dès quelques centaines d'appels
- **`SCAN_US_MIN_DOLLAR_VOLUME` (défaut 200 M$/jour)** — le réglage qui compte. Mesuré : à 5 M$/j le top 8 se remplit de micro-caps biotech à +230 %/+785 % qui évincent tout le reste ; à 200 M$/j on obtient JBHT, SCCO, CVS, HUM, TD… liquides et sûrement traitables chez BD. `0` désactive et revient à la liste manuelle
- **Veto ATR déplacé dans le screen** : `validate_candidate` rejetait déjà les titres trop volatils (SL technique > `MAX_SL_PCT`) — les filtrer dès le classement évite de leur brûler une validation IA et empêche les « billets de loterie » de saturer le top 8
- **Rafraîchissement planifié le dimanche 08h00**, marchés fermés. **Jamais à la demande** : un passage complet fait rate-limiter yfinance, ce qui dégraderait les cours du scan et du suivi de positions
- Repli automatique et silencieux sur la liste manuelle si le cache est absent ou périmé (> 3 jours) — jamais de scan sur données mortes

### 2026-07-29 — `/stats` : P&L latent faux, corrigé
- **Une position dont le cours était indisponible disparaissait du total, sans aucun signal.** Constaté en réel : latent affiché **+7,91 €** au lieu de **+74,91 €**, AIR étant muette à cet instant (rate-limit yfinance). `/stats` liste désormais les positions non valorisées et annonce le total comme **INCOMPLET**
- **Aucune conversion de devise** : le P&L latent d'une position en USD était additionné tel quel à un total en euros. Corrigé via `fx_to_eur` (invisible aujourd'hui — AIR et GLE sont en EUR — mais faux dès la première position US)

### 2026-07-29 — Univers de scan élargi : 115 → 149 valeurs Euronext
- **+34 valeurs** (Paris, Amsterdam, Bruxelles). L'univers était incomplet **même sur le CAC 40** : `OR.PA` (L'Oréal, un des premiers poids de l'indice), `AKE.PA`, `BVI.PA`, `SW.PA`, `VIV.PA`, `URW.PA`, `FGR.PA` en étaient absents
- **Chaque ajout validé sur données réelles**, pas ajouté de mémoire : technicals yfinance exploitables (RSI, momentum 12-1, MM200) **et** liquidité médiane ≥ **2 M€ échangés/jour** sur 3 mois. 23 candidats ont été écartés pour illiquidité (de 0,01 à 1,97 M€/jour) — avec ~4 € de frais aller-retour et un seuil de rentabilité à 5×, une valeur au spread large coûte plus cher que les frais eux-mêmes
- Effet mesuré immédiatement : le filtre quantitatif passe de **7 à 12 candidats** (régime NEUTRAL), dont 3 nouveaux dans le top 8 envoyé à l'IA (`KBC.BR`, `AGS.BR`, `GNFT.PA`). Durée du screen inchangée (~5 s, 10 threads)
- Les garde-fous restent souverains : `GNFT.PA` (+195 % sur 12 mois) et `SOI.PA` arrivent haut au score mais sont **vetoés sur l'ATR** (5,6 % et 10,5 % → SL requis au-delà du plafond `MAX_SL_PCT`). Le momentum les remonte, le risque les écarte
- ⚠️ **Ne jamais ajouter un ticker sans repasser les deux tests** (données yfinance + liquidité) — la liste fixe existe précisément parce que l'IA inventait des tickers (commit `5a7dc2f`)
- `/scan us` et `US_UNIVERSE` inchangés (36 valeurs)

### 2026-07-28 — `/scan us` : scan US à la demande
- **`/scan us`** (ou `/scan_us` en un tap depuis le menu) : relance le scan sur les **36 valeurs US** seulement. Jusqu'ici le scan US n'existait qu'en automatique à `US_SCAN_TIME` (16h) — impossible de le relancer pendant la séance de Wall Street sans lancer le scan complet (~100 tickers), bien plus lent et coûteux en appels IA
- **Pas de plancher de cash** contrairement au scan planifié : une demande explicite doit toujours répondre, même si aucun achat ne passerait le garde-fou frais
- Prévient si la séance US est fermée (ouverture 15h35 Paris) : les opportunités validées attendront l'ouverture au lieu de laisser croire à une entrée imminente
- `/scan` sans argument reste l'univers complet, inchangé

### 2026-07-28 — Scan : taille réelle du moteur + explication de l'inaction
- **Fin de l'écart entre le scan et le moteur** : le scan dimensionnait avec `POSITION_BUDGET_PCT` (budget manuel) alors que le moteur autonome dimensionne **par le risque**. Le 28/07 il proposait LLY à **89% du cash** — une taille que le moteur refusait. Suivre l'affichage à la main revenait à contourner ses propres garde-fous. `compute_position_size()` est désormais la **source unique** utilisée par le passage d'ordre réel ET par l'affichage
- Si le moteur refuserait l'entrée (titre trop cher pour le budget de risque, veto de corrélation), le scan l'écrit et **n'affiche plus de commande manuelle**
- **Le scan explique pourquoi il n'entre pas** : `entry_blocked_reason()` (places occupées, mode désactivé, session BD non connectée, budget insuffisant). Avant, il affichait « Passer l'ordre (mode Playwright) » sans contexte, ce qui laissait croire que le mode autonome était inopérant alors qu'il était simplement à sa limite de positions
- **`/auto positions N`** : nombre max de positions autonomes simultanées (1-10). L'avertissement rappelle que N lignes exposent jusqu'à N × `RISK_PER_TRADE_PCT` % du budget
- **Bug corrigé** : `set_config()` remettait `max_positions` à sa valeur par défaut à **chaque** `/auto on` — un réglage manuel était silencieusement perdu au premier changement de budget

### 2026-07-28 — Commande `/trailing`
- **`/trailing`** : force une vérification du trailing stop à la demande, avec le détail de **chaque** position évaluée (écart au seuil, SL actuel, raison du non-déclenchement). Le cycle automatique reste volontairement silencieux quand il n'a rien à faire — utile en fonctionnement normal, frustrant quand on veut juste savoir où on en est
- Rappel du rythme automatique, désormais documenté dans `/tuto` et le README : **chaque heure à :35** (jours de marché, 9h-22h, session BD connectée) + déclenchement immédiat dès qu'une position franchit son seuil lors des checks 9h/12h/15h/17h
- La commande réarme les notifications d'échec : une demande explicite obtient toujours une réponse, même si le même échec a déjà été signalé par un cycle automatique

### 2026-07-28 — Trailing : bon ordre ciblé, plus de churn inutile
- **Cause du 403 « une erreur est intervenue »** (trailing AIR bloqué depuis le 27/07) : un bloc consolidé BD mélange l'ordre d'**achat parent exécuté** et la **protection active**. Le bot annulait toujours le premier id du bloc — celui du parent, non annulable. Textes réels relevés : `Achat(CPT) Ordre exécuté … Annulé` vs `Vente(CPT) … En cours`
- **Sélection explicite** : le trailing ne vise plus qu'un sous-ordre **actif ET non exécuté**, et seulement s'il est identifié **sans ambiguïté**. Sinon il **s'abstient** (aucune annulation à l'aveugle : sur compte réel, viser le mauvais id laisserait la position à nu) et explique la situation
- **`/order/cancel` : payload corrigé** — le site n'envoie que `{"order_id"}`, le bot ajoutait `login` + `csrf` (confirmé par capture réseau, HTTP 200 vs 403)
- **`BREAKEVEN_TOLERANCE_PCT` (défaut 0.3)** : BD arrondit au pas de cotation, donc un SL « au PRU » retombe quelques centimes dessous (196.84 pour un PRU de 196.90). Sans tolérance le bot annulait/reposait la protection en boucle pour un gain nul, en exposant la position à une fenêtre **sans protection** à chaque passage
- **Limite connue** : quand la protection est rattachée à l'ordre d'achat exécuté (cas des positions dont le SL/TP vient de l'ordre Expert d'achat initial), BD n'expose pas d'id annulable sur la page portefeuille — le bot s'abstient et le SL doit être remonté à la main. Les ids existent sur la page carnet d'ordres : `read_order_book()` journalise les appels API de cette page pour construire le correctif

### 2026-07-25 — Corrélation au portefeuille + garde-fou « thèse falsifiable »
- **`correlation_risk.py`** : avant une entrée autonome, corrélation des rendements quotidiens (90j, `CORR_LOOKBACK_DAYS`) contre chaque position déjà gérée par le bot (`portfolio.get_managed_positions()`, HOLD long terme exclues). Au-delà de `CORR_VETO_THRESHOLD` (défaut 0.85) : **entrée bloquée** — même pari qu'une position détenue, aucune diversification réelle même si les scores quant sont indépendants (ex. AIR + SAF, même thème aéro). Entre `CORR_DAMPEN_THRESHOLD` (0.6) et le seuil de veto : taille réduite de moitié, comme les autres réducteurs (série de pertes, volatilité)
- **`validate_candidate`** : le format de sortie exige désormais un champ **« Risque principal »** — un scénario concret et falsifiable qui invaliderait la thèse, pas une généralité. Objectif : forcer un raisonnement à double sens (thèse haussière + risque concret) avant le verdict ACHAT, plutôt qu'une étiquette LOW/MEDIUM/HIGH sans substance. Inspiré du mécanisme de débat haussier/baissier du framework open-source TradingAgents (TauricResearch) — le reste de son architecture multi-agents n'a pas été repris (déjà couvert ici par les garde-fous quantitatifs durs de `validate_candidate` : RSI, MM200, ATR, ratio R/R)
- Nouveaux paramètres `.env` : `CORR_LOOKBACK_DAYS`, `CORR_DAMPEN_THRESHOLD`, `CORR_VETO_THRESHOLD`

### 2026-07-18 — IA de secours (fallback multi-providers)
- **`FallbackProvider`** : si le provider IA principal échoue (crédits épuisés — incident du 17/07, panne, rate limit), le bot bascule automatiquement sur le(s) provider(s) de secours listés dans `AI_FALLBACK_PROVIDERS` (ex: `gemini,groq`), essayés dans l'ordre. Le bot reste opérationnel au lieu de devenir aveugle. Notification Telegram à la première bascule (throttlée 1×/6h)
- **`/fallback gemini CLE_API`** : configuration **depuis Telegram** pensée pour les non-techniciens — la clé est **testée en réel** avant activation, écrite uniquement dans `.env` local (gitignoré), et **le message contenant la clé est supprimé du chat** immédiatement (elle ne reste pas dans l'historique Telegram). `/fallback` = état de la chaîne (clés masquées), `/fallback off` = désactivation
- Clés lues à chaud (`os.environ` d'abord) : la bascule fonctionne **sans redémarrage**. Les appels Gemini sont désormais comptés dans les coûts API (tarifs flash/pro) — le bilan reste honnête quel que soit le provider

### 2026-07-18 — Coûts API intégrés au bilan (P&L net honnête)
- **`api_costs.py`** : chaque appel Anthropic enregistre ses tokens réels (renvoyés par l'API) dans `api_costs.json` (gitignoré), valorisés au tarif du modèle (Haiku/Sonnet/Opus). Amorce : 5,66$ constatés sur la console Anthropic du 01-17/07 (CSV) ; l'usage mai-juin, inconnu, n'est **pas estimé** — on ne comptabilise que le mesuré
- **`/stats`** : lignes « Coûts API IA » (total + mois en cours) et « NET après IA » ; **dashboard** : cartes « Coûts API IA » et « P&L net après coûts IA » + résumé Telegram
- Motif : les frais de courtage sont déduits par trade, mais les coûts IA (2e charge réelle) étaient invisibles → le bilan surestimait l'efficacité du bot. Conversion USD→EUR au fx live

### 2026-07-17 — Coûts API réduits (~60-70%) sans toucher à la décision
- **Résumé macro automatique** : `macro_analysis.md` (47 Ko ≈ 12k tokens) était injecté ENTIER dans chaque revue de positions (scan + briefing) — 60-70% de la facture API. Au-delà de 6 000 caractères, il est désormais **condensé (~2 500 chars) par le modèle cheap**, avec cache sur date de modification (regénéré uniquement quand le fichier change). Un dump de 47 Ko dilue l'attention du modèle : le condensé sert *mieux* la décision. En cas d'échec IA → texte intégral (jamais dégradé)
- **Lecture de graphique sur le modèle cheap** (`complete_cheap_with_image`, Haiku côté Anthropic) : décrire chandeliers/supports/résistances est une tâche descriptive, pas un jugement — le verdict ACHAT/EXCLUS reste intégralement sur le modèle principal (Sonnet)
- Combiné au plancher cash du scan US (ci-dessous), la facture attendue passe de ~10$/mois à ~3-4$/mois

### 2026-07-17 — Scan US auto : sauté quand aucun achat n'est possible
- Le scan US planifié (16h) est **sauté silencieusement** quand le cash est sous le **plancher de viabilité** (`min_viable_cash()` ≈ 200€ avec les défauts BD : gain brut au TP ≥ 5× les frais A/R). En dessous, chaque candidat serait vetoé « cash insuffisant » — le scan brûlait ~8 validations IA pour un résultat garanti vide (observé le 17/07 : cash 154€, 8 validations, 0 opportunité)
- Le `/scan` **manuel** n'est pas concerné (toujours complet, revue de positions incluse) ; le briefing de 9h05 avait déjà son propre seuil (1000€)
- Pas de branchement scan→swap : la rotation reste l'affaire de l'analyse hebdo du lundi 9h10, volontairement stricte (thèse invalidée uniquement, friction > gain si position < ~600€) — un swap quotidien piloté par le scan serait une machine à overtrading

### 2026-07-16 — Achat auto : résumé « EN BREF » en langage simple
- À chaque **ordre autonome placé**, le message Telegram inclut désormais **3 lignes en langage simple** : ce que fait l'entreprise + pourquoi le deal peut être gagnant (au lieu du seul ticker, ex. `GLE.PA`). Généré par `_deal_summary()` à partir des fondamentaux (secteur, consensus analystes, objectif), des techniques (momentum 12-1, MM200, RSI) et de la thèse validée
- Modèle **cheap** (Haiku côté Anthropic) via `complete_cheap`, généré **après** le placement de l'ordre → aucun délai sur le trade, best-effort (jamais bloquant si l'IA échoue)

### 2026-07-16 — Veto « résultats » : seuil numérique configurable
- **`EARNINGS_VETO_DAYS` (défaut 6)** : le veto résultats ne s'applique plus qu'aux résultats **imminents** (< N jours). Motif : un SL ne protège pas d'un **gap** de résultats (le titre ouvre au-delà du stop — modélisé dans `backtest.py`), mais bloquer une entrée à 3 semaines des résultats ampute le vivier **sans** protéger la position (un swing momentum croise de toute façon des résultats en cours de route)
- **Fin de la dérive IA** : l'IA excluait des candidats à 19-20 jours des résultats en inventant une fenêtre « < 21 jours » absente du code (la règle écrite disait < 5 j). La règle est désormais **numérique et explicite** dans les prompts : au-delà de `EARNINGS_VETO_DAYS`, résultats = simple drapeau + risque MEDIUM, jamais une exclusion
- **`_earnings_note()`** injecte le nombre exact de jours (« résultats 2026-08-04 (dans 19 j) ») pour que l'IA applique le seuil sans se tromper de calcul
- Ce que le veto ne fait **pas** : il ne sort pas une position détenue avant ses résultats (choix « veto court + drapeau » — le momentum assume de tenir occasionnellement un gap, borné par la taille au risque)

### 2026-07-15 — Séance US prolongée
- **Surveillance des positions US jusqu'à 21h40** : les 4 checks standards s'arrêtaient à 17h, mais Wall Street tourne jusqu'à 22h Paris. Nouveaux checks `US_CHECK_TIMES` (18h/20h/21h40) **limités aux tickers US**, alertes SL/TP seules — silencieux s'il n'y a aucune position US (pas de spam de status le soir)
- **Scan d'opportunités US à 16h** (`US_SCAN_TIME`) : `scan_us_opportunities()` rejoue le moteur de `/scan` sur le sous-univers US → le bot cherche des entrées pendant la séance américaine, plus seulement au briefing de 9h05. **Sauté automatiquement si le cash est sous le plancher de viabilité** (~200€ : en dessous, le garde-fou frais vetoerait tout candidat — inutile de brûler des validations IA). Le `/scan` manuel reste toujours complet
- Les entrées + trailing autonomes tournaient déjà chaque heure jusqu'à 22h (sync horaire) — seuls les **alertes de position** et le **scan** manquaient à l'appel après 17h
- Nouveaux paramètres `.env` : `US_EXTENDED_HOURS` (on/off), `US_CHECK_TIMES`, `US_SCAN_TIME` · `monitor.check_positions`/`check_pending_orders` acceptent `us_only=True` ; `scan_opportunities` accepte un `universe` restreint

### 2026-07-14 — Phase 2 : backtest + config « recovery »
- **`backtest.py`** : rejoue le moteur quantitatif sur l'univers de scan avec hypothèses pessimistes et frais réels — compare ancienne logique, Phase 1 et variantes (voir section [Backtest](#backtest-backtestpy))
- **Trailing breakeven autonome +3% → +6%** (`AUTO_BREAKEVEN_PCT`) : à +3% le trail transformait les futurs gagnants en sorties à zéro — P&L backtest ×9 à +6%
- **Config « recovery » validée par les données** (compte petit orienté rattrapage) : risque 2.5%/trade, coût ≤ 50% du budget, max 2 positions — moins de trades mais plus gros, pour amortir les frais fixes BD (0.4-0.8% par aller-retour sur des positions de ~500€)

### 2026-07-14 — Phase 1 : stratégie alignée sur la recherche académique
- **Sélection 12-1** : le screen quantitatif classe par momentum 12 mois HORS dernier mois (Jegadeesh & Titman 1993) — plus jamais par momentum 1 mois, dont les gagnants s'inversent (cause des achats de sommets de 06-07/2026)
- **Filtre MM200** : achat uniquement au-dessus de la MM200 (titre + indices) ; le régime passe en CORRECTION si CAC et S&P sont sous leur MM200 même avec VIX calme
- **Zone d'entrée RSI 35-65** + veto quantitatif dur à RSI > 70, inviolable par l'IA
- **SL adapté à la volatilité** : ≈ 2×ATR borné 3-10%, élargi si l'IA propose un stop dans le bruit ; TP ≥ 1.5× la distance du SL (ratio R/R minimum)
- **Sizing par le risque** : perte au SL = 1% du budget autonome (fini le all-in), coût ≤ 30% du budget, taille ÷2 si volatilité 20j > 1.5× la normale (Barroso & Santa-Clara 2015)
- **Prompts IA neutralisés** : suppression de « penche vers ACHAT » et « prime sur ton instinct de prudence » — décision symétrique, l'IA est un contrôle qualitatif (news, OPA, événements binaires)
- **Gain réduit opt-in** : mode désactivé par défaut (`SMALL_GAIN_MODE`) — l'overtrading forcé détruit la performance retail (Barber & Odean 2000)
- **Limite marchande** : l'entrée autonome se place 0.3% au-dessus du cours pour exécution immédiate — plus de limite qui traîne sous le marché
- Nouveaux paramètres `.env` : `RSI_ENTRY_MIN/MAX`, `RSI_HARD_MAX`, `ATR_SL_MULT`, `MIN/MAX_SL_PCT`, `MIN_RR`, `RISK_PER_TRADE_PCT`, `MAX_POSITION_PCT`, `VOL_SCALE_TRIGGER`, `SMALL_GAIN_MODE`

### 2026-07-14
- **`/hold TICKER [off]`** : position HOLD long terme, **hors gestion bot** — plus d'alertes SL/TP ni trailing, exclue du P&L trading (`/stats`), jamais proposée à la vente/swap par l'IA (le sync BD continue de suivre qté/PRU)
- **Annulation auto des ordres d'entrée périmés** : un achat limite autonome non exécuté à la clôture du marché du titre est annulé sur BD (cycle d'entrée + sync horaire) ; annulation immédiate si une validation ultérieure rend EXCLUS sur le même titre. Motif : anti-sélection — un limite qui traîne ne se remplit que quand le momentum s'est retourné (cas AF.PA)
- **Capture de contexte blindée** : le contrôle pré-achat autonome rafraîchit le contexte d'entrée au moment réel de l'achat + filet de sécurité dans le passage d'ordre ; un contexte manquant est tagué « bug de capture » au post-mortem, plus jamais « perte sans signal d'alerte » à tort

### 2026-06-24
- **Mode Autonome** (`/auto`) : le bot gère un budget isolé en totale autonomie — scan, ordre Expert achat, trailing stop à +3%, notifications Telegram pour chaque action
- **Ordres Expert achat** (`/ordre acheter TICKER QTE expert ENTREE SL TP`) : entrée + SL + TP en un seul ordre posé sur BD
- **Validité des ordres** : paramètre optionnel `seance | max | JJ/MM/AAAA` sur tous les `/ordre`
- **Trailing stop automatique** : quand une position atteint +5% du PRU, le SL est relevé au PRU dans le bot + notification avec commande `/ordre` prête à copier

### 2026-06-11
- **`bot.sh`** : script de gestion — `start/stop/restart/status/logs/update` + `autostart` (service launchd/systemd : démarrage au boot, relance auto après crash)
- **Menu de commandes Telegram** : les commandes apparaissent dans le menu natif (bouton bas-gauche de l'app)
- **Indicateur « écrit… »** : les trois points s'affichent pendant tous les traitements (analyses IA, fetch des cours, ordres Playwright)
- **Sentiment social composite** : score -100/+100 par ticker (tags StockTwits + VADER + scoring IA des messages Boursorama) + détection des pics de volume
- **Sentiment marché temps réel** : VIX et CNN Fear & Greed injectés dans le contexte macro de chaque briefing/scan
- **Nouvelles règles par défaut** : SL -7% / TP +10% — le TP est un minimum, l'IA vise plus haut quand le potentiel le justifie
- **Fix** : les actions US n'affichent plus `nan` dans le briefing avant l'ouverture de Wall Street

### 2026-06-03
- **`/ordre vendre|acheter`** : passage d'ordres réels sur Bourse Direct depuis Telegram — marché, limite, et Expert (SL+TP combiné). Flow : `/ordre ...` → recap + montant → `/oui` pour envoyer
- **`/sync`** : synchronisation réelle BD → `positions.json` — cash, quantités, détection des écarts
- **Mode Playwright** : connexion à Bourse Direct via Chromium headless avec authentification TOTP
- **Reader BD** : lecture live du portefeuille CTO depuis BD
- **`/mode`** : bascule Classic ↔ Playwright, affiche l'état de la session

### 2026-06-01
- **Gmail sync** : détection automatique des emails "Finalisation de votre stratégie" de Bourse Direct
- **`/vendu NOM [PRIX]`** : clôturer rapidement une position (prix TP automatique si omis)
- **Indicateurs techniques** : RSI 14j, momentum 1 mois, ratio volume injectés dans `/scan` et `/research`
- **Catalyseurs imminents** : recherche DuckDuckGo dédiée dans chaque analyse

---

## Limitations

**Données marché**
Les prix proviennent de Yahoo Finance via `yfinance` (données différées de 15 min en journée). Les actions suspendues ou en liquidation judiciaire peuvent ne plus avoir de cotation.

**Mode Playwright**
La session Playwright ne survit pas à un redémarrage du bot — `/connect` est requis à chaque démarrage. La durée d'une session BD varie selon les paramètres de sécurité de votre compte.

**Mode Autonome**
Le bot ne peut entrer en position que si Playwright est connecté et le marché du titre ouvert. Si la session expire, les positions existantes restent protégées par leurs ordres Expert sur BD, mais aucune nouvelle entrée n'est possible. Les exécutions (achats comme ventes SL/TP) sont détectées par le **sync horaire** qui lit les ordres exécutés sur BD — clôture enregistrée au prix réel d'exécution, jamais estimée sans preuve.

**Stabilité**
Pour un usage continu, activez `./bot.sh autostart` : démarrage au boot + relance automatique après crash.

---

## Stratégie de sélection — validée par la recherche académique

Depuis 07/2026, la sélection suit les résultats **répliqués** de la littérature financière — plus de chasse aux hausses du mois :

| Principe | Implémentation | Référence |
|---|---|---|
| Le momentum rentable se mesure sur **12 mois HORS dernier mois** ; les gagnants du dernier mois **s'inversent** | Classement par `mom_12_1`, plus jamais par momentum 1 mois | Jegadeesh & Titman 1993 (*JF*) ; Jegadeesh 1990 ; Lehmann 1990 |
| Filtre de tendance long terme | Achat uniquement si cours **> MM200** (titre ET indices — le régime passe en CORRECTION si CAC et S&P sont sous leur MM200, même avec un VIX calme) | Moskowitz, Ooi & Pedersen 2012 (*JFE*) |
| Pas d'entrée en surchauffe : on achète le **repli sain dans la tendance** | Zone d'entrée RSI 35-65, **veto quantitatif dur à RSI > 70** (indépendant de l'IA) | réversion court terme, Jegadeesh 1990 |
| Les stops aident les stratégies momentum s'ils sont hors du bruit | SL ≈ **2×ATR** sous l'entrée, borné 3-10% ; TP ≥ **1.5×** la distance du SL | Kaminski & Lo 2014 (*J. Financial Markets*) |
| Dimensionner par le **risque**, pas par le budget | Perte au SL = **1% du budget autonome** ; coût ≤ 30% du budget | fractional Kelly (MacLean, Thorp & Ziemba 2011) |
| Réduire la voilure quand la volatilité monte | Taille **÷2** si vol 20j > 1.5× la vol 1 an du titre + réduction en série de pertes | Barroso & Santa-Clara 2015 (*JFE*) ; Moreira & Muir 2017 |
| La diversification ne vaut que si les paris sont réellement décorrélés | Corrélation des rendements quotidiens (90j) vs positions détenues : **entrée bloquée** au-delà de 0.85, taille **÷2** entre 0.6 et 0.85 | Markowitz 1952 (*JF*) — un score quant indépendant ne suffit pas si les cours bougent ensemble |
| L'overtrading détruit la performance retail | Mode « gain réduit » (trades courts forcés) **désactivé par défaut** — zéro trade est un résultat acceptable | Barber & Odean 2000 (*JF*) ; Novy-Marx & Velikov 2016 |

L'IA reste dans la boucle comme **contrôle qualitatif symétrique** (news invalidante, OPA plafonnée, événement binaire imminent, illiquidité) — elle ne peut plus contourner les garde-fous quantitatifs, et aucune directive ne la pousse vers l'achat.

> Aucune stratégie ne gagne à tous les coups : les meilleures stratégies documentées gagnent 50-60% du temps et font leur performance sur l'asymétrie gain/perte et le contrôle du risque. Méfiez-vous de quiconque promet le contraire.

### Backtest (`backtest.py`)

`venv/bin/python3 backtest.py` rejoue le moteur quantitatif sur l'univers de scan (2023 → aujourd'hui, hypothèses pessimistes : SL prioritaire sur TP dans la même bougie, gaps exécutés à l'open, frais BD 1.98€/ordre). Enseignements de la campagne du 14/07/2026 :

- L'ancienne logique (momentum 1 mois, all-in) perd **-30%** sur la période — la refonte est justifiée.
- Le moteur 12-1 + MM200 a un edge réel (**+26% brut** sur 3.5 ans) mais les **frais fixes BD consomment tout** sur des positions de ~500€ → il faut **moins de trades, plus gros** (risque 2.5%, coût ≤ 50% du budget, max 2 positions).
- Le trailing breakeven à **+3% scratchait les futurs gagnants** (win rate 27%→34%, P&L ×9 en passant à +6%) → défaut désormais `AUTO_BREAKEVEN_PCT=6`.
- Config retenue : **+16% net de frais** sur 3.5 ans (PF 1.22, drawdown max -446€ sur 2000€) — sans compter l'étage IA (news/OPA/catalyseurs), non simulable.

**Validation (bootstrap + walk-forward)** — depuis le 15/07/2026, le script ajoute automatiquement un étage de robustesse sur les stratégies livrées (PHASE1, RECOVERY), inspiré des plateformes quant (validation-first) :

- **Bootstrap** (`--boot`, défaut 2000) : rééchantillonne les trades avec remise et recalcule la courbe d'équité à chaque tirage → **IC90%** sur le P&L total, le profit factor et le max drawdown, plus `P(stratégie gagnante)`. Un point unique positif peut être un coup de chance ; si l'IC du P&L inclut 0, l'edge n'est pas distinguable du bruit.
- **Walk-forward** (`--folds`, défaut 4) : découpe la période en fenêtres consécutives simulées indépendamment → révèle si l'edge tient **hors échantillon** ou repose sur un seul régime.
- `--no-validate` saute ce bloc (simulation seule).

⚠️ **Ce que la validation révèle (14/07/2026, univers complet)** : le P&L positif agrégé du moteur 12-1 **repose presque entièrement sur la fenêtre du rebond 2023** (PF ~5-7), puis stagne ou perd sur les 3 fenêtres suivantes (1/4 fenêtre gagnante). Le bootstrap donne `P(gagnante)` **< 10%** avec une médiane négative : distribution très asymétrique (beaucoup de petites pertes, quelques gros gagnants rares). Conclusion honnête : **l'edge quantitatif seul n'est pas robuste** — il dépend d'attraper de rares outliers en marché porteur, d'où l'importance critique de l'étage IA (news/catalyseurs) et du régime de marché comme filtres.

Limites : constituants actuels (biais du survivant, identique pour toutes les configs comparées), pas de FX, moteur quantitatif seul.

## Règles de trading par défaut

- **Stop-loss** : technique, ≈ 2×ATR sous l'entrée, borné 3-10% (fallback -7% fixe pour les positions manuelles sans ATR)
- **Take-profit** : ≥ 1.5× la distance du SL, +10% typique (l'IA peut viser plus haut si le potentiel le justifie)
- **Trailing stop** : SL relevé au PRU à +5% (positions manuelles) / +6% (mode autonome, `AUTO_BREAKEVEN_PCT`)
- **Taille autonome** : risque au SL = 1% du budget, coût ≤ 30% du budget · **Taille suggérée** (`/scan`) : 50% du cash, plafonné à 800€
- Modifiables dans `.env` :

```env
DEFAULT_SL_PCT=7          # stop-loss fixe fallback en % sous le PRU
DEFAULT_TP_PCT=10         # take-profit minimum en % au-dessus du PRU
BREAKEVEN_THRESHOLD=5     # trailing stop positions manuelles : % au-dessus du PRU
POSITION_BUDGET_PCT=50    # % du cash investi par nouvelle position (suggestions /scan)
POSITION_BUDGET_MAX=800   # plafond en € par position (à adapter à votre capital)

RSI_ENTRY_MIN=35          # zone d'entrée saine (pullback dans la tendance)
RSI_ENTRY_MAX=65          # au-delà : on attend le repli
RSI_HARD_MAX=70           # veto dur à l'achat, indépendant du verdict IA
ATR_SL_MULT=2.0           # distance SL = 2×ATR 14j
MIN_SL_PCT=3              # SL jamais plus serré (bruit du titre)
MAX_SL_PCT=10             # au-delà : titre trop volatil → exclu
MIN_RR=1.5                # TP ≥ 1.5× la distance du SL
EARNINGS_VETO_DAYS=6      # EXCLUS si résultats < N jours (gap non couvert par le SL) ; au-delà, non bloquant
RISK_PER_TRADE_PCT=1.0    # perte au SL en % du budget autonome
MAX_POSITION_PCT=30       # coût max d'une position en % du budget autonome
VOL_SCALE_TRIGGER=1.5     # vol 20j > 1.5× vol 1 an → taille réduite de moitié

BROKERAGE_FEE=1.98        # frais de courtage BD par ordre (aller-retour = 2×)
MIN_NET_GAIN_FEE_RATIO=5  # gain brut au TP requis : au moins N× les frais A/R

SMALL_GAIN_MODE=off       # trades courts forcés quand rien ne passe (déconseillé)
FALLBACK_TP_MIN_PCT=3     # mode gain réduit : TP minimum des trades courts
FALLBACK_TP_MAX_PCT=8     # mode gain réduit : TP maximum des trades courts

DASHBOARD_BIND=127.0.0.1  # 0.0.0.0 pour accès Tailscale/LAN (voir section Dashboard)
```

---

## Lancer en tâche de fond

```bash
./bot.sh start        # démarre en arrière-plan — le terminal peut être fermé
./bot.sh stop         # arrête le bot
./bot.sh restart      # redémarre (après une modif de .env par exemple)
./bot.sh status       # tourne ou pas ? + dernières lignes du log
./bot.sh logs         # logs en direct (Ctrl+C pour quitter)
./bot.sh update       # git pull + dépendances + redémarrage
```

### Démarrage automatique au boot (recommandé)

```bash
./bot.sh autostart      # installe un service launchd (macOS) ou systemd (Linux)
./bot.sh unautostart    # le désactive
```

Une fois l'autostart actif, plus besoin d'y penser : l'ordinateur redémarre → le bot revient. **Utilisez toujours `./bot.sh stop`/`restart`** (et non `pkill`) : le service relancerait automatiquement un bot tué à la main, ce qui peut créer une double instance.

> ⚠️ L'ordinateur doit rester **allumé et non suspendu** pour les checks de 9h/12h/15h/17h.

---

## Contribuer

Projet open-source, conçu pour être étendu. Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour le guide complet.

**Idées de contributions :**
- Support d'autres courtiers français (Boursorama, Fortuneo, Trade Republic...)
- Backtest simple sur données historiques Yahoo Finance
- Suite de tests automatisés (pytest)
- Authentification sur le dashboard web (pour exposition au-delà du tailnet)

```bash
git checkout -b feature/ma-feature
# ... commits ...
# ouvrez une Pull Request — template fourni
```

**Questions ?** Ouvrez une [Discussion](../../discussions) plutôt qu'une Issue.

---

## Avertissement

Ce bot est un outil d'aide à la décision. Il ne constitue pas un conseil en investissement.
Toutes les décisions d'achat/vente restent de votre responsabilité.
Les performances passées ne garantissent pas les performances futures.

---

*Python · yfinance · DuckDuckGo Search · API IA de votre choix*
