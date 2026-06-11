# TradingBot

**Assistant de trading personnel pour Bourse Direct, piloté par Telegram.**

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Telegram-26A5E4?logo=telegram&logoColor=white)
![AI](https://img.shields.io/badge/IA-Groq%20%7C%20Gemini%20%7C%20Anthropic%20%7C%20OpenAI%20%7C%20Mistral-orange)
![Broker](https://img.shields.io/badge/Courtier-Bourse%20Direct-navy)
![Mode](https://img.shields.io/badge/Mode-Classic%20%7C%20Playwright%20%28bêta%29-purple)

Bourse Direct ne dispose pas d'API publique. TradingBot comble ce manque : il analyse votre portefeuille chaque matin, surveille vos positions, génère les instructions d'ordres précises à saisir sur l'app iPhone ou web, et vous alerte en temps réel — le tout depuis Telegram.

**Deux modes de fonctionnement :**
- **Mode Classic** (défaut) : données via Yahoo Finance + import par captures d'écran — aucun accès à votre compte BD requis.
- **Mode Playwright** (optionnel) : le bot se connecte à Bourse Direct via un navigateur headless, lit vos données en temps réel et **passe des ordres directement depuis Telegram** avec double confirmation.

---

## Démo rapide — 30 secondes pour comprendre le flux

```
/start          → message de bienvenue : nb de positions, cash disponible,
                   et les 3 commandes clés pour démarrer

/cash 1500      → enregistre votre cash disponible

/add GNFT.PA 100 8.51 7.66 9.79
                → ajoute une position (ticker, qté, PRU, SL, TP)

/setup LBIRD 48 24.46
                → génère les 2 instructions d'ordres à saisir sur Bourse Direct
                   (stop-loss à 22.01€ + take-profit à 28.13€)

/status         → portefeuille complet avec P&L en temps réel

📸 photo        → envoyez une capture d'écran de votre app Bourse Direct
                   → le bot lit les positions et les importe automatiquement

/morning        → briefing IA : état du portefeuille + macro + 3 opportunités
```

Tout se passe dans Telegram. Bourse Direct reste votre seul point d'exécution — vous saisissez vous-même les ordres.

---

## Prérequis

| | |
|---|---|
| **Python** | 3.10 ou supérieur (`python3 --version`) |
| **Git** | Pour cloner le projet (`git --version`) |
| **OS testé** | macOS, Linux. Windows fonctionne mais non testé en prod |
| **Compte Telegram** | Pour créer votre bot via @BotFather |
| **Clé API IA** | Groq ou Gemini sont gratuits (voir étape 3) |
| **Bourse Direct** | Compte actif — vous passez les ordres manuellement |
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

> Ces commandes sont affichées à la fin du script d'installation sous `==> Next steps:`. Copiez-les depuis votre terminal — elles peuvent légèrement varier selon votre machine.

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

> **C'est quoi `cd` ?** `cd` signifie "change directory" — ça déplace le terminal dans le dossier du projet. Toutes les commandes qui suivent doivent être exécutées depuis ce dossier. Si vous fermez et rouvrez votre terminal, pensez à refaire `cd Tradingbot` avant de lancer quoi que ce soit.

> **Git vous demande un mot de passe ?** C'est un repo public — aucun mot de passe nécessaire. Si la question apparaît quand même, appuyez sur **Entrée** sans rien taper. Si ça bloque toujours, GitHub n'accepte plus les mots de passe depuis 2021 — utilisez `gh` (GitHub CLI) : `brew install gh && gh auth login`, puis relancez le clone.

Créez un environnement virtuel et installez les dépendances :

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

> **C'est quoi un venv ?** Un environnement virtuel isole les packages Python de ce projet du reste de votre système. Sans ça, les librairies s'installent globalement et peuvent entrer en conflit avec d'autres projets. Bonne pratique systématique.

> **Pourquoi `venv/bin/pip` et pas `pip` ?** Sur macOS la commande `pip` n'existe pas — c'est `pip3`, et encore, elle installe les packages globalement. `venv/bin/pip` utilise directement le pip du venv créé juste avant, sans ambiguïté sur aucun OS.

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

> **Pourquoi `venv/bin/python3` et pas juste `python3` ?** Ça garantit que vous utilisez le Python du venv avec toutes les librairies installées, même si vous n'avez pas activé l'environnement. Plus fiable, surtout pour relancer le bot depuis un script ou un terminal fraîchement ouvert.

> ⚠️ **Lancé ainsi, le bot s'arrête si vous fermez la fenêtre de terminal.** Pour un usage durable, utilisez plutôt `./bot.sh start` (arrière-plan) et `./bot.sh autostart` (relance auto au boot et après crash) — voir [Lancer en tâche de fond](#lancer-en-tâche-de-fond).

---

## Fonctionnalités

| | |
|---|---|
| **Briefing matinal 9h05** | Analyse IA : état des positions + contexte macro + top 3 opportunités |
| **Surveillance 4×/jour** | Check automatique 9h / 12h / 15h / 17h — alerte si SL ou TP atteint |
| **Instructions d'ordres** | Format Bourse Direct step-by-step, prêt à saisir sur mobile ou web |
| **Import screenshot** | Envoyez vos captures d'écran — le bot lit et importe automatiquement |
| **Import CSV** | Envoyez l'export Bourse Direct — importe avec SL/TP par défaut |
| **IA pluggable** | 5 providers : Groq, Gemini (gratuits), Anthropic, OpenAI, Mistral |
| **Indicateurs techniques** | RSI 14j, momentum 1 mois, ratio volume — filtre avant analyse IA |
| **Catalyseurs imminents** | Recherche résultats, contrats, OPA, rachats — signaux +10% et plus |
| **Sentiment social composite** | Score -100/+100 par ticker : StockTwits, Reddit, forum Boursorama (IA), VADER + détection de pics de volume |
| **Sentiment marché temps réel** | VIX + CNN Fear & Greed injectés dans chaque briefing |
| **Menu de commandes Telegram** | Les 28 commandes dans le menu natif (bouton bas-gauche) |
| **Sync Gmail Bourse Direct** | Détecte auto les emails "Finalisation de votre stratégie" et clôture les positions (IMAP, sans OAuth) |
| **Recherche web gratuite** | DuckDuckGo + yfinance — aucune clé payante requise |
| **Contexte personnel** | Fichier de contexte IA pour des conseils adaptés à votre situation |
| **Mode Playwright** *(bêta — optionnel)* | Connexion Bourse Direct via navigateur headless — lecture live du portefeuille, sync automatique, et passage d'ordres réels depuis Telegram ⚠️ non testé en prod |

---

## Architecture — pour les devs

```
TradingBot/
├── main.py                  Point d'entrée : lance le scheduler + le polling Telegram
├── config.py                Variables d'env centralisées (lues depuis .env)
├── telegram_bot.py          Polling Telegram, routing des commandes, buffer photo
├── analysis.py              Prompts IA : briefing, scan, indicateurs techniques, catalyseurs
├── monitor.py               Vérification SL/TP 4×/jour, envoi des alertes
├── orders.py                Génère les instructions texte format Bourse Direct
├── portfolio.py             CRUD positions.json + import CSV
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
`positions.json` est la source de vérité en mode Classic. En mode Playwright, `bourse_direct_reader.py` synchronise les données réelles de BD dans ce même fichier — les deux modes sont compatibles et le fichier reste toujours à jour.

**Scheduler :** `schedule` (Python) — 4 checks SL/TP/jour + briefing 9h05. Tourne dans le thread principal, le polling Telegram dans un thread daemon.

**IA :** chaque provider expose `complete(prompt)` et `complete_with_image(prompt, bytes)`. Ajouter un provider = hériter de `AIProvider` dans `ai_provider.py`.

**Dépendances clés :**
- `yfinance` — prix et historiques (Yahoo Finance, gratuit, sans clé)
- `duckduckgo-search` — recherche web (gratuit, sans clé)
- `python-telegram-bot` non utilisé — polling HTTP direct via `requests` pour garder le contrôle
- `schedule` — scheduler léger (cron-like en Python pur)
- `playwright` *(optionnel)* — navigateur Chromium headless pour le mode Playwright

---

## Commandes Telegram

### Portefeuille

| Commande | Description |
|---|---|
| `/status` | Portefeuille complet avec P&L temps réel, alertes SL/TP |
| `/cash [montant]` | Voir ou mettre à jour le cash disponible |
| `/stats` | Bilan des trades : win rate, P&L réalisé, profit factor |

### Positions

| Commande | Description |
|---|---|
| `/add TICKER QTY PRU SL TP` | Ajouter une position manuellement |
| `/remove TICKER` | Supprimer une position |
| `/sl TICKER PRIX` | Mettre à jour le stop-loss |
| `/tp TICKER PRIX` | Mettre à jour le take-profit |

### Ordres Bourse Direct

| Commande | Description |
|---|---|
| `/setup TICKER QTY PRU` | Générer les instructions SL+TP à saisir après un achat |
| `/buy TICKER QTY PRU` | Générer un ordre Expert complet (achat + SL + TP groupés) |
| `/order buy\|sell TICKER QTY PRIX` | Générer une instruction d'ordre complète |
| `/attente NOM TICKER QTY PRIX [SL TP]` | Ordre en attente : réserve le cash, alerte quand le cours est atteint |
| `/annuler NOM` | Annuler un ordre en attente (côté bot) |

### Analyse IA

| Commande | Description |
|---|---|
| `/morning` | Déclencher manuellement le briefing matinal |
| `/scan` | Scanner des opportunités — catalyseurs + indicateurs techniques |
| `/research TICKER` | Analyse approfondie : RSI, momentum, catalyseurs imminents |

### Clôture de trades

| Commande | Description |
|---|---|
| `/vendu NOM [PRIX]` | Clôturer une position — prix TP automatique si omis |
| `/close TICKER QTY PRIX [FRAIS]` | Clôturer avec frais de courtage |
| `/syncmail` | Vérifier Gmail pour les ordres Bourse Direct finalisés |

> **`/vendu`** est la commande recommandée au quotidien. Elle accepte le nom court ou le ticker, récupère automatiquement le prix du TP posé sur Bourse Direct, et met à jour le cash.

### Mode Playwright *(optionnel)*

| Commande | Description |
|---|---|
| `/mode` | Afficher le mode actuel (Classic ou Playwright) et l'état de la session |
| `/connect` | Activer le mode Playwright — connexion à Bourse Direct avec relay TOTP |
| `/disconnect` | Fermer la session Playwright et revenir en mode Classic |
| `/sync` | Synchroniser le portefeuille depuis Bourse Direct |

### Passage d'ordres réels *(mode Playwright uniquement)*

| Commande | Description |
|---|---|
| `/ordre vendre TICKER QTE marche` | Vente au marché |
| `/ordre vendre TICKER QTE limite PRIX` | Vente à cours limité |
| `/ordre vendre TICKER QTE expert SL TP` | Vente Expert — stop-loss + take-profit en un seul ordre |
| `/ordre acheter TICKER QTE marche` | Achat au marché |
| `/ordre acheter TICKER QTE limite PRIX` | Achat à cours limité |
| `/oui` | Confirmer et envoyer l'ordre en attente (irréversible) |
| `/non` | Annuler l'ordre en attente |
| `/annuler_bd TICKER` | Annuler un ordre en cours sur Bourse Direct |

> **Format des tickers :** utilisez le format Yahoo Finance — `EXENS.PA`, `GNFT.PA`, `ILMN`, `BP.L`, `SAP.DE`. La conversion vers le format interne Bourse Direct est automatique.
>
> **Flow :** `/ordre ...` → le bot affiche recap + frais calculés → `/oui` pour envoyer, `/non` pour annuler (timeout 120s).

### Import & Aide

| Commande | Description |
|---|---|
| 📸 Photo | Captures d'écran du portefeuille Bourse Direct — import automatique (Classic et Playwright) |
| `/import` | Guide import CSV Bourse Direct |
| `/help` | Liste complète des commandes |
| `/tuto` | Rappel des étapes de configuration |
| `/update` | Afficher le commit en cours et la date de mise à jour |

---

## Mode Playwright — Connexion Bourse Direct

> ### ⚠️ AVERTISSEMENT — FONCTIONNALITÉ EN BÊTA
>
> **Le mode Playwright n'a pas encore été testé en conditions réelles.** Les scripts de connexion, de lecture de portefeuille et de passage d'ordres ont été développés par ingénierie inverse de l'interface Bourse Direct (exploration du DOM, analyse des bundles JavaScript). Ils n'ont pas encore été exécutés sur un compte avec de l'argent réel.
>
> **Ce mode opère sur votre compte de courtage avec votre argent réel.** Une erreur dans les scripts peut entraîner le passage d'ordres non désirés, une mauvaise quantité, ou une mauvaise valeur. Vous êtes entièrement responsable de tout ordre exécuté.
>
> **Avant de l'utiliser :**
> - Testez d'abord `/sync` seul (lecture seule — aucun risque)
> - Ne testez `/ordre` qu'avec de petites quantités
> - Vérifiez toujours l'écran de récapitulatif avant de taper `/oui`
> - Gardez l'app Bourse Direct ouverte en parallèle pour surveiller
> - En cas de doute, tapez `/non` ou fermez la session avec `/disconnect`

Le mode Playwright est **entièrement optionnel**. Le mode Classic (défaut) reste pleinement fonctionnel sans rien configurer.

### Ce que ça apporte

| | Mode Classic | Mode Playwright |
|---|---|---|
| Source des données marché | Yahoo Finance (différé 15 min) | Yahoo Finance + cours BD live |
| Source du portefeuille | Captures d'écran + saisie manuelle | Lecture automatique depuis BD |
| Cash disponible | Mis à jour manuellement | Synchronisé depuis BD |
| Passage d'ordres | Instructions texte à saisir | Ordres réels envoyés depuis Telegram (double confirmation) |
| Import de positions | Photo ou CSV | Sync automatique |

### Prérequis

```bash
# Installer playwright dans le venv
venv/bin/pip install playwright
venv/bin/playwright install chromium
```

### Configuration

Ajoutez dans votre `.env` :

```env
BD_LOGIN=votre_identifiant_bourse_direct
BD_PASSWORD=votre_mot_de_passe
```

> ⚠️ Ces credentials ne quittent jamais votre machine. Ils sont dans `.gitignore` et ne sont jamais loggés ni transmis.

### Utilisation

**Activer le mode Playwright :**
```
/connect
```
Le bot lance Chromium en arrière-plan, se connecte à Bourse Direct. Bourse Direct utilise une authentification **TOTP** (application d'authentification à 6 chiffres — Google Authenticator, Authy...).

```
Bot : "Code 2FA Bourse Direct reçu par ton app ? Envoie-le ici (90 secondes) :"
Vous : 847291
Bot : "Mode Playwright actif ✅ Connecté à Bourse Direct"
```

**Synchroniser le portefeuille :**
```
/sync
```
Compare le portefeuille réel BD avec `positions.json` — met à jour le cash, les quantités, détecte les écarts.

**Passer un ordre :**
```
/ordre vendre EXENS.PA 17 expert 56.7 72.45
```
→ Le bot crée l'ordre, affiche le récapitulatif + frais calculés
```
/oui   → envoie l'ordre au marché (irréversible)
/non   → annule
```

**Types d'ordres disponibles :**
- `marche` — au marché, exécution immédiate
- `limite PRIX` — ordre à cours limité
- `expert SL TP` — ordre Expert Bourse Direct : stop-loss + take-profit en un seul ordre (équivalent "Cpt EQT" dans votre portefeuille)

**Voir l'état du mode :**
```
/mode
```

**Revenir en mode Classic :**
```
/disconnect
```

### Comportement au redémarrage

Le bot démarre **toujours en mode Classic**, même si le mode Playwright était actif avant. La session Playwright ne survit pas à un redémarrage — vous devez relancer `/connect` manuellement.

### État du développement

| Fonctionnalité | Statut |
|---|---|
| Connexion BD + TOTP | ✅ Testé et fonctionnel |
| Lecture portefeuille CTO (`/sync`) | ✅ Testé et fonctionnel |
| Formulaire ordre standard (UI explorée) | 🔬 API reverse-engineered, non testé |
| Ordre Expert TAKE PROFIT (UI explorée) | 🔬 API reverse-engineered, non testé |
| Ordre Expert STOP LOSS | 🔬 Identifié dans l'UI, non implémenté |
| Ordre Expert STOP SUIVEUR (trailing) | 🔬 Identifié dans l'UI, non implémenté |
| Passage d'ordre réel (`/ordre` + `/oui`) | ❌ **Jamais testé — à valider avant usage** |

**Ce qui a été exploré :**
- L'interface BD utilise un formulaire Vue.js en **4 étapes** pour les ordres Expert : choix du type → configuration (compte, sens, qté, validité) → sélection de stratégie (STOP LOSS / TAKE PROFIT / STOP SUIVEUR) → confirmation
- L'API `hub/trading` accepte `POST /order/create` puis `POST /order/execute/strategy`
- Le payload a été extrait du bundle `ordertrade.bundle.js` par analyse statique

**Ce qui reste à tester :**
- Que le payload `create_order()` soit accepté par l'API BD en conditions réelles
- Que `execute_strategy()` déclenche bien l'ordre Expert côté BD
- La gestion des erreurs API (session expirée, solde insuffisant, marché fermé...)
- L'implémentation des stratégies STOP LOSS et STOP SUIVEUR

### Note sur les CGU

L'automatisation d'un site web via navigateur headless est techniquement en zone grise dans les CGU de la plupart des courtiers. Ce mode est conçu pour un usage personnel, sur votre propre compte, sans impact sur les systèmes de BD. Utilisez-le en connaissance de cause.

---

## Sync automatique des ordres Bourse Direct

Bourse Direct envoie un email "Finalisation de votre stratégie" dès qu'un ordre expert (`take_profit` ou `stop_loss`) est exécuté. Le bot peut détecter ces emails via IMAP et clôturer automatiquement les positions concernées.

### Configuration (une seule fois)

**1. Activez l'accès IMAP dans Gmail :**
> Paramètres Gmail → Voir tous les paramètres → Transfert et POP/IMAP → Activer IMAP

**2. Créez un mot de passe d'application :**
> Lien direct : [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
> (Nécessite la validation en 2 étapes — si le lien ne fonctionne pas, active d'abord la validation en 2 étapes dans Sécurité)

**3. Ajoutez dans votre `.env` :**
```env
GMAIL_USER=votre@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

### Utilisation

| | |
|---|---|
| **Automatique** | Le bot vérifie Gmail à chaque check (9h, 12h, 15h, 17h) et notifie via Telegram si un ordre est clôturé |
| **À la demande** | `/syncmail` — vérifie immédiatement et affiche le résultat |
| **Manuel** | `/vendu NOM [PRIX]` — si Gmail n'est pas configuré ou pour corriger un prix |

### Logique des prix

Le bot utilise le prix de l'ordre posé sur Bourse Direct (pas le cours live) :
- **`take_profit` finalisé** → prix = TP enregistré dans le bot (exact pour un ordre limite)
- **`stop_loss` finalisé** → prix = SL enregistré dans le bot
- Si le prix réel diffère, utilisez `/vendu NOM PRIX_REEL` pour corriger

---

## Personnaliser les conseils IA

Par défaut l'IA ne connaît que votre portefeuille temps réel et le contexte macro du jour. Pour des conseils adaptés à votre situation réelle (objectifs, positions bloquées, historique, règles personnelles) :

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

Une seule commande : `git pull` + installation des nouvelles dépendances + redémarrage. Vérifiez ensuite les nouveautés dans le [CHANGELOG](#changelog) ou via `/update` dans Telegram.

> **Tip :** `git log --oneline -10` affiche les 10 derniers commits pour voir ce qui a changé.

---

## Changelog

### 2026-06-11
- **`bot.sh`** : script de gestion — `start/stop/restart/status/logs/update` + `autostart` (service launchd/systemd : démarrage au boot, relance auto après crash)
- **Fix `/update`** : fonctionne désormais sur toute machine (chemin du projet dérivé de l'installation, plus de chemin codé en dur)
- **Menu de commandes Telegram** : les 28 commandes apparaissent dans le menu natif (bouton bas-gauche de l'app)
- **Indicateur « écrit… »** : les trois points s'affichent pendant tous les traitements (analyses IA, fetch des cours, ordres Playwright)
- **Sentiment social composite** : score -100/+100 par ticker (tags StockTwits + VADER sur textes anglais + scoring IA des messages Boursorama pour les valeurs .PA) + détection des pics de volume entre deux checks
- **Sentiment marché temps réel** : VIX et CNN Fear & Greed injectés dans le contexte macro de chaque briefing/scan
- **Nouvelles règles par défaut** : SL -7% / TP +10% (au lieu de -10%/+15%) — le TP est un minimum, l'IA vise plus haut quand le potentiel le justifie et l'indique explicitement
- **Fix** : les actions US n'affichent plus `nan` dans le briefing avant l'ouverture de Wall Street
- **Fix** : `/research` sur une position détenue ne propose plus de conseils d'achat

### 2026-06-03
- **`/ordre vendre|acheter`** : passage d'ordres réels sur Bourse Direct depuis Telegram — marché, limite, et Expert (SL+TP combiné). Flow : `/ordre ...` → recap + frais → `/oui` pour envoyer
- **`/sync`** : synchronisation réelle BD → `positions.json` — cash, quantités, détection des écarts entre BD et le bot
- **Mode Playwright** : connexion à Bourse Direct via Chromium headless avec authentification TOTP (app d'authentification 6 chiffres)
- **Reader BD** : lecture live du portefeuille CTO depuis `/priv/new/portefeuille-TR.php`
- **Conversion tickers automatique** : format yfinance → format BD interne (`EXENS.PA` → `E:EXENS + XPAR`) pour tous les marchés (Euronext, NASDAQ, NYSE, LSE, Xetra)
- **`/mode`** : bascule Classic ↔ Playwright, affiche l'état de la session
- **`/connect` / `/disconnect`** : activation/désactivation du mode Playwright
- **Fix** : annulation automatique d'un ordre en attente lors de `/add` — recherche désormais par ticker si le nom diffère (ex: EXOSENS vs EXENS)

### 2026-06-01
- **Gmail sync** : détection automatique des emails "Finalisation de votre stratégie" de Bourse Direct — le bot envoie une notification Telegram et demande le prix de vente (`/syncmail`, check auto 4×/jour)
- **`/vendu NOM [PRIX]`** : nouvelle commande pour clôturer rapidement une position (prix TP automatique si omis)
- **Indicateurs techniques** : RSI 14j, momentum 1 mois, ratio volume injectés dans `/scan` et `/research`
- **Catalyseurs imminents** : recherche DuckDuckGo dédiée (résultats, contrats, OPA, rachats) dans chaque analyse
- **Validation tickers** : post-vérification yfinance des tickers proposés par l'IA + critères de risque LOW/MEDIUM/HIGH explicites
- **`/version`** : affiche le commit en cours et la date de mise à jour

---

## Limitations

**Pas d'API Bourse Direct**
Bourse Direct ne fournit pas d'API publique. Le bot génère des instructions texte que vous saisissez manuellement dans l'app. Aucun ordre ne peut être passé automatiquement.

**Qualité de l'import screenshot**
L'extraction de positions depuis des captures d'écran dépend de la lisibilité des images et des capacités vision du provider IA choisi. Les résultats varient selon la résolution, le zoom et le provider. Vérifiez toujours avec `/status` après import.

**Données marché**
Les prix proviennent de Yahoo Finance via `yfinance` (données différées de 15 min en journée) et les analyses de DuckDuckGo. Ni l'un ni l'autre ne garantit une disponibilité ou une exactitude permanente. Les actions suspendues ou en liquidation judiciaire peuvent ne plus avoir de cotation.

**Passage d'ordre (mode Playwright)**
En mode Playwright, le bot utilise l'API interne de Bourse Direct pour envoyer des ordres réels. Il demande toujours une double confirmation : `/ordre ...` affiche le récapitulatif + frais, puis `/oui` envoie l'ordre. Aucun ordre n'est envoyé sans validation explicite.

**Stabilité**
Pour un usage continu, activez `./bot.sh autostart` (service `launchd` sur macOS, `systemd` sur Linux) : démarrage au boot + relance automatique après crash. Sans ça, un crash laisse le bot arrêté jusqu'à relance manuelle.

---

## Règles de trading par défaut

- **Stop-loss** : -7% sur PRU
- **Take-profit** : +10% sur PRU (minimum — l'IA peut viser plus haut si le potentiel le justifie)
- Modifiables dans `.env` : `DEFAULT_SL_PCT` et `DEFAULT_TP_PCT`

---

## Lancer en tâche de fond

Le script `bot.sh` (fourni à la racine du projet) gère tout :

```bash
./bot.sh start        # démarre en arrière-plan — le terminal peut être fermé
./bot.sh stop         # arrête le bot
./bot.sh restart      # redémarre (après une modif de .env par exemple)
./bot.sh status       # tourne ou pas ? + dernières lignes du log
./bot.sh logs         # logs en direct (Ctrl+C pour quitter)
./bot.sh update       # git pull + dépendances + redémarrage
```

### Démarrage automatique au boot (recommandé)

Pour que le bot survive aux redémarrages de l'ordinateur et se relance tout seul après un crash :

```bash
./bot.sh autostart      # installe un service launchd (macOS) ou systemd (Linux)
./bot.sh unautostart    # le désactive
```

Une fois l'autostart actif, plus besoin d'y penser : l'ordinateur redémarre → le bot revient. **Utilisez toujours `./bot.sh stop`/`restart`** (et non `pkill`) : le service relancerait automatiquement un bot tué à la main, ce qui peut créer une double instance.

> ⚠️ L'ordinateur doit rester **allumé et non suspendu** pour les checks de 9h/12h/15h/17h. Sur Mac : Réglages Système → Économie d'énergie → empêcher la suspension automatique. Sur un serveur Linux distant : `sudo loginctl enable-linger $USER` pour que le service survive à la déconnexion SSH.

---

## Contribuer

Projet open-source, conçu pour être étendu. Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour le guide complet.

**Idées de contributions :**
- Support d'autres courtiers français (Boursorama, Fortuneo, Trade Republic...)
- Mode "trade rapide" : surveillance toutes les 30min sur actions volatiles
- Dashboard web léger (Flask)
- Backtest simple sur données historiques Yahoo Finance
- Suite de tests automatisés (pytest)
- Gestion automatique des redémarrages (supervisor, systemd)

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
