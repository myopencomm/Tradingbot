# Sécurité — TradingBot

Ce document décrit la posture de sécurité du projet : ce qui a été audité, les
mesures en place, et les bonnes pratiques à respecter en tant qu'utilisateur.

> **Nature du projet.** TradingBot est un outil **auto-hébergé, mono-utilisateur**.
> Il tourne sur *votre* machine, pilote *votre* compte Bourse Direct via *votre*
> session, et n'expose aucun service multi-locataire. La surface d'attaque est
> donc essentiellement locale : accès à votre machine, à votre `.env`, ou à votre
> bot Telegram.

---

## Résumé de l'audit

Audit réalisé le **2026-07-08** sur l'ensemble du dépôt. Domaines couverts :
secrets & identifiants, injection de code, gestion des fichiers locaux, journaux,
permissions, exposition réseau, sécurité du bot Telegram.

| Domaine | Résultat | Action |
|---|---|---|
| Secrets hardcodés (code, commentaires, historique git) | ✅ Aucun | — |
| `.gitignore` des fichiers sensibles | ✅ Correct | — |
| `.env` / `positions.json` jamais commités | ✅ Vérifié sur tout l'historique | — |
| `eval` / `exec` / `os.system` / `shell=True` / `pickle` | ✅ Aucun | — |
| Injection de commande (subprocess) | ✅ Args en liste, jamais de shell | — |
| **Autorisation des commandes Telegram** | 🔴 → ✅ **Corrigé** | Allowlist expéditeur |
| Permissions `.env` (`-rw-r--r--`) | 🟠 → ✅ **Corrigé** | `chmod 600` forcé |
| Dashboard exposé au réseau sans auth | 🟠 → ✅ **Corrigé** | Jeton obligatoire |
| Secrets dans les journaux | ✅ Aucun secret imprimé | — |

---

## Vulnérabilités corrigées

### 🔴 CRITIQUE — Aucune autorisation des commandes Telegram

**Avant.** `_handle_message()` traitait les messages de **n'importe quel** chat.
`CHAT_ID` ne servait que de destinataire par défaut des messages *sortants*,
jamais à filtrer les *entrants*. Quiconque découvrait le `@username` du bot
pouvait : lire le portefeuille (`/status`, `/dashboard`), **passer des ordres
réels** (`/ordre acheter …`, `/oui`), activer le trading autonome (`/auto on`),
et surtout **fournir le code 2FA** de connexion Bourse Direct (le relais OTP
acceptait un code à 6 chiffres de n'importe quel expéditeur).

**Impact.** Prise de contrôle du compte de courtage : ordres frauduleux, vidage
de trésorerie, exfiltration des positions.

**Correctif.** Allowlist stricte des expéditeurs, vérifiée avant tout traitement :

```python
# config.py — CHAT_ID="111,222" pour autoriser plusieurs comptes
AUTHORIZED_CHAT_IDS = {c.strip() for c in (CHAT_ID or "").split(",") if c.strip()}

# telegram_bot.py — tout en haut de _handle_message()
if AUTHORIZED_CHAT_IDS and cid not in AUTHORIZED_CHAT_IDS:
    print(f"[SECURITY] message ignoré d'un chat non autorisé : {cid}")
    return
```

Ce filtre couvre **toutes** les entrées : commandes, photos, CSV, et le relais
OTP — un intrus ne peut plus rien déclencher.

### 🟠 MOYEN — Permissions `.env` trop ouvertes

**Avant.** `.env` était `-rw-r--r--` (lisible par tous les comptes de la machine).
Il contient le token Telegram, les identifiants Bourse Direct, le mot de passe
d'application Gmail, et les clés IA.

**Correctif.** `bot.sh` force `chmod 600` sur les fichiers sensibles à chaque
démarrage (`.env`, `positions.json`, `trades_history.json`,
`CLAUDE_TRADING_CONTEXT.md`, `bot_state.json`, `nav_history.json`) —
lecture/écriture réservées au propriétaire.

### 🟠 MOYEN — Dashboard exposé au réseau sans authentification

**Avant.** Avec `DASHBOARD_BIND=0.0.0.0` (accès Tailscale/LAN), le dashboard
servait le portefeuille complet à quiconque atteignait le port, sans contrôle.

**Correctif.** Un jeton (`DASHBOARD_TOKEN`) devient **obligatoire** dès que le
bind n'est pas local. Sans jeton, le serveur **se replie de force sur
`127.0.0.1`** et le signale. L'accès distant se fait via `?token=…` (mémorisé en
cookie `SameSite=Strict` pour la PWA). Un accès sans jeton renvoie **HTTP 403**.

```bash
DASHBOARD_BIND=0.0.0.0
DASHBOARD_TOKEN=<openssl rand -hex 16>
# → http://<ip-tailscale>:8642/?token=<jeton>
```

---

## Mesures de sécurité en place

### Secrets & identifiants
- **Tout secret vit dans `.env`** (jamais dans le code) — chargé via `python-dotenv`.
- `.env`, `positions.json`, `trades_history.json`, `CLAUDE_TRADING_CONTEXT.md`,
  `nav_history.json`, `*.log` sont dans `.gitignore` et **n'ont jamais été commités** (vérifié sur
  l'historique complet via `git log --all --diff-filter=A`).
- `.env.example` ne contient que des placeholders — aucune vraie valeur.
- Le démarrage n'imprime que des booléens de présence (`Telegram : OK`), jamais
  la valeur d'un secret.
- Le code 2FA Bourse Direct transite en mémoire (relais éphémère), n'est ni
  journalisé ni persisté.

### Exécution & entrées
- **Aucun** `eval`, `exec`, `os.system`, `shell=True`, ni `pickle`.
- Les appels `subprocess` (git) passent leurs arguments en **liste** (pas de
  shell) — pas d'injection de commande possible.
- Les entrées Telegram sont parsées en tokens ; les montants/quantités sont
  convertis via `float()`/`int()` sous `try/except`.
- Les ordres réels exigent une **double confirmation** (`/ordre …` → récap →
  `/oui`) avec expiration à 120 s.
- Trois chemins passent des ordres **sans confirmation** — par construction,
  puisqu'ils s'exécutent sans utilisateur devant l'écran : le mode autonome
  (entrées), le trailing (remontée du SL) et le renouvellement des protections
  (`protection_renewal`, repose d'un SL/TP expiré). Tous trois sont bornés au
  périmètre du portefeuille existant : ils ne peuvent que **protéger ou solder**
  une position déjà détenue, ou entrer dans la limite du budget autonome. Le
  renouvellement, en particulier, exige deux preuves concordantes (échéance
  dépassée **et** deux lectures du carnet sans ordre à seuil) avant de reposer
  quoi que ce soit — reposer sur une lecture ratée créerait un doublon de vente.

### Données & journaux
- Fichiers créés en local : `positions.json`, `trades_history.json`,
  `bot_state.json`, `sentiment_cache.json`, `api_costs.json`,
  `macro_summary_cache.json`, `nav_history.json`, `tradingbot.log` — tous locaux,
  aucun envoi vers un tiers hormis les API nécessaires.

### Clés API via Telegram (`/fallback`)
- `/fallback PROVIDER CLE` fait transiter la clé par les serveurs Telegram
  (chiffrement client-serveur, pas E2E). Compromis assumé pour la simplicité ;
  atténuations : le **message est supprimé du chat** immédiatement après
  traitement (il ne reste pas dans l'historique), la clé est **testée puis
  stockée uniquement dans `.env` local** (gitignoré, permissions 600), jamais
  ré-affichée en clair (masquée `…xxxx`), et la commande n'est acceptée que
  depuis les `AUTHORIZED_CHAT_IDS`. Pour une confidentialité maximale,
  éditez `.env` à la main à la place.
- Permissions `600` sur les fichiers sensibles (forcées au démarrage).
- Aucun secret ni PII n'est écrit dans `tradingbot.log`.

### Réseau & déploiement
- Le bot **sort** vers : API Telegram, Yahoo Finance, le provider IA configuré,
  Bourse Direct (session Playwright), Gmail IMAP (optionnel). Aucun port entrant
  n'est ouvert par le bot lui-même.
- Le **dashboard** est le seul service entrant : `127.0.0.1` par défaut ;
  exposition réseau conditionnée à un jeton.
- Accès distant recommandé via **Tailscale** (réseau privé chiffré) — jamais de
  redirection de port vers l'Internet public.

### Données transmises aux API
| Destinataire | Données envoyées |
|---|---|
| Telegram | Vos messages, analyses, graphiques (chiffré en transit) |
| Provider IA | Contexte de marché, tickers, votre contexte personnel de trading |
| Yahoo Finance | Tickers interrogés (aucune donnée personnelle) |
| Bourse Direct | Vos identifiants (session) + ordres — via HTTPS, comme le site |

---

## Checklist des bonnes pratiques

- [x] Aucun secret dans le code ou l'historique git
- [x] `.gitignore` couvre secrets, portefeuille, logs, état Playwright
- [x] Secrets chargés depuis l'environnement (`.env`)
- [x] Permissions `600` sur les fichiers sensibles (forcées au démarrage)
- [x] Autorisation stricte des commandes Telegram (allowlist expéditeur)
- [x] Relais 2FA protégé par la même allowlist
- [x] Double confirmation + timeout sur les ordres réels
- [x] Aucun `eval`/`exec`/`shell=True`/`pickle`
- [x] `subprocess` sans shell (arguments en liste)
- [x] Aucun secret dans les journaux
- [x] Dashboard local par défaut ; jeton obligatoire si exposition réseau
- [x] Accès distant via réseau privé (Tailscale), pas d'exposition Internet
- [x] `.env.example` sans valeurs réelles

---

## Recommandations à l'utilisateur

1. **`CHAT_ID` doit être renseigné.** Sans lui, l'allowlist est vide et le filtre
   ne peut pas s'appliquer — définissez-le avec votre ID (via `/start`, ou
   [@userinfobot](https://t.me/userinfobot)).
2. **Ne partagez jamais votre token Telegram.** S'il fuite, révoquez-le via
   [@BotFather](https://t.me/BotFather) (`/revoke`) et régénérez-le.
3. **N'exposez jamais le dashboard sur l'Internet public.** Tailscale + jeton, ou
   local uniquement.
4. **Activez le 2FA** sur votre compte Bourse Direct, Gmail et Telegram.
5. **Sauvegardez `.env` hors du dépôt**, dans un gestionnaire de secrets.
6. Utilisez un **mot de passe d'application** Gmail dédié (jamais le mot de passe
   principal), révocable indépendamment.

---

## Signaler une vulnérabilité

Ouvrez une [Security Advisory](../../security/advisories) privée sur GitHub
plutôt qu'une Issue publique. Merci de ne pas divulguer publiquement avant
correction.

---

*Audit et corrections : 2026-07-08. Ce document reflète l'état du dépôt à cette
date ; il n'offre aucune garantie absolue — la sécurité dépend aussi de votre
environnement d'exécution.*
