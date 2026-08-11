# Plan de refactoring — TradingBot

> Établi le 11/08/2026 après audit complet des 24 modules / 14 640 lignes.
> Objectif : **une seule source de vérité par notion**, factorisation de ce qui
> est réécrit plusieurs fois, **sans rien changer au comportement d'aujourd'hui**.
>
> Chaque constat ci-dessous est mesuré, pas supposé — les commandes de mesure
> sont données pour pouvoir refaire le calcul après chaque phase.

---

## 1. État des lieux

### Signes vitaux

| Mesure | Valeur | Commentaire |
|---|---|---|
| Modules Python | 24 | |
| Lignes | 14 640 | dont **45 % dans 3 fichiers** |
| `telegram_bot.py` | 2 833 | dont **544 lignes de prose** (`/help` + `/tuto`) = 19 % |
| `analysis.py` | 2 268 | prompts + orchestration IA + scan mélangés |
| `autonomous_engine.py` | 1 506 | entrée + trailing + sizing mélangés |
| **Tests** | **0** | aucun test sur du code qui passe des ordres réels |
| Cycles d'import | 6 | contournés par **164 imports locaux** |
| `except Exception` | 69 | dont **32 suivis d'un `pass`** |
| Écritures de `positions.json` | 21 sites | **sans verrou, non atomiques**, 16 threads |

### Ce qui est déjà bien fait — à ne PAS toucher

Le code n'est pas en mauvais état : il est **annoté d'incidents réels** et
plusieurs parties sont déjà exemplaires. Le refactoring doit les préserver
telles quelles :

- **`config.py` — le modèle de frais.** Courtage par tranches + TTF + change,
  vérifié au centime contre les PRU réels de BD. C'est déjà LA source unique,
  correctement utilisée par `analysis`, `autonomous_engine` et `backtest`.
- **`_quant_screen` — le précalcul en lot.** Le piège N+1 (un an d'historique
  par ticker) est déjà évité par `precomputed`. Ne pas y toucher.
- **`playwright_session.py` — le singleton à file d'attente.** Un seul Chromium,
  un seul thread worker : c'est ce qui rend les accès BD sérialisables.
- **Le dispatch `COMMANDS`** — un dict nom → handler, c'est le bon patron.
  Seules ses *métadonnées* sont dispersées (voir §2.3).
- **Les commentaires d'incident** (NVDA.PA, BAC à nu, AF.PA, 403 du trailing…).
  C'est la mémoire du projet. Un refactoring qui les efface détruit plus de
  valeur qu'il n'en crée : **ils se déplacent avec le code**.

---

## 2. Les 5 violations de « source unique »

Classées par nuisance **démontrée**, pas par élégance.

### 2.1 « Combien vaut cette position » — recalculé dans 5 modules 🔴

Le même calcul (cours retenu, note de fraîcheur, variation, P&L, symbole de
devise, détection de perf aberrante, problème de cotation) est réécrit dans :

| Lieu | Rendu | Drapeaux affichés |
|---|---|---|
| `telegram_bot.cmd_status` | Telegram | `protected`, `pending_sl`, aberrant, source |
| `monitor.check_positions` | STATUS planifié | `protected`, `pending_sl`, source |
| `analysis._portfolio_snapshot` | **prompt IA** | **aucun** |
| `dashboard.build_data` + son JS | web | `protected`, `hold`, estimé |
| `stats.py` | bilan | — |

**Ce n'est pas cosmétique, c'est une erreur de fond active.**
`grep -n protected analysis.py` → **0 occurrence**. Le snapshot qui sert de
« SOURCE DE VÉRITÉ » à l'IA (briefing matinal, swap hebdo, scan) présente les
SL/TP comme des faits, **sans jamais dire qu'aucun ordre ne les porte**.
Du 31/07 au 05/08, pendant que BAC était réellement à nu, l'IA a raisonné
chaque matin comme s'il était protégé. Le drapeau existait déjà — il n'était
simplement pas dans ce rendu-là.

Les 5 rendus doivent rester distincts (Telegram ≠ prompt ≠ web). C'est le
**calcul** qui doit être unique.

### 2.2 « Sur quelle place se traite ce ticker » — 5 définitions indépendantes 🔴

| Module | Mécanisme | Réponse pour `NVDA` |
|---|---|---|
| `config._suffix` + `CURRENCY_BY_SUFFIX` | suffixe Yahoo | USD |
| `monitor._is_us` | `"." not in ticker` | US |
| `autonomous_engine.market_open_for` | `"." not in ticker` | 15h35–21h55 |
| `portfolio.market_close_expiry` | `"." not in ticker` | clôture 21h55 |
| `sync_engine.MIC_MARKETS` | MIC de BD | XNGS → `""`, USD |

Cette dispersion **a déjà coûté** : le défaut « place inconnue → `.PA` » a
enregistré NVDA en `NVDA.PA`, ticker inexistant chez Yahoo — position de
1 233 € invisible du suivi SL/TP, annoncée « COURS SUSPENDU » (03/08/2026).
Le correctif a fusionné deux tables *dans `sync_engine`*, mais les trois
autres définitions vivent toujours ailleurs.

### 2.3 La surface des commandes — 5 copies, déjà désynchronisées 🟠

`COMMANDS` (dispatch, 41) · `BOT_COMMANDS` (menu Telegram, 36) · le texte de
`cmd_help` · les 457 lignes de `/tuto` · les tableaux du README.

Dérive mesurée aujourd'hui :

```
dans dispatch mais PAS dans le menu : capture, non, oui, start, testordre
dans dispatch mais PAS dans /help   : capture, dashboard, fallback, lessons,
                                      reticker, scan_us, start, testordre, help
```

`/dashboard`, `/lessons`, `/reticker`, `/fallback`, `/scan_us` sont des
commandes utilisateur réelles **absentes de `/help`**. La règle CLAUDE.md
« doc à jour à chaque update » demande à un humain de tenir 5 listes à la
main : elle a échoué 5 fois. Ce n'est pas un problème de discipline, c'est un
problème de conception.

### 2.4 Le contrat `.env` 🟠

- `config.py` lit **53** variables, `.env.example` en documente **47** →
  7 lues mais non documentées (`BREAKEVEN_THRESHOLD`, `TP_ALERTS`,
  `MIN_NET_GAIN_FEE_RATIO`, `FALLBACK_TP_MIN_PCT`, `FALLBACK_TP_MAX_PCT`,
  `GMAIL_USER`, `GMAIL_APP_PASSWORD`), 1 documentée non lue par config
  (`AI_FALLBACK_PROVIDERS`, lue directement par `ai_provider`).
- `MAX_POSITIONS = 2` est **codé en dur dans `autonomous_engine.py`** (donc
  non réglable par `.env`) et **re-codé en dur** comme littéral `2` dans
  `telegram_bot.py:2493`. Les deux valent 2 aujourd'hui : la dérive est
  prête, pas encore survenue.
- `playwright` **absent de `requirements.txt`** alors que toute l'intégration
  BD en dépend : une installation neuve suivant le README échoue.

### 2.5 Les primitives d'accès BD 🟠

URLs BD en dur dans 4 modules · `bourse_direct_auth` importe la fonction
**privée** `_dismiss_popups` du reader · 20 sélecteurs CSS dans le reader,
11 dans l'auth.

**Le bug du 11/08 est né exactement là** : la notion « la lecture a-t-elle
abouti ? » n'existait nulle part, donc une liste vide valait preuve d'absence.
Le correctif (`orders_read`) est posé, mais localement — rien n'empêche la
prochaine lecture BD de refaire la même erreur.

---

## 3. Risques structurels (indépendants du DRY, mais bloquants)

### 3.1 Zéro test 🔴
14 640 lignes qui passent des ordres réels, sans un seul test. Toute
factorisation est un pari. **C'est le seul point qui doit être traité avant
tout le reste.**

### 3.2 `positions.json` : pas de verrou, écriture non atomique 🔴
```python
def save(data): POSITIONS_PATH.write_text(json.dumps(...))   # pas de tmp+replace
```
21 sites font `load()` → modifier → `save()`, depuis 16 threads (scheduler,
polling Telegram, worker Playwright, serveur HTTP du dashboard, `Timer`s).
Deux conséquences réelles :
- **mise à jour perdue** : `sync()` charge l'état, lit BD, sauvegarde — un
  `/sl` passé entre-temps est écrasé sans bruit ;
- **fichier tronqué** si le process meurt pendant l'écriture — c'est TOUT
  l'état du portefeuille.

> ⚠️ Honnêteté : le correctif posé aujourd'hui (relecture de confirmation)
> a **allongé cette fenêtre** de ~10 s dans le cas « position vue sans
> protection » — `load()` ligne 110, relecture ligne 551, `save()` ligne 622.
> Rare et sans conséquence observée, mais c'est un argument de plus pour la
> phase 1.

### 3.3 32 `except: pass` 🟠
C'est la forme exacte du bug d'aujourd'hui : trois chemins d'échec silencieux
produisaient une liste vide indiscernable d'un carnet vide. Chaque `pass` est
un endroit où le bot peut mentir sans le savoir.

---

## 4. Plan d'action

Règle générale : **une phase = une série de commits, bot redémarré et observé
une journée complète avant la phase suivante.** Jamais deux phases en vol.

---

### Phase 0 — Le filet de sécurité *(prérequis absolu)*
**Aucun changement de comportement. Rien d'autre ne doit être tenté avant.**

1. **`pytest` + `tests/`** — tests de *caractérisation* : ils gèlent la sortie
   **actuelle** des fonctions pures, donc ils verrouillent « l'efficacité et la
   stabilité d'aujourd'hui » au sens littéral demandé. Cibles (toutes pures,
   donc testables sans réseau ni Playwright) :
   - `config` : `brokerage_fee`, `order_fees`, `roundtrip_fee`, `_ttf_liable`
   - `autonomous_engine` : `compute_position_size`, `trailing_target`, `tp_progress`
   - `bourse_direct_reader` : `_parse_position`, `_parse_order`, `_parse_float`,
     `find_account_nc`
   - `portfolio` : `best_price`, `quote_problem`, `market_close_expiry`
   - `sync_engine` : le scénario protection (le harnais écrit aujourd'hui,
     6 cas, à verser dans `tests/`)
   - `bourse_direct_orders` : `parse_validity`, `_round_to_tick`
2. **`./bot.sh test`** = `py_compile` + `pytest`, à lancer avant chaque commit.
3. **Dette triviale** : `playwright` dans `requirements.txt`, les 7 variables
   dans `.env.example`, suppression du code mort confirmé
   (`read_portfolio_screenshot`, `scan_sector`, `sl_from_pru`, `tp_from_pru`,
   `prices.get_price`, `_closed_with_context`, `cache_info`, `_needs_otp`) et
   du **doublon `portfolio.update_sl` défini 2×** (lignes 333 et 393, la
   seconde masque la première).

**Effort : ~1 jour.** **Effet : le refactoring devient réversible.**

---

### Phase 1 — Un état, un écrivain 🔴
Seule phase qui change la sémantique d'exécution — elle supprime une classe
de bug qui existe aujourd'hui.

1. `portfolio.save()` **atomique** : écriture dans un `.tmp` puis `os.replace()`.
2. Un `threading.RLock` de module + un gestionnaire de contexte :
   ```python
   with portfolio.mutate() as data:      # load sous verrou
       data["positions"][k]["target_low"] = x
   # save atomique sous le même verrou
   ```
3. Migration des 21 sites, **en commençant par `sync_engine`** (la plus longue
   fenêtre) puis `autonomous_engine` (10 sites).

**Effort : ~1 jour.** **Effet : plus de mise à jour perdue, plus de fichier tronqué.**

---

### Phase 2 — `market.py` : une vérité sur un ticker
Module **feuille** (n'importe aucun module du projet, donc casse zéro cycle) :

```python
suffix(ticker) · mic(ticker) · currency(ticker) · symbol(ticker)
is_us(ticker)  · is_euronext(ticker)
is_open_now(ticker) · close_time_today(ticker) · validity_end(ticker, kind)
yf_ticker(bd_ticker, mic, currency)      # ex-sync_engine.MIC_MARKETS
```

Les 5 définitions actuelles **délèguent** — code déplacé à l'identique, donc
comportement inchangé, et les tests de la phase 0 le prouvent.

**Effort : ~0,5 jour.** **Effet : la classe d'incident NVDA.PA devient impossible.**

---

### Phase 3 — `position_view.py` : un calcul, N rendus 🔴
```python
view(name, cfg, quote=None) -> {
  price, currency, sym, source, note, chg_pct, pnl, pnl_eur,
  hold, protected, trailable, pending_sl, aberrant, problem: (code, msg),
}
```
Les 5 rendus gardent **leur** formatage et cessent de calculer.

**Bénéfice immédiat, indépendant du DRY** : `_portfolio_snapshot` hérite de
`protected` et `pending_sl` → **l'IA cesse de raisonner sur des seuils qui ne
protègent rien** (§2.1). C'est un correctif fonctionnel déguisé en refactoring.

**Effort : ~1 jour.** **Effet : les 5 vues ne peuvent plus diverger.**

---

### Phase 4 — La surface des commandes en données
Une table unique `commands.py` :
```python
Command(name, handler, menu_desc, help_section, usage, example, needs_playwright)
```
`COMMANDS`, `BOT_COMMANDS`, `/help` et le tableau README **se génèrent** depuis
elle. Un test assure qu'aucune commande n'est absente du menu ni de l'aide :
**la dérive mesurée au §2.3 devient impossible**, plus seulement corrigée.

Les 457 lignes de `/tuto` sortent vers `docs/tuto/*.md` lus à l'exécution
(−19 % sur `telegram_bot.py`, et la doc devient relisible/éditable sans
toucher au code).

**Effort : ~1 jour.** **Effet : la règle CLAUDE.md « doc à jour » devient automatique.**

---

### Phase 5 — Découpe des 3 gros modules *(seulement après 0 → 4)*

| Avant | Après |
|---|---|
| `telegram_bot.py` 2 833 | `tg_transport.py` (send/poll/typing/photo) + `handlers/` par domaine (portefeuille, ordres, BD, IA, admin) |
| `analysis.py` 2 268 | `prompt_context.py` (snapshot, macro, leçons) · `ai_flows.py` (briefing, swap, research) · `scan.py` |
| `autonomous_engine.py` 1 506 | `auto_entry.py` · `auto_trailing.py` · `sizing.py` |

Avec `market.py`, `position_view.py` et `portfolio.mutate()` en place, **la
plupart des 6 cycles d'import se dissolvent** → les 164 imports locaux peuvent
remonter en tête de fichier, ce qui rend enfin le graphe de dépendances lisible.

**Effort : ~2 jours.** **À ne tenter qu'avec les tests des phases précédentes.**

---

## 5. Règles à ajouter à CLAUDE.md (issues de cet audit)

1. **Toute conclusion tirée d'une ABSENCE exige la preuve que la lecture a eu
   lieu.** (Leçon du 11/08 généralisée : `orders_read`.)
2. **Cinq notions, cinq sources uniques** — la valorisation d'une position, la
   place d'un ticker, la surface des commandes, le contrat `.env`, l'accès BD.
   Toute nouvelle copie de l'une d'elles est un défaut, pas un raccourci.
3. **Pas de `except: pass`** — au minimum un `print` qui nomme ce qui a échoué.
4. **Une fonction pure nouvelle = un test.** Le coût est de 5 minutes ; le coût
   d'une régression sur un ordre réel ne l'est pas.

---

## 6. Ordre recommandé et effort

| Phase | Effort | Risque | Gain |
|---|---|---|---|
| 0 — filet de sécurité | 1 j | nul | rend tout le reste sûr |
| 1 — état/verrou | 1 j | moyen | supprime une perte de données réelle |
| 3 — `position_view` | 1 j | faible | **corrige le mensonge fait à l'IA** |
| 2 — `market.py` | 0,5 j | faible | ferme la classe NVDA.PA |
| 4 — commandes | 1 j | faible | supprime la dérive de doc |
| 5 — découpe | 2 j | élevé | lisibilité, cycles d'import |

**Chemin le plus rentable si le temps manque : 0 → 1 → 3.** Deux jours et demi
qui suppriment une perte de données, un mensonge à l'IA, et rendent le reste
possible. Les phases 2, 4, 5 sont du confort durable, pas de l'urgence.

---

## Annexe — refaire les mesures

```bash
wc -l *.py | sort -rn                                    # taille
grep -rc "^\s\+import \|^\s\+from " *.py | grep -v ':0'  # imports locaux
grep -rn "except Exception:$" -A1 *.py | grep -c pass    # échecs muets
grep -n protected analysis.py                            # §2.1 : doit renvoyer des lignes
```
Le script de comparaison dispatch/menu/help et celui du contrat `.env` sont
reproduits dans l'historique de la session du 11/08/2026.
