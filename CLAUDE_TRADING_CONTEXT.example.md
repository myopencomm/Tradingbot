# CONTEXTE TRADING PERSONNEL

> Copiez ce fichier vers CLAUDE_TRADING_CONTEXT.md et remplissez-le avec votre situation réelle.
> Ce fichier est chargé à chaque analyse IA. Il est dans .gitignore : jamais publié sur GitHub.

## ⚠️ Règle d'or : n'y mettez QUE ce que le bot ne sait pas déjà

Le bot injecte automatiquement dans chaque prompt IA :
- **Vos positions, cash, SL/TP en temps réel** (depuis positions.json) — ne les recopiez PAS ici, une copie périmée contredirait les données live
- **L'historique de vos trades** (trades_history.json, visible via /stats)
- **Les règles chiffrées** (stop-loss, take-profit, univers de marchés — voir .env : DEFAULT_SL_PCT / DEFAULT_TP_PCT)

Ce fichier sert au reste : votre objectif, vos règles personnelles, vos contraintes.

## OBJECTIF

Décrivez votre situation et ce que vous cherchez à accomplir.

Exemple : "Récupérer une moins-value latente de X€ sur compte CTO Bourse Direct.
Trader uniquement avec le cash disponible."

## RÈGLES PERSONNELLES

Ce que l'IA doit respecter et qui n'est pas dans le code.

Exemple :
- Position longue durée à CONSERVER même si le SL est franchi (ex : ILMN)
- Positions bloquées à ignorer dans les décisions (ex : liquidation judiciaire)
- Éviter les biotechs phase 1/2 (trop spéculatif)
- Ne pas ouvrir plus de 3 positions simultanées
- Cash minimum à conserver : 100€
- Ne jamais considérer un trade comme exécuté sans ma confirmation explicite

## CONTRAINTES (optionnel)

Exemple :
- Pas d'effet de levier
- Pas d'actions hors Euronext / Euronext Growth sauf exception justifiée
