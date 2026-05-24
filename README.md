# TradingBot

**Assistant de trading personnel pour Bourse Direct, piloté par Telegram.**

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Telegram-26A5E4?logo=telegram&logoColor=white)
![AI](https://img.shields.io/badge/IA-Groq%20%7C%20Gemini%20%7C%20Anthropic%20%7C%20OpenAI%20%7C%20Mistral-orange)
![Broker](https://img.shields.io/badge/Courtier-Bourse%20Direct-navy)

Bourse Direct ne dispose pas d'API publique. TradingBot comble ce manque : il analyse votre portefeuille chaque matin, surveille vos positions, génère les instructions d'ordres précises à saisir sur l'app iPhone ou web, et vous alerte en temps réel — le tout depuis Telegram.

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

**Stabilité en production :** le bot tourne en arrière-plan (polling Telegram, scheduler). Il est stable sur un Mac allumé en permanence ou un serveur Linux. Pas de gestion automatique des redémarrages après crash — prévoir `systemd` ou `launchd` pour un usage continu.

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
pip install -r requirements.txt
```

> **C'est quoi un venv ?** Un environnement virtuel isole les packages Python de ce projet du reste de votre système. Sans ça, les librairies s'installent globalement et peuvent entrer en conflit avec d'autres projets. Bonne pratique systématique.

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
| **Recherche web gratuite** | DuckDuckGo + yfinance — aucune clé payante requise |
| **Contexte personnel** | Fichier de contexte IA pour des conseils adaptés à votre situation |

---

## Architecture — pour les devs

```
TradingBot/
├── main.py           Point d'entrée : lance le scheduler + le polling Telegram
├── config.py         Variables d'env centralisées (lues depuis .env)
├── telegram_bot.py   Polling Telegram, routing des commandes, buffer photo
├── analysis.py       Prompts IA : briefing, scan, import screenshots
├── monitor.py        Vérification SL/TP 4×/jour, envoi des alertes
├── orders.py         Génère les instructions texte format Bourse Direct
├── portfolio.py      CRUD positions.json + import CSV
├── prices.py         Prix temps réel via yfinance (Yahoo Finance)
├── ai_provider.py    Abstraction multi-providers avec vision (5 providers)
├── research.py       Recherche web via DuckDuckGo (sans clé API)
│
├── .env.example                      Template de configuration
├── positions.example.json            Exemple de portefeuille
├── CLAUDE_TRADING_CONTEXT.example.md Template de contexte IA personnel
│
├── positions.json             Votre portefeuille — ignoré par git ✅
├── CLAUDE_TRADING_CONTEXT.md  Votre contexte personnel — ignoré par git ✅
└── .env                       Vos secrets — ignoré par git ✅
```

**Flux de données :**
`positions.json` est la source de vérité, lue dynamiquement à chaque cycle — pas besoin de redémarrer le bot après une modification.

**Scheduler :** `schedule` (Python) — 4 checks SL/TP/jour + briefing 9h05. Tourne dans le thread principal, le polling Telegram dans un thread daemon.

**IA :** chaque provider expose `complete(prompt)` et `complete_with_image(prompt, bytes)`. Ajouter un provider = hériter de `AIProvider` dans `ai_provider.py`.

**Dépendances clés :**
- `yfinance` — prix et historiques (Yahoo Finance, gratuit, sans clé)
- `duckduckgo-search` — recherche web (gratuit, sans clé)
- `python-telegram-bot` non utilisé — polling HTTP direct via `requests` pour garder le contrôle
- `schedule` — scheduler léger (cron-like en Python pur)

---

## Commandes Telegram

### Portefeuille

| Commande | Description |
|---|---|
| `/status` | Portefeuille complet avec P&L temps réel, alertes SL/TP |
| `/cash [montant]` | Voir ou mettre à jour le cash disponible |

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
| `/order buy\|sell TICKER QTY PRIX` | Générer une instruction d'ordre complète |

### Analyse IA

| Commande | Description |
|---|---|
| `/morning` | Déclencher manuellement le briefing matinal |
| `/scan` | Scanner des opportunités avec le cash disponible |
| `/research TICKER` | Analyse approfondie d'une action |

### Import & Aide

| Commande | Description |
|---|---|
| 📸 Photo | Captures d'écran du portefeuille Bourse Direct — import automatique |
| `/import` | Guide import CSV Bourse Direct |
| `/help` | Liste complète des commandes |
| `/tuto` | Rappel des étapes de configuration |

---

## Personnaliser les conseils IA

Par défaut l'IA ne connaît que votre portefeuille temps réel et le contexte macro du jour. Pour des conseils adaptés à votre situation réelle (objectifs, positions bloquées, historique, règles personnelles) :

```bash
cp CLAUDE_TRADING_CONTEXT.example.md CLAUDE_TRADING_CONTEXT.md
# Éditez avec votre situation — objectif, positions hors-bot, règles, historique récent
```

Ce fichier est injecté automatiquement dans chaque prompt IA (`/morning`, `/scan`, `/research`). Il est dans `.gitignore` — jamais publié.

---

## Limitations

**Pas d'API Bourse Direct**
Bourse Direct ne fournit pas d'API publique. Le bot génère des instructions texte que vous saisissez manuellement dans l'app. Aucun ordre ne peut être passé automatiquement.

**Qualité de l'import screenshot**
L'extraction de positions depuis des captures d'écran dépend de la lisibilité des images et des capacités vision du provider IA choisi. Les résultats varient selon la résolution, le zoom et le provider. Vérifiez toujours avec `/status` après import.

**Données marché**
Les prix proviennent de Yahoo Finance via `yfinance` (données différées de 15 min en journée) et les analyses de DuckDuckGo. Ni l'un ni l'autre ne garantit une disponibilité ou une exactitude permanente. Les actions suspendues ou en liquidation judiciaire peuvent ne plus avoir de cotation.

**Pas de passage d'ordre automatique**
Ce bot est un assistant de décision, pas un système d'exécution. Chaque ordre reste sous votre contrôle direct.

**Stabilité**
Pas de gestion de crash automatique. Pour un usage continu, configurez un superviseur de processus (`systemd`, `launchd`, `supervisor`).

---

## Règles de trading par défaut

- **Stop-loss** : -10% sur PRU
- **Take-profit** : +15% sur PRU
- Modifiables dans `.env` : `DEFAULT_SL_PCT` et `DEFAULT_TP_PCT`

---

## Lancer en tâche de fond

Pour que le bot continue à tourner après avoir fermé votre terminal :

```bash
# Lancer en arrière-plan (logs dans tradingbot.log)
nohup venv/bin/python3 main.py > tradingbot.log 2>&1 &

# Vérifier qu'il tourne
ps aux | grep main.py

# Lire les logs en direct
tail -f tradingbot.log

# Arrêter le bot
pkill -f "main.py"
```

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
