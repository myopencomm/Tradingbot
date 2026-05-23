# Contribuer à TradingBot

Merci de l'intérêt ! Ce guide explique comment mettre en place l'environnement de dev et soumettre une contribution.

## Prérequis

- Python 3.10+
- Un compte Telegram + bot créé via @BotFather
- Une clé API IA (Groq gratuit : [console.groq.com](https://console.groq.com))

## Installation dev

```bash
git clone https://github.com/myopencomm/Tradingbot.git
cd Tradingbot
python3 -m venv venv
pip install -r requirements.txt
cp .env.example .env
cp positions.example.json positions.json
# Remplis .env avec tes clés
venv/bin/python3 main.py
```

## Architecture en 2 minutes

```
main.py          Point d'entrée — scheduler + polling Telegram
telegram_bot.py  Routing des commandes /cmd → fonctions
analysis.py      Prompts IA (briefing, scan, screenshots)
monitor.py       Checks SL/TP 4x/jour + alertes
orders.py        Génération d'instructions Bourse Direct
portfolio.py     CRUD positions.json
prices.py        Prix temps réel via yfinance
ai_provider.py   Abstraction multi-providers IA
research.py      Recherche web via DuckDuckGo
stats.py         Historique et performances trades
```

**Flux typique d'une commande :**
`Telegram → telegram_bot.py (routing) → module métier → send()`

## Ajouter un provider IA

1. Hériter de `AIProvider` dans `ai_provider.py`
2. Implémenter `complete(prompt, max_tokens)` et `complete_with_image(prompt, image_bytes)`
3. Ajouter le provider dans `get_provider()` et dans `PROVIDERS`
4. Documenter dans le README (tableau Prérequis → Provider IA)

## Ajouter une commande Telegram

1. Créer `cmd_moncommand(args, cid)` dans `telegram_bot.py`
2. L'enregistrer dans le dict `COMMANDS` en bas du fichier
3. L'ajouter dans `cmd_help()` (texte d'aide)
4. Mettre à jour le README (tableau Commandes Telegram)

## Tester

Il n'y a pas encore de suite de tests automatisés. Pour tester :

```bash
# Lancer le bot en local
venv/bin/python3 main.py

# Tester les commandes dans Telegram
/help
/status
/research AAPL
```

Contributions bienvenues pour ajouter des tests unitaires sur `portfolio.py`, `orders.py` et `prices.py`.

## Soumettre une PR

1. Fork le repo
2. Crée une branche : `git checkout -b feature/ma-feature`
3. Commite tes changements avec un message clair
4. Ouvre une Pull Request avec :
   - Ce que ça fait et pourquoi
   - Comment tu l'as testé
   - Screenshots si c'est une nouvelle commande Telegram

## Ce qui est délibérément hors scope

- Passage d'ordres automatique (pas d'API Bourse Direct publique)
- Support des produits dérivés (turbos, warrants)
- Interface web (hors Telegram)

## Questions

Ouvre une [Discussion GitHub](../../discussions) — plus adapté qu'une Issue pour les questions ouvertes.
