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
- **Mode Autonome** (optionnel, nécessite Playwright) : le bot gère un **budget isolé en totale autonomie** — il scanne le marché, entre en position, relève le SL au PRU à +3%, et vous notifie pour chaque action.

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
| **Trailing stop automatique** | À +5% (manuel) / +3% (autonome), l'ordre Expert est **remplacé sur BD** avec le SL au PRU (P&L ≥ 0 garanti) |
| **Ordres Expert réels** | `/ordre acheter TTE.PA 3 expert 54.2 49.0 61.0` — achat+SL+TP en un seul ordre, envoyé à BD (Euronext + marchés US) |
| **Validité des ordres** | Par séance, max (fin d'année Euronext / fin de mois US), ou date précise JJ/MM/AAAA |
| **Mode Autonome** | Budget isolé géré en totale autonomie : scan → entrée → SL au PRU à +3% → sortie détectée → réinvestissement. Ordres d'entrée non exécutés à la clôture : annulés auto (anti-sélection) |
| **Positions HOLD long terme** | `/hold TICKER` : sortie du périmètre bot (pas d'alertes, hors P&L trading, jamais proposée à la vente) |
| **Mode gain réduit** | Si rien ne passe à +10%, trades courts (TP +3-8%, 1-5 jours) à rentabilité nette de frais contrôlée |
| **Dashboard visuel** | http://localhost:8642 (accès Tailscale possible) + `/dashboard` Telegram : P&L cumulé, cash engagé, ROI, trades filtrables |
| **Instructions d'ordres** | Format Bourse Direct step-by-step, prêt à saisir sur mobile ou web |
| **Import screenshot** | Envoyez vos captures d'écran — le bot lit et importe automatiquement |
| **Import CSV** | Envoyez l'export Bourse Direct — importe avec SL/TP par défaut |
| **IA pluggable** | 5 providers : Groq, Gemini (gratuits), Anthropic, OpenAI, Mistral |
| **Indicateurs techniques** | RSI 14j, momentum 1 mois, ratio volume — filtre avant analyse IA |
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

**Scheduler :** `schedule` (Python) — 4 checks SL/TP/jour + briefing 9h05. À chaque check, `autonomous_engine` est invoqué pour surveiller les positions autonomes et tenter de nouvelles entrées si Playwright est connecté.

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
| `/stats` | Bilan des trades : win rate, P&L réalisé, profit factor |
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
| `/sync` | Synchroniser le portefeuille depuis Bourse Direct — détecte et clôture automatiquement les ventes exécutées (TP/SL touchés), ajoute les positions issues d'achats exécutés |
| `/testordre TICKER` | Diagnostic : teste 5 variantes de payload d'ordre contre l'API BD (validation seule, rien n'est envoyé au marché) |
| `/capture` | Diagnostic : trace dans le log toutes les requêtes API que le site BD envoie (à utiliser en passant un ordre à la main dans la fenêtre Chromium du bot) |

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
| `/auto status` | État complet + P&L en temps réel des positions autonomes |

> Le bot opère entièrement seul sur ce budget : il exploite les opportunités validées par le briefing/`/scan` (cycle d'entrée **toutes les heures** + à chaque check), entre en position via un ordre Expert (SL+TP garantis sur BD), et vous notifie pour chaque action. Maximum 2 positions simultanées ; les **ordres en attente comptent dans le budget** (fonds réservés). La position n'est créée qu'à l'**exécution réelle** de l'ordre, détectée par le sync.
>
> **Horaires par marché** : un titre US n'est acheté qu'entre 15h35 et 21h55 Paris (Euronext : 9h05-17h25) — les opportunités US validées le matin attendent l'ouverture de Wall Street.
>
> **Mode gain réduit** : quand aucune opportunité à +10% ne passe la validation, le bot re-teste les meilleurs candidats en trade court (TP +3 à +8% calé sous la première résistance, SL ≤ TP en %, horizon 1-5 jours) — la rentabilité nette de frais reste contrôlée. Objectif : gagner un peu chaque jour plutôt que rien.
>
> **Trailing stop réel** : dès **+3%** (autonome) ou **+BREAKEVEN_THRESHOLD%** (manuel), le bot **remplace l'ordre Expert sur BD** avec le SL remonté au PRU — automatique, TP inchangé, uniquement pour les positions protégées par un ordre Expert actif (les positions historiques sans ordre ne sont jamais touchées).

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

1. **Recherche** — À chaque check planifié (9h, 12h, 15h, 17h), il filtre ~100 actions via RSI / momentum / volume, puis valide les 5 meilleurs candidats avec l'IA
2. **Entrée** — Place un ordre Expert achat (entrée + SL + TP) sur Bourse Direct — le SL et le TP sont garantis côté BD
3. **Trailing stop** — Quand la position atteint **+3%** du PRU, relève le SL au PRU (P&L ≥ 0 garanti)
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

Bot : "✅ ACHAT AUTONOME CONFIRMÉ
       ASML.AS | 1 titre @ 720€
       SL : 664€ | TP : 800€
       Coût : 720€ | Budget restant : 0€"

[Quelques jours plus tard, à +3%]
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

`/lessons` affiche à tout moment ce que le bot a retenu et les garde-fous actifs.

> **Source de décision unique** : le jugement ACHAT/EXCLUS est produit par **une seule fonction** (`validate_candidate`) que tous les chemins appellent (scan, briefing, gain réduit, contrôle pré-achat autonome). Même stratégie d'analyse, mêmes règles, mêmes leçons partout — seul l'objectif de TP varie selon le mode.

---

## Dashboard visuel

Un tableau de bord local est servi en permanence par le bot : **http://localhost:8642**

- **Cartes de synthèse** : P&L réalisé / latent / total, win rate, profit factor, cash, performance en €/jour depuis le premier trade, ROI sur cash engagé
- **Courbe du P&L cumulé** sur axe temporel réel — la taille de chaque point est proportionnelle au cash engagé sur le deal
- **P&L par trade** : une barre par trade avec nom, date, cash engagé et résultat annoté
- **Tableau des trades filtrable** (texte, WIN/LOSS) avec colonnes Investi et ROI
- **Positions ouvertes** : cours live, variation, P&L latent, SL/TP, badge `auto` pour les positions du mode autonome

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

## Règles de trading par défaut

- **Stop-loss** : -7% sur PRU
- **Take-profit** : +10% sur PRU (minimum — l'IA peut viser plus haut si le potentiel le justifie)
- **Trailing stop** : SL relevé au PRU à +5% (positions manuelles) / +3% (mode autonome)
- **Taille de position suggérée** (`/scan`) : 50% du cash, plafonné à 800€
- Modifiables dans `.env` :

```env
DEFAULT_SL_PCT=7          # stop-loss en % sous le PRU
DEFAULT_TP_PCT=10         # take-profit minimum en % au-dessus du PRU
BREAKEVEN_THRESHOLD=5     # trailing stop positions manuelles : % au-dessus du PRU
POSITION_BUDGET_PCT=50    # % du cash investi par nouvelle position
POSITION_BUDGET_MAX=800   # plafond en € par position (à adapter à votre capital)

BROKERAGE_FEE=1.98        # frais de courtage BD par ordre (aller-retour = 2×)
MIN_NET_GAIN_FEE_RATIO=5  # gain brut au TP requis : au moins N× les frais A/R

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
