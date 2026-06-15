# Analyse macro sectorielle — EXEMPLE

Ce fichier est OPTIONNEL. S'il existe sous le nom `macro_analysis.md`,
le bot l'injecte dans les prompts de /scan et du briefing matinal pour
orienter la sélection de candidats vers des secteurs structurellement porteurs.

IMPORTANT : ce document est un POINT DANS LE TEMPS. Il influence l'IA
mais ne remplace pas les données de marché live ni les règles de validation.
Mettez-le à jour dès que votre analyse macro change.

---

## Date de rédaction
2026-06-15

## Horizon d'analyse
Court terme (fin 2026) et moyen terme (18 mois)

## Secteurs porteurs — fin 2026

1. IA et infrastructure de calcul : très fort
   - GPU, HBM, data centers, agents IA, inference
   - Chercher le "goulet d'étranglement" : électricité, refroidissement, optique

2. Electricité et réseaux : très fort
   - Transformateurs, stockage batterie, nucléaire, câbles HT

3. Défense et drones : fort à très fort
   - Munitions, contre-drones, guerre électronique, cyber militaire

4. Cybersécurité : fort
   - Identité/IAM, XDR, zero trust, sécurité IA

## Secteurs à éviter ou surveiller avec prudence

- Immobilier commercial : taux réels pesants
- Retail discrétionnaire : consommation sous pression en Europe

## Scénario macro principal

Description brève du scénario (ex: fragmentation géopolitique, taux en baisse graduelle,
croissance mondiale 2.5%...).

## Règles d'application pour le bot

- À qualification technique égale, favoriser les candidats dans les secteurs porteurs ci-dessus
- La logique "goulet d'étranglement" : identifier la contrainte rare du secteur vedette
- Les règles ANALYSIS_RULES du bot restent prioritaires (RSI, couteau, TP cap, etc.)
