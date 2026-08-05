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
| **Analyses IA non gaspillées** | Scan US planifié et recherche de candidats du briefing **sautés quand aucun achat n'est possible** — cash sous le plancher de viabilité, ou mode autonome sans emplacement libre. Une ligne Telegram par jour explique pourquoi. `/scan` et `/research` restent toujours complets |
| **Frais BD au barème réel** | Courtage par tranches Euronext, forfait US, **TTF française 0,4 % à l'achat** et commission de change 0,08 % — vérifié au centime sur nos ordres exécutés. Conditionne le sizing, le veto de rentabilité et le plancher de scan |
| **Dashboard filtrable par période** | Menu ☰ : Global / ce mois / mois dernier / cette année / année dernière. Cartes, graphiques et tableau recalculés sur la période ; P&L latent et cash restent des instantanés globaux, signalés comme tels |
| **Trailing en 2 paliers** | **1.** À +5% (manuel) / +6% (autonome), le SL monte au PRU — perte impossible. **2.** Passé 60% du chemin vers le TP, le SL monte **au-dessus du PRU** et verrouille une part croissante du gain (50% → 80% au contact du TP). L'ordre Expert est remplacé sur BD à chaque palier |
| **Contrôle de protection** | À chaque sync, toute position gérée est comparée au carnet BD. Sans ordre SL/TP actif → alerte (même en sync silencieux), marquage `🚨 non protégé` dans `/status` et le dashboard, commande de replacement fournie. Un stop calculé mais non posé sur BD est affiché comme tel, jamais comme actif |
| **Ordres Expert réels** | `/ordre acheter TTE.PA 3 expert 54.2 49.0 61.0` — achat+SL+TP en un seul ordre, envoyé à BD (Euronext + marchés US) |
| **Validité des ordres** | Par séance, max (fin d'année Euronext / fin de mois US), ou date précise JJ/MM/AAAA |
| **Mode Autonome** | Budget isolé géré en totale autonomie : scan → entrée → SL au PRU à +6% → sortie détectée → réinvestissement. Ordres d'entrée non exécutés à la clôture : annulés auto (anti-sélection) |
| **Positions HOLD long terme** | `/hold TICKER` : sortie du périmètre bot (pas d'alertes, hors P&L trading, jamais proposée à la vente) |
| **Sélection momentum validée** | Momentum 12 mois (hors dernier mois) + cours > MM200 + entrée sur repli sain (RSI 35-65) — voir [Stratégie](#stratégie-de-sélection--validée-par-la-recherche-académique) |
| **Sizing par le risque** | Perte au SL = 1% du budget autonome, SL ≈ 2×ATR, taille réduite si volatilité élevée, série de pertes, ou corrélation forte avec une position déjà détenue (entrée bloquée au-delà de 0.85) |
| **Mode gain réduit** (opt-in) | Si rien ne passe à +10%, trades courts (TP +3-8%, 1-5 jours) — désactivé par défaut (`SMALL_GAIN_MODE=on` pour l'activer) |
| **Dashboard visuel** | http://localhost:8642 (accès Tailscale possible) + `/dashboard` Telegram : P&L cumulé, cash engagé, ROI, trades filtrables |
| **Coûts API dans le bilan** | Chaque appel IA enregistre ses tokens réels ET **le modèle qui a réellement répondu** (`api_costs.json`) ; `/stats` et le dashboard affichent le coût cumulé, le modèle servi et le **P&L net après coûts IA** — bilan honnête de l'efficacité du bot |
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

**Scheduler :** `schedule` (Python) — 4 checks SL/TP/jour + briefing 9h05. À chaque check, `autonomous_engine` est invoqué pour surveiller les positions autonomes et tenter de nouvelles entrées si Playwright est connecté. **Séance US** (`US_EXTENDED_HOURS=on`, défaut) : checks positions/ordres **US uniquement** à `US_CHECK_TIMES` (18h/20h/21h40, alertes seules — silencieux sans position US) + scan US à `US_SCAN_TIME` (16h). Les entrées/trailing autonomes, eux, tournent déjà chaque heure jusqu'à 22h via le sync horaire. **Garde-fou de capacité** : avant toute analyse IA planifiée (scan US, candidats du briefing), `autonomous_engine.entry_capacity_block()` vérifie qu'une entrée est structurellement possible — place libre et budget suffisant — sinon le travail coûteux est sauté.

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
| `/reticker POSITION TICKER` | **Corriger le ticker Yahoo** d'une position sans la recréer (garde flag autonome, PRU brut BD, contexte d'entrée). Le nouveau ticker est refusé s'il ne cote pas |
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
- **Balayage du reliquat de cash** — si le cash restant après l'achat tombe sous `CASH_SWEEP_MIN_LEFTOVER` (500 €), la position est agrandie pour l'absorber : ce fond ne pouvait financer aucun autre trade. ⚠️ Ce mécanisme **prime sur le plafond de taille** et augmente donc la perte au SL dans la même proportion — c'est un arbitrage assumé entre capital déployé et respect strict du sizing par le risque. La nouvelle perte au SL est annoncée dans le message d'achat ; `CASH_SWEEP_MIN_LEFTOVER=0` désactive
- **Aucune analyse IA quand toutes les places sont prises** — dès que les emplacements autonomes sont occupés (positions + ordres d'achat en attente), les analyses IA *planifiées* sont sautées : scan US de 16h et recherche de candidats du briefing. Elles ne pourraient produire que des opportunités inachetables. Le bot le dit une fois par jour dans Telegram, plutôt que de rester silencieux
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

## Trailing stop — deux paliers

Le trailing ne fait pas que protéger le capital : il sécurise aussi le gain
déjà acquis à mesure que le cours approche du TP.

**Palier 1 — BREAKEVEN.** Le cours dépasse +5% (manuel) / +6% (`AUTO_BREAKEVEN_PCT`,
autonome) → le SL monte **au PRU**. La position ne peut plus perdre.

**Palier 2 — SÉCURISATION DU GAIN.** Le cours a parcouru au moins
`TRAIL_LOCK_TRIGGER_PCT` (60%) du chemin PRU→TP → le SL monte **au-dessus du
PRU**, à une fraction du gain acquis. Cette fraction **grandit avec la
progression** : `TRAIL_LOCK_MIN_RATIO` (50%) au déclenchement,
`TRAIL_LOCK_MAX_RATIO` (80%) au contact du TP. Plus le TP est proche, moins il
reste de raisons de laisser filer ce qui est déjà gagné.

Le palier le plus haut l'emporte. Sur un TP étroit, le palier 2 se déclenche
**avant** le palier 1 : une position à +3% d'un TP à +5% a déjà fait 60% du
chemin et mérite un stop au-dessus du PRU, alors que le breakeven à +6% ne
serait jamais atteint.

Exemple — PRU 100 €, TP 110 €, ATR 1,5% :

| Cours | Gain | Chemin vers le TP | Palier | SL posé | Marge sous le cours |
|---|---|---|---|---|---|
| 104 € | +4% | 40% | — | — | — |
| 106 € | +6% | 60% | sécurisation | 103,00 € (**+3,0%**) | 2,8% |
| 108 € | +8% | 80% | sécurisation | 105,20 € (**+5,2%**) | 2,6% |
| 109,5 € | +9,5% | 95% | sécurisation | 107,24 € (**+7,2%**) | 2,1% |

Deux garde-fous, tous deux appris d'incidents réels :

- **Marge de respiration** — le SL sécurisé reste toujours à au moins
  `TRAIL_MIN_BUFFER_PCT` (2%) **ou 1×ATR** sous le cours, le plus large des
  deux. Un stop collé au cours se ferait sortir par le bruit ordinaire juste
  avant le TP — exactement ce que ce palier cherche à éviter. Sur un titre à
  ATR 5%, la marge s'élargit automatiquement.
- **Pas minimal** — chaque remontée **annule les 2 ordres BD et en repose un**,
  fenêtre pendant laquelle la position est à nu (incident UNA du 28/07/2026).
  Le SL ne bouge donc que s'il gagne au moins `TRAIL_MIN_STEP_PCT` (1% du PRU) :
  ratcheter pour 0,2% n'en vaut pas le risque.

⚠️ **Le trailing ne peut remonter qu'un stop posé en ordre de VENTE** (visible au carnet legacy, avec une référence annulable). Une protection portée par un Expert d'**achat** reste active sur BD mais hors de portée du bot : il n'a rien à annuler, donc rien à replacer. `/trailing` le signale au lieu de la croire absente.

`trailing_target()` est la **source unique** des deux paliers — même calcul que
la session BD soit connectée (l'ordre est remplacé automatiquement) ou non
(alerte Telegram avec la commande `/ordre` prête à coller).

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

- **Filtre de période (menu ☰)** : **Global**, **Ce mois-ci**, **Le mois dernier**, **Cette année**, **L'année dernière**. Cartes, les deux graphiques et le tableau sont recalculés sur la période choisie — y compris les coûts API, jour par jour.
  - Le **P&L cumulé est recalculé** sur la période (il repart de 0), pas simplement tronqué
  - Le **€/jour** se base sur la durée réellement écoulée, bornée à aujourd'hui
  - **P&L latent et cash restent globaux** : ce sont des instantanés, pas des flux — le libellé le précise, et le « P&L total » ne les ajoute qu'en vue Global
- **Cartes de synthèse** : P&L réalisé / latent / total, win rate, profit factor, cash, performance en €/jour, ROI sur cash engagé
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

> 💡 **Ne notez jamais le lien.** Tailscale renomme (`yok` → `yok-2` → `yok-3`) et ré-adresse la machine à chaque réinstallation ou mise à jour. `/dashboard` recalcule l'URL à chaque appel, et le bot vous prévient sur Telegram au démarrage si elle a changé.

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

### 2026-08-05 (5) — Le lien du dashboard se recalcule au lieu d'être recopié
Tailscale crée un **nœud dupliqué** à chaque réinstallation ou mise à jour : la machine `yok` devient `yok-2`, puis `yok-3`, et **l'IP du tailnet change avec elle**. Tout lien noté quelque part devient donc faux sans prévenir — constaté ce jour : le lien mémorisé pointait encore sur `100.65.97.62` alors que la machine était passée à `100.108.53.48`.

Le bot est le seul à savoir où il est joignable : c'est désormais lui qui le dit.
- **`dashboard.access_urls()`** interroge `tailscale status --json` et construit les URLs (nom tailnet, IP tailnet, local) **avec le jeton**, à chaque appel — rien n'est mémorisé
- **`/dashboard` affiche le lien courant** sous le graphique
- **Au démarrage, le bot compare et prévient sur Telegram si l'adresse a changé** — plus besoin de la demander : elle arrive toute seule quand elle bouge
- **`dashboard-link.local.txt` est régénéré** à chaque lancement au lieu d'être un fichier écrit à la main
- **Piège corrigé au passage** : le bot tourne sous launchd, dont le `PATH` ne contient pas `/usr/local/bin`. Un simple `shutil.which("tailscale")` échouait et le lien tailnet disparaissait du démarrage sans un mot ; les emplacements connus sont maintenant essayés dans l'ordre

### 2026-08-05 (4) — La protection d'un Expert d'achat est annulable : il manquait l'id
Capture réseau d'une annulation manuelle (`/capture` élargi à toute requête modifiante) :

```
POST /hub/trading/order/cancel   {"order_id":"0a7d399c-fd78-49b6-8fe9-8b3ba0f6aedd"}
→ 200  "ordre 0a7d399c… en cours d'annulation"
```

**C'est exactement l'endpoint que le bot utilisait déjà.** Le blocage n'était pas l'API mais l'identifiant : `0a7d399c` n'est pas `d57ffcb4`, l'id du parent affiché dans la modal et le seul que le lecteur capturait. La conclusion « protection non annulable » était donc fausse — il manquait un id, pas une capacité.

**D'où vient le bon id** : la réponse de `/order/create` d'un Expert renvoie `children` — les ids des deux jambes SL et TP. C'est la **seule** occasion de les obtenir : une fois l'achat exécuté, ni la page portefeuille (qui n'expose que le parent) ni le carnet legacy (qui ignore les protections d'achat) ne les montrent.

- **`children` est désormais capturé et persisté** à la création de l'ordre, transporté par le sync jusqu'à la position (`protection_ids`)
- **Le trailing sait s'en servir** : plus de jambe au carnet mais des ids connus → il annule chaque jambe via `/order/cancel`, **attend et vérifie** que la protection a disparu (BD répond « en cours d'annulation » — c'est asynchrone), et ne repose qu'ensuite. Annulation non confirmée → aucun ordre posé, l'ancienne protection reste active
- **Conséquence** : une position achetée en Expert autonome devient remontable comme les autres. Le cas NVDA — stop figé à −8,6 % du PRU pendant que le palier 2 en visait +4,1 % — ne peut plus se reproduire

Les trois positions actuelles n'ont pas d'`protection_ids` (elles précèdent ce correctif) ; NVDA a été réparé en remplaçant sa protection par un ordre de vente, désormais visible au carnet et trailé normalement.

### 2026-08-05 (3) — Pourquoi le trailing ne peut pas remonter NVDA : la preuve
Question posée : « si le sync voit les SL/TP, le trailing doit pouvoir les annuler par le même chemin, non ? » L'intuition était juste — la page portefeuille **expose bien des ids annulables**, contrairement à ce qu'affirmait une note interne. C'est exactement ainsi que le trailing a annulé AIR. Le log des sous-ordres, rendu systématique pour trancher, donne le verdict :

```
AIR  4b07d823-… | Vente(CPT) 0/5  … Seuil 209.70 € En cours     → annulable
BAC  00e7bd95-… | Vente(CPT) 0/12 … Seuil 58.93 $ En cours      → annulable
NVDA d57ffcb4-… | Achat(CPT) Ordre exécuté 7/7 … Seuil 187.40 $ → PARENT
```

**NVDA n'expose qu'un seul id : celui de l'ordre d'ACHAT exécuté.** Sa protection est rendue dans le même nœud DOM, sans identifiant propre, et BD refuse d'annuler un ordre exécuté (403 légitime). La limite est côté Bourse Direct, pas côté bot.

- **Discriminant encodé** : une protection est *remontable* si l'un de ses sous-ordres porte un texte `Vente` + `En cours` **sans** `Ordre exécuté`. Le sync pose le drapeau `trailable` à chaque passage, et liste les protections non remontables dans son compte rendu
- **`/trailing` donne le palier visé et le gain qu'il verrouillerait**, avec la commande exacte à passer après annulation manuelle — au lieu de constater l'impasse
- **Origine du cas** : une position achetée en **Expert d'achat** (entrée + SL + TP atomiques) hérite d'une protection soudée au parent. C'est plus sûr à l'entrée — aucune fenêtre sans protection — mais non remontable ensuite. Les protections posées séparément en **ordre de vente** (trailing, `/ordre vendre`) restent gérables

### 2026-08-05 (2) — « Absent du carnet » ≠ « sans protection » (fausse alerte NVDA)
Le sync voyait NVDA protégé (`Achat Take Profit SL 187.40$ | TP 225.00$ · En cours`) pendant que `/trailing` annonçait « toujours aucune protection au carnet ». Fausse alerte, introduite le jour même.

**Les deux pages BD sont complémentaires, pas redondantes** — le log du carnet legacy le prouve : il ne contient *que* les deux jambes de vente d'AIR.

| Source | Voit | Identifiant annulable |
|---|---|---|
| Page portefeuille (sync) | **toutes** les protections actives, y compris celles portées par un Expert d'**achat** exécuté | ❌ |
| Carnet legacy (trailing) | uniquement les ordres de **vente** autonomes | ✅ |

NVDA est protégé par son Expert d'achat : il n'apparaît donc pas au carnet. En déduire « position à nu » était faux. Le trailing **corrobore désormais avec le drapeau `protected` du sync** avant toute alerte :
- absent du carnet **mais** protégé selon le sync → *protection active, hors de portée du bot* (rien à annuler, donc rien à remonter) — dit tel quel, avec la marche à suivre manuelle
- absent des **deux** sources → la position est vraiment à nu, alerte

⚠️ **Limite réelle, désormais annoncée dans l'en-tête de `/trailing`** : le bot ne peut remonter que les protections posées en **ordre de vente**. Une position achetée en Expert autonome garde ses SL/TP d'origine jusqu'à ce qu'ils soient remplacés à la main sur BD.

### 2026-08-05 — Le bot affichait des SL/TP qui ne protégeaient rien
Signalé par l'utilisateur : le `/status` montrait `SL $58.93 — TP $67.53` pour BAC alors que le carnet BD ne contenait **aucun ordre** pour cette valeur. Trois défauts distincts, tous du même genre — présenter une valeur mémorisée comme un fait vérifié.

**1. Aucun contrôle de protection.** Le sync mettait à jour les SL/TP *depuis* les ordres actifs, mais ne regardait jamais l'inverse : une position gérée **sans aucun ordre de protection** ne déclenchait rien. BAC est resté à nu du **31/07 au 05/08** sans un mot. Cause : l'Expert d'ACHAT qui portait ses protections avait une validité au 31/07 22h ; en expirant il les a emportées. Le sync compare désormais chaque position gérée au carnet, pose un drapeau `protected`, et **rompt le silence du sync horaire** quand une position perd sa protection.

**2. Le trailing ne reconnaissait plus ses propres ordres — et se taisait.** `find_stop_loss_order` identifiait le stop comme « la vente sous le PRU ». Le palier 2 du trailing (livré le 03/08) remonte précisément le stop **au-dessus du PRU** : dès le premier palier, les deux jambes se retrouvent au-dessus, plus rien n'est identifiable, et la position est sautée **en silence**. AIR et NVDA étaient figés depuis leur premier palier.
- Le discriminant est maintenant la position **relative** des deux jambes — la plus basse est le stop, la plus haute la cible — quel que soit le PRU
- Le cas « ni SL ni TP au carnet » n'est plus un `continue` muet : il alerte une fois par position, avec la commande de replacement prête

**3. Un SL calculé n'est plus écrit comme un SL actif.** En session BD déconnectée, le trailing écrivait le nouveau stop dans `target_low` et affichait un ordre à passer à la main — donc `/status` annonçait un stop que BD n'avait jamais reçu (AIR affiché à 209.68 quand BD tenait 205.25). Le stop souhaité part désormais dans `pending_sl` ; **`target_low` ne dit que ce que BD exécutera vraiment**, et l'écart est affiché : `⏳ SL 209.68 calculé mais PAS posé sur BD — le stop actif reste 205.25`.

**Affichage** : `/status`, le STATUS planifié et le dashboard marquent une position sans protection (`🚨 AUCUN ordre SL/TP actif sur BD`, badge `non protégé`) au lieu d'imprimer ses seuils comme s'ils étaient actifs.

⚠️ **Aucun ordre n'est replacé automatiquement.** Deux sources BD décrivent les protections différemment (page portefeuille vs carnet legacy) ; poster un Expert de vente sur une lecture erronée créerait un **doublon de vente**. Le bot détecte, alerte et fournit la commande — le placement reste un geste explicite.

### 2026-08-04 — Dashboard : filtre de période + audit des coûts API
**Filtre de période** — menu ☰ en haut à droite : **Global / Ce mois-ci / Le mois dernier / Cette année / L'année dernière**. Cartes, courbe du P&L cumulé, P&L par trade et tableau sont **tous recalculés côté client** sur la période choisie — laisser une seule valeur figée côté serveur l'aurait rendue fausse dès la première sélection.
- **Le cumul est recalculé, pas tronqué** : réutiliser le cumul global ferait démarrer la courbe au niveau hérité des trades précédents. Sur juillet elle part bien de 0
- **€/jour borné à aujourd'hui** : diviser une période en cours par sa durée nominale gonflerait le rythme
- **P&L latent et cash restent globaux** — ce sont des instantanés, ils n'appartiennent à aucune période ; le libellé le dit, et le « P&L total » ne les additionne qu'en vue Global
- **Bug corrigé pendant la mise au point** : les bornes passaient par `toISOString()`, qui convertit en UTC — minuit à Paris devient 22h la veille. Juillet perdait le 31 et récupérait le 30 juin (coûts API affichés 0,08 € au lieu de 0,10 €). Les bornes sont désormais construites en heure locale

**Audit des coûts API — le bot tourne sur le fallback Gemini depuis le 20/07.** En recoupant les tokens enregistrés avec les tarifs, **chaque appel depuis le 20/07/2026 correspond exactement au tarif Gemini** : le crédit Anthropic s'est épuisé le lendemain du démarrage du suivi, et **aucun appel Anthropic n'a été servi depuis**. Toutes les décisions de trading depuis cette date viennent du modèle de secours, pas de Sonnet — et rien, ni dans `/stats` ni dans le dashboard, ne le disait.
- **Le modèle réellement servi est désormais enregistré** (`msg.model` côté Anthropic, `model_version` côté Gemini) et non l'alias demandé : `gemini-flash-latest` est un alias evergreen, le facturer sur son nom revient à parier sur ce qu'il désigne. Ventilation par modèle conservée jour par jour, affichée dans `/stats` et sur la carte du dashboard
- **Tarif Gemini Flash corrigé** : la table facturait `flash` à 0,30/2,50 $/M — c'est le tarif Flash-**Lite**. Le Flash courant est à 1,50/7,50 $/M, soit une **sous-estimation d'un facteur 5 en entrée** sur toute la période concernée
- **Tarif Opus corrigé** : 15/75 $/M était l'ancien tarif Opus 3/4 ; les Opus actuels sont à **5/25 $/M** (surestimation de 3× si le bot y repasse)
- **Modèle inconnu → tarif haut** (10/50 $/M) et trace dans les logs, au lieu du tarif Sonnet : sous-estimer une facture qu'on ne sait pas lire donne un bilan flatteur et faux
- **Tokens de cache comptés** (écriture 1,25×, lecture 0,1×) : nuls aujourd'hui, mais les ignorer ferait disparaître l'essentiel de la facture le jour où le prompt caching serait activé
- **L'historique n'a PAS été réécrit** : la provenance d'avant le 04/08 est inférée, pas mesurée. Réécrire le journal avec une déduction la transformerait en fait. Les totaux antérieurs restent donc sous-estimés — c'est signalé plutôt que corrigé en silence

### 2026-08-04 — yfinance servait des cours vieux de 2 à 3 séances, sans le dire
- **Symptôme** : le `/status` de 9h annonçait AIR à 208,00 € et NVDA à 200,75 $ quand Bourse Direct affichait **211,40 €** et **206,64 $**. P&L faux sur les trois positions actives (NVDA donné perdant à -2,13 % alors qu'il était gagnant à +0,92 %)
- **Cause** : yfinance a renvoyé **toute la séance du 03/08 en NaN**. `get_quote` supprimait les lignes vides et servait la dernière barre valide — celle du **31/07** — en `status: ok` avec `change_pct: 0.0 %`. Rien, nulle part, ne signalait que la donnée avait deux séances de retard. Sur AIR.PA le retard atteignait **trois séances**
- **Conséquence silencieuse la plus grave** : les alertes SL/TP, le trailing et le briefing IA raisonnaient tous sur ces cours morts. Le palier 2 du trailing ne s'est pas déclenché sur AIR alors que le vrai cours (211,40 €) plaçait la position à **72 % du chemin vers le TP** — largement au-dessus du seuil de 60 %. Avec le cours périmé, le bot calculait 55 % et ne faisait rien
- **`get_quote` date désormais ce qu'il renvoie** : `as_of` (date de la barre), `stale_days` (séances de retard) et `status: "stale"` au-delà d'une séance. Une donnée périmée reste utilisable, mais elle est **annoncée comme telle**
- **`portfolio.best_price()`** — cascade unique pour toute position détenue : yfinance frais → **relevé Bourse Direct** → yfinance périmé faute de mieux. BD passe avant un yfinance périmé parce que le sync horaire le rafraîchit et que c'est le cours du courtier chez qui la position est réellement détenue — celui qui déclenchera le SL. Un relevé BD plus vieux que la barre yfinance (session Playwright déconnectée depuis des jours) ne le remplace pas
- **La provenance est affichée quand ce n'est pas yfinance** : `⚠️ cours Bourse Direct — yfinance périmé (2026-07-31)`. Un chiffre de repli qui ne se présente pas comme tel est exactement ce qui a produit ce bug
- **Le relevé BD est horodaté** (`bd_price_at`) : sans date, impossible de savoir s'il vient du sync de l'heure passée ou d'une semaine sans connexion
- **Le range intraday intègre le cours retenu** (`max`/`min`) : un range issu d'une séance périmée pouvait ignorer le cours réel et laisser passer un franchissement de SL
- Branché sur le `/status`, les alertes SL/TP, le trailing (les deux paliers) et le snapshot envoyé à l'IA

### 2026-08-03 — Bug : NVDA enregistré en `NVDA.PA`, position invisible du suivi
- **Symptôme** : `NVDA: COURS SUSPENDU — non vendable` dans le `/status`, sur une position de 1 233 € achetée et exécutée normalement une heure plus tôt
- **Cause** : `sync_engine` reconstruit le ticker Yahoo depuis le code place (MIC) renvoyé par BD. **`XNGS`** — NASDAQ Global Select, la place que BD renvoie réellement pour NVDA — **manquait dans la table des suffixes**, et le défaut de cette table est « Paris ». D'où `NVDA` + `.PA`. Yahoo ne connaît pas `NVDA.PA` → aucun cours → aucune alerte SL/TP, aucun trailing : **la position n'était plus surveillée du tout**, et le seul signal envoyé était faux
- **Le vrai défaut est structurel** : il y avait DEUX tables de places, une pour les suffixes et une pour les devises, et elles avaient dérivé — `XNGS` figurait bien dans la table des devises (le PRU en USD a été lu correctement). Elles sont désormais **fusionnées en une seule** (`MIC_MARKETS`), une ligne portant les deux informations : elles ne peuvent plus se contredire. Les compartiments US manquants sont ajoutés (`XNGS`, `XNMS`, `XNCM`, `ARCX`, `XASE`, `BATS`), ainsi que Milan, Madrid, Lisbonne et la Suisse
- **Plus de repli silencieux sur « .PA »** : une place inconnue est tranchée par la devise cotée par BD (USD ⇒ US ⇒ aucun suffixe) et **tracée dans les logs** pour être ajoutée, plutôt que subie une seconde fois
- **Le ticker de l'ordre autonome prime désormais** sur la reconstruction : quand le bot a lui-même passé l'ordre, il connaît déjà le bon ticker — validé chez yfinance avant l'achat. Le reconstruire depuis le MIC ne pouvait que faire pire
- **« COURS SUSPENDU » n'était pas un diagnostic mais une supposition.** Le discriminant existe pourtant déjà dans les données : **si BD cote le titre, il n'est pas suspendu**. `portfolio.quote_problem()` distingue maintenant trois cas — ticker faux (BD cote, Yahoo non) / vraie suspension / Yahoo indisponible — et le message dit ce qui compte vraiment : *« Position NON SUIVIE (ni SL, ni TP) tant que le ticker n'est pas corrigé »*. Appliqué au `/status`, au `/positions` et au snapshot envoyé à l'IA
- **Nouvelle commande `/reticker POSITION TICKER`** : corrige un ticker sans recréer la position. `/remove` + `/add` aurait perdu le flag autonome, le PRU brut BD, le contexte d'entrée et les compteurs de notification. Le nouveau ticker est **vérifié chez Yahoo avant écriture** — remplacer un ticker faux par un autre laisserait la position tout aussi aveugle
- Position NVDA réparée (`NVDA.PA` → `NVDA`), suivi SL/TP rétabli

### 2026-08-03 — Trailing : second palier qui sécurise le gain
- **Le breakeven ne protégeait que le capital.** Une position montée à +9% sur un TP à +10% pouvait redescendre au PRU et sortir **à zéro, tout le gain rendu** — le SL restait collé au PRU quelle que soit la distance parcourue vers le TP
- **Nouveau palier 2 — SÉCURISATION** : passé `TRAIL_LOCK_TRIGGER_PCT` (60%) du chemin PRU→TP, le SL monte **au-dessus du PRU**, à une fraction du gain acquis qui **grandit avec la progression** (`TRAIL_LOCK_MIN_RATIO` 50% au déclenchement → `TRAIL_LOCK_MAX_RATIO` 80% au contact du TP). Sur un PRU de 100 € et un TP de 110 € : SL à 103 € au cours de 106, à 105,20 € au cours de 108, à 107,24 € au cours de 109,50
- **Sur un TP étroit le palier 2 passe AVANT le palier 1** : à +3% d'un TP à +5%, 60% du chemin est fait et le stop monte au-dessus du PRU — alors que le breakeven à +6% ne serait jamais atteint sur ce trade. Ces positions n'avaient jusqu'ici **aucun** trailing
- **Marge de respiration obligatoire** : le SL sécurisé reste à au moins 2% **ou 1×ATR** sous le cours (le plus large). Un stop collé au cours se ferait sortir par le bruit juste avant le TP — sur un titre à ATR 5%, la marge s'élargit d'elle-même
- **Anti-churn** : chaque remontée annule les 2 ordres BD et en repose un, fenêtre pendant laquelle la position est à nu (incident UNA du 28/07). Le SL ne bouge que s'il gagne au moins 1% du PRU (`TRAIL_MIN_STEP_PCT`)
- **`trailing_target()` = source unique** des deux paliers, partagée par le trailing réel sur BD et par l'alerte en mode déconnecté — qui, elle, ne connaissait que le breakeven

### 2026-08-02 — Balayage du reliquat de cash + plafond de position à 1 000 €
- **`POSITION_BUDGET_MAX` passe de 800 € à 1 000 €.** Effet de bord voulu : le plancher de viabilité US étant à 930 €, le scan US n'est plus bloqué par construction (voir l'entrée frais ci-dessous)
- **Nouveau : `CASH_SWEEP_MIN_LEFTOVER` (défaut 500 €).** Si le cash restant APRÈS l'achat prévu tombe sous ce seuil, la position est agrandie pour l'absorber, frais inclus. Motif : un fond de portefeuille trop petit pour financer un second trade ne travaille pas, il attend. Exemple mesuré (GLE à 81 €) : à 900 € disponibles le moteur achetait 11 titres et laissait 9 € dormir ; à 1 200 € il achetait 5 titres d'Airbus et laissait **218 €** — désormais porté à 6 titres, 21 € de reliquat
- **Le balayage prime volontairement sur `MAX_POSITION_PCT`** — sans ça il serait sans effet dès que le plafond de taille est la contrainte active. **La contrepartie est réelle** : la perte au SL grandit dans la même proportion, et le sizing par le risque (`RISK_PER_TRADE_PCT`) n'est plus respecté sur un trade balayé. Le message d'achat annonce la nouvelle perte au SL en clair (`risque au SL 78 € au lieu de 65 €`), et `0` désactive le mécanisme
- **Pas de balayage quand le reliquat reste utile** : à 1 800 € disponibles, 577 € restent après l'achat — au-dessus du seuil, donc intacts, ils peuvent financer un autre trade
- **Le scan dimensionne sur la même enveloppe que le moteur** (budget autonome libre plafonné au cash réel, plus le cash total) : sinon l'affichage annonçait un balayage différent de celui réellement appliqué à l'ordre

### 2026-08-02 — Frais BD : barème réel, vérifié au centime sur nos ordres
Le forfait unique de 1,98 €/ordre a été remplacé le matin même par 1,98 € Euronext / 8,50 € « US et étranger ». Le tarif US était juste ; le reste ne l'était pas. Contrôle sur les tarifs publics BD **et** sur le PRU de nos propres positions (le PRU BD inclut tous les frais — la différence avec le montant exécuté les donne exactement) :

| Ordre | Montant | Frais réels | Ancien modèle | Nouveau |
|---|---|---|---|---|
| AIR 5 × 196,52 € | 982,60 € | 1,90 € | 1,98 € | **1,90 €** |
| GLE 12 × 75,55 € | 906,60 € | 5,53 € | 1,98 € | **5,53 €** |
| BAC 12 × 61,43 $ | 647,76 € | 9,03 € | 8,50 € | **9,02 €** |

- **La TTF française manquait — et c'est le plus gros poste sur une valeur française.** 0,4 % du montant (0,3 % avant le 01/04/2025), **à l'achat uniquement**, sociétés au siège en France capitalisant plus de 1 Md€. Sur GLE elle coûte **3,63 €, soit presque le double du courtage**. Ni la place ni le suffixe ne la déterminent : Airbus cote à Paris mais son siège est néerlandais (exonérée — nos 1,90 € de frais réels le prouvent), Genfit est française mais sous le milliard (exonérée aussi). Classement par `country` + `marketCap` yfinance, cache disque 30 j ; donnée manquante = **considéré assujetti** (surestimer les frais fait renoncer à un trade marginal, les sous-estimer fait entrer dans un trade qui ne se rembourse pas)
- **Le courtage Euronext est par tranches**, pas forfaitaire : 0,99 € < 500 € · 1,90 € < 1 000 € · 2,90 € < 2 000 € · 3,80 € < 4 400 € · 0,09 % au-delà. Les frais enregistrés dans l'historique le confirment un par un (AL2SI 1 130 € → 2,90 € · GNFT 851 € → 1,90 € · LBIRD 1 174 € → 2,90 €)
- **La commission de change (0,08 % par opération) manquait** : c'est elle qui complète les 8,50 € de courtage US pour retomber sur les 9,03 € réellement payés sur BAC
- **Les places non-US étaient tarifées comme les US** (8,50 €) alors que Londres et Xetra coûtent **0,15 % avec un minimum de 15 €**, Madrid/Suisse/Lisbonne 0,20 % min 18 €, et les autres marchés (dont Milan) **0,48 % min 41,90 €** — soit 5× le tarif annoncé. Latent aujourd'hui (l'univers de scan est Euronext + US), faux dès qu'on l'élargit
- **Conséquence directe sur le plancher de scan** : `min_viable_cash()` passe de 198 € partout à **130 € sur Euronext** (100 € hors TTF) et **930 € aux US**. Le forfait de 1,98 € avait fait sauter 5 scans Euronext entre le 17 et le 29/07 à 154 € de cash — alors que 154 € suffisait. À l'inverse, aucun achat US ne peut passer le seuil 5× tant que `POSITION_BUDGET_MAX` reste sous 930 € : le scan US le dit maintenant explicitement (voir « Frais Bourse Direct — barème réel »)
- `config.order_fees()` / `roundtrip_fee()` / `min_viable_amount()` prennent le **montant** de l'ordre, plus seulement le ticker ; scan, gain réduit, achat autonome et backtest y sont branchés, et le détail des frais est affiché (`courtage 3,80€ + TTF 3,63€`)

### 2026-07-31 — Analyses IA automatiques sautées quand aucun achat n'est possible
- **Le scan US planifié (16h) ne regardait que le cash, jamais les emplacements libres.** Constaté le 31/07 : 3 positions autonomes sur 3 places, cash 495 € — le scan a quand même lancé **8 validations IA** (~4 min), dont une opportunité (`GOOGL`) que le moteur ne pouvait pas acheter et qui est restée en attente. Le log dit tout : `[Auto] Max positions atteint (3 + 0 / 3)` puis, ligne suivante, `[scan] validation 1/8`
- **Nouveau garde-fou commun : `autonomous_engine.entry_capacity_block()`** — blocage *structurel* d'une entrée autonome (plus d'emplacement libre, ou budget/cash sous le plancher de viabilité). **Aucun appel réseau, aucun état transitoire** : ni session BD (elle se reconnecte) ni cours. Il répond `None` si le mode autonome est désactivé — c'est alors à l'utilisateur de décider quoi faire d'une opportunité, et rien n'est sauté
- **Scan US 16h** : sauté aussi quand toutes les places sont prises (en plus du plancher de cash existant). **Le briefing 9h05** : la recherche de candidats (passe 1 « ===CANDIDATS=== » + jusqu'à 10 validations) est sautée de la même façon — **l'analyse portefeuille, elle, reste faite** : c'est le rapport quotidien, il ne dépend pas d'une capacité d'achat
- **Le silence devient un message** : un scan sauté envoie désormais **une** ligne Telegram par jour (`⏭️ Scan US 🇺🇸 sauté — 3/3 emplacements occupés (AIR, GLE, BAC)`), le briefing affiche la raison à la place des opportunités. Sans ça, l'absence de scan est indiscernable d'une panne du scheduler (incident du 21-23/07)
- **`/scan`, `/scan us` et `/research` ne sont jamais concernés** : une demande explicite répond toujours, complète

### 2026-07-30 — Veto d'extension : testé, **réfuté**, non livré
Après la sortie au SL de JNJ (acheté au 5ᵉ jour d'une hausse de +4,3 %, au plus haut historique), hypothèse à tester : refuser un candidat dont le mouvement des 5 dernières séances est déjà tendu. Mesuré en **ATR quotidiens** (`ext_5d_atr`), seuils 1,0 à 3,0, sur 2023-2026, bootstrap 3000× et walk-forward 4 fenêtres.

| Seuil | Euronext (137 valeurs) | US (36 valeurs) |
|---|---|---|
| sans veto (référence) | -285 € · P(gagnante) **10 %** | -53 € · P(gagnante) **40 %** |
| ≤ 1,0 ATR | **-134 € · 28 %** (meilleur) | **-273 € · 12 %** (pire) |
| ≤ 2,0 ATR | -403 € · 4 % (pire) | -19 € · 47 % (meilleur) |

- **Le meilleur seuil sur un marché est le pire sur l'autre** : le signe s'inverse d'un univers à l'autre, c'est du bruit ajusté a posteriori. **Le filtre n'est pas activé.** Le code de mesure reste dans `backtest.py` (`--ext`, désactivé par défaut) pour que la conclusion soit rejouable
- JNJ était à **1,98 ATR** : aucun seuil défendable ne l'aurait bloqué. L'intuition était plausible, les données ne la soutiennent pas
- **Piège rencontré en route** : `backtest.py` figeait le trailing breakeven à **+3 %** alors que la production tourne à **+6 %** depuis juillet. Le premier verdict — « le trailing détruit la performance » — n'était qu'un artefact de ce réglage périmé ; au bon seuil, le trailing **améliore** le résultat (-285 € contre -338 € sans). Le backtest lit désormais `AUTO_BREAKEVEN_PCT` depuis `config.py`
- **Ce que le backtest dit vraiment** : le moteur quantitatif SEUL reste perdant sur les deux univers (P(gagnante) 10-40 %), ce qui confirme l'audit de juillet — l'étage de validation IA, non simulable, est ce qui doit porter l'edge
- `backtest.py` gagne `--ext` (comparaison des seuils), `--us` (univers US) et valide désormais **toutes** les variantes, plus seulement B et C

### 2026-07-30 — Alerte « SL proche » : ne part plus à l'ouverture de la position
- La zone d'alerte était `SL + 5 %`. Avec un SL à 2×ATR, toute valeur dont l'ATR est sous ~2,5 % ouvrait sa position **déjà dans la zone** : l'alerte partait immédiatement, à chaque fois (JNJ : SL à -4,6 %, zone d'alerte 0,2 % **au-dessus** du PRU)
- Nouvelle règle : le plus bas de `SL + 5 %` et des **deux tiers du chemin du PRU vers le SL** — l'alerte ne peut plus se déclencher au-dessus du PRU, quelle que soit la largeur du SL. Réarmement à +2 % au-dessus de la zone (au lieu de +8 % au-dessus du SL, inatteignable sur un SL serré)

### 2026-07-30 — P&L réalisé : conversion de devise à la clôture
- **Le P&L d'un trade en dollars était additionné tel quel à un total en euros.** Le correctif du 29/07 ne portait que sur le P&L **latent** ; la clôture, elle, enregistrait `pnl` dans la devise du trade. Première victime : JNJ, clôturé au SL le 30/07 à **-48,82 $** comptés comme -48,82 € — soit **6,4 € d'erreur** sur un seul trade
- `record_close` enregistre désormais `currency` et `pnl_eur` (converti au taux de la clôture) ; `/stats` et le dashboard raisonnent sur `pnl_eur`. Les trades antérieurs, tous en euros, ont été complétés avec les deux champs

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

`venv/bin/python3 backtest.py` rejoue le moteur quantitatif sur l'univers de scan (2023 → aujourd'hui, hypothèses pessimistes : SL prioritaire sur TP dans la même bougie, gaps exécutés à l'open, barème BD réel — tranches Euronext, forfait US, TTF française à l'achat, commission de change). Enseignements de la campagne du 14/07/2026 :

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
BREAKEVEN_THRESHOLD=5     # trailing palier 1 (manuel) : % au-dessus du PRU
TRAIL_LOCK_TRIGGER_PCT=60 # trailing palier 2 : % du chemin PRU→TP avant de sécuriser
TRAIL_LOCK_MIN_RATIO=50   # % du gain verrouillé au déclenchement
TRAIL_LOCK_MAX_RATIO=80   # % du gain verrouillé au contact du TP
TRAIL_MIN_BUFFER_PCT=2    # marge mini sous le cours (ou 1×ATR si plus large)
TRAIL_MIN_STEP_PCT=1      # gain mini du SL pour justifier annuler/reposer
POSITION_BUDGET_PCT=50    # % du cash investi par nouvelle position (suggestions /scan)
POSITION_BUDGET_MAX=1000  # plafond en € par position (à adapter à votre capital)
CASH_SWEEP_MIN_LEFTOVER=500  # sous ce reliquat, la position est agrandie (0 = off)

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

BROKERAGE_FEE_US=8.50     # courtage BD par ordre US (le barème Euronext est en dur)
TTF_RATE=0.004            # taxe transactions financières FR, à l'achat (0.4%)
MIN_NET_GAIN_FEE_RATIO_US=5  # seuil de rentabilité côté étranger
MIN_NET_GAIN_FEE_RATIO=5  # gain brut au TP requis : au moins N× les frais A/R

SMALL_GAIN_MODE=off       # trades courts forcés quand rien ne passe (déconseillé)
FALLBACK_TP_MIN_PCT=3     # mode gain réduit : TP minimum des trades courts
FALLBACK_TP_MAX_PCT=8     # mode gain réduit : TP maximum des trades courts

DASHBOARD_BIND=127.0.0.1  # 0.0.0.0 pour accès Tailscale/LAN (voir section Dashboard)
```

---


### Frais Bourse Direct — barème réel

Les frais ne sont pas un forfait par ordre : **trois composantes**, dont deux
que le bot ignorait complètement jusqu'au 02/08/2026.

**1. Courtage** — par tranches sur Euronext, forfait aux US, pourcentage avec
minimum ailleurs :

| Place | Courtage par ordre |
|---|---|
| Euronext (`.PA`, `.AS`, `.BR`) | 0,99 € < 500 € · 1,90 € < 1 000 € · 2,90 € < 2 000 € · 3,80 € < 4 400 € · puis 0,09 % |
| US (sans suffixe) | 8,50 € jusqu'à 10 000 €, puis 0,09 % |
| Londres (`.L`), Xetra (`.DE`) | 0,15 %, **minimum 15 €** |
| Madrid (`.MC`), Suisse (`.SW`), Lisbonne (`.LS`) | 0,20 %, **minimum 18 €** |
| Autres marchés (dont Milan `.MI`) | 0,48 %, **minimum 41,90 €** |

**2. TTF française** — 0,4 % du montant, **à l'achat uniquement**, sur les
sociétés dont le **siège social est en France** et la capitalisation dépasse
**1 Md€**. Sur une grande valeur française elle coûte *plus cher que le
courtage* (3,63 € contre 1,90 € sur un ordre de 900 €). Ni la place ni le
suffixe ne suffisent à trancher : Airbus cote à Paris mais son siège est aux
Pays-Bas, Genfit est française mais sous le milliard — les deux sont exonérées.
Le classement vient de `country` + `marketCap` (yfinance), en cache disque 30
jours (`ttf_cache.json`). **Donnée manquante = considéré assujetti** :
surestimer les frais fait renoncer à un trade marginal, les sous-estimer fait
entrer dans un trade qui ne couvre pas ses coûts.

**3. Commission de change** — 0,08 % par opération sur tout ordre libellé en
devise (US, Londres, Suisse).

#### Vérifié au centime sur nos propres ordres

Le PRU affiché par Bourse Direct inclut tous les frais : la différence avec le
montant exécuté les donne exactement.

| Ordre | Montant exécuté | Frais réels | Modèle | Détail |
|---|---|---|---|---|
| AIR 5 × 196,52 € | 982,60 € | **1,90 €** | 1,90 € | courtage seul (Airbus SE = siège NL, pas de TTF) |
| GLE 12 × 75,55 € | 906,60 € | **5,53 €** | 5,53 € | 1,90 courtage + 3,63 TTF |
| BAC 12 × 61,43 $ | 647,76 € | **9,03 €** | 9,02 € | 8,50 courtage US + 0,52 change |

Les frais enregistrés dans l'historique confirment les tranches Euronext :
AL2SI 1 130 € → 2,90 € · GNFT 851 € → 1,90 € · LBIRD 1 174 € → 2,90 €.

#### Position minimale rentable (seuil 5×, TP +10 %)

| Place | Position mini |
|---|---|
| Euronext, valeur non soumise à la TTF | ~100 € |
| Euronext, grande valeur française (TTF) | ~130 € |
| US | ~930 € |
| Xetra / Londres | ~1 500 € |
| Milan / autres marchés | ~4 190 € |

Calculé par balayage (`config.min_viable_amount`) et non par formule : le
barème mêle un forfait par tranches et des composantes proportionnelles.

⚠️ **Le US ne passe qu'au-dessus de 930 €.** `POSITION_BUDGET_MAX` est à
**1 000 €** précisément pour laisser cette marge — sous 930 €, aucun achat US
ne peut respecter le seuil 5× et le scan US le dit explicitement au lieu
d'échouer en silence. Si vous redescendez ce plafond sous 930 €, le US se
referme ; l'autre levier est `MIN_NET_GAIN_FEE_RATIO_US` (à 3 : ~560 € de
position mini, mais les frais pèsent plus lourd dans le gain).

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
