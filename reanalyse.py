"""
Réanalyse des positions détenues — et sortie au premier gain si l'achat
s'avère mauvais.

Demandé le 14/10/2026 : « il faut que le bot soit autonome dans le cadre d'une
réanalyse où l'achat était en fait nul : il faut qu'il sécurise dès que
possible à 0 € ou 1 € la position ». Cas d'origine : KRT, acheté sur une hausse
portée par un remboursement de droits de douane exceptionnel.

Chaque jour ouvré, pour chaque position gérée (hors HOLD) :
  1. dossier de recherche sourcé du jour (research.deep_research, en cache) ;
  2. Claude confronte la THÈSE D'ACHAT d'origine aux faits du jour et répond
     MAINTENIR ou THESE_CASSEE, avec un fait daté à l'appui ;
  3. thèse cassée → le bot PROTÈGE sans se couper du potentiel (précisé par
     l'utilisateur le 14/10 : « ne pas me couper de potentiel gain, sécuriser
     en positif et baisser le TP de façon raisonnable ») :
       - objectif (TP) abaissé au PLUS HAUT des 3 derniers mois (juste sous
         cette résistance) s'il est plus bas que le TP actuel — jamais sous le
         cours qui rapporte GAIN_MINI_EUR net ;
       - stop remonté au cours qui garantit GAIN_MINI_EUR net DÈS que le cours
         a assez de marge au-dessus (max(ATR, 2 %)) pour qu'une variation
         normale ne le déclenche pas. Vérifié à chaque sync horaire, sans IA
         (`ajuster_cassees`).

Ce n'est pas une sortie sur signal de PRIX (celles-là ont toutes perdu au
backtest du 23/09) : le déclencheur est un FAIT qui invalide la raison
d'achat, et la règle ne vend jamais à perte de son fait.
"""
import os
import re
from datetime import datetime

import pytz

import portfolio
import position_view
import prices
import research

PARIS = pytz.timezone("Europe/Paris")
GAIN_MINI_EUR = float(os.getenv("REVIEW_GAIN_MINI_EUR", "1"))
ACTIF = os.getenv("REVIEW_AUTO", "1") != "0"
# Thèse cassée ET cours à moins de REVIEW_EXIT_PCT % au-dessus du PRU (ou sous
# le PRU) → vente au marché au lieu de la protection. 18/11/2026 : HUM, thèse
# cassée le matin par la règle CRUSH (Medicare Advantage) à +0,7 %, gardé sous
# protection, stop à −7 % touché l'après-midi même. Près du PRU, la
# protection n'a rien à protéger : elle ne fait qu'attendre le stop.
# 0 = désactivé (protection seule, comportement d'avant le 18/11/2026).
SORTIE_PCT = float(os.getenv("REVIEW_EXIT_PCT", "3"))

PROMPT = """Tu réanalyses une position DÉTENUE. Question unique : la raison pour
laquelle elle a été achetée tient-elle encore, au vu des faits du jour ?

SOCIÉTÉ : {nom} ({ticker}) — détenue depuis le {depuis}
THÈSE D'ACHAT D'ORIGINE : {these}
COURS : {cours} (achat {pru}) — {perf}
{tech}
DOSSIER DE RECHERCHE SOURCÉ DU JOUR :
{dossier}

Règles :
- THESE_CASSEE seulement si un FAIT DATÉ du dossier contredit la thèse
  d'origine : croissance en réalité exceptionnelle / non récurrente, guidance
  abaissée, catalyseur annulé ou raté, problème de bilan, révision forte des
  analystes, événement défavorable. Une simple baisse du cours ne suffit PAS.
- Sinon : MAINTENIR.
Réponds EXACTEMENT :
VERDICT : MAINTENIR — [raison en une phrase]
ou
VERDICT : THESE_CASSEE — [le fait daté qui invalide la thèse, et sa source]"""


def juger(nom: str, cfg: dict) -> dict:
    """{"verdict": "MAINTENIR"|"THESE_CASSEE"|None, "raison": str}.
    None = jugement impossible (pas de recherche, réponse illisible) : on ne
    fait RIEN plutôt que d'agir sur un jugement absent."""
    funds = prices.get_fundamentals(cfg["ticker"]) or {}
    dossier = research.deep_research(cfg["ticker"], funds.get("name", ""))
    if not dossier:
        return {"verdict": None, "raison": "recherche du jour indisponible"}
    v = position_view.view(nom, cfg)
    ctx = portfolio.get_entry_context(cfg["ticker"]) or {}
    tech = prices.get_technicals(cfg["ticker"]) or {}
    prompt = PROMPT.format(
        nom=funds.get("name", nom), ticker=cfg["ticker"],
        depuis=(cfg.get("opened_at") or "?")[:10],
        these=ctx.get("thesis") or "(non mémorisée — juge la qualité du dossier d'achat "
                                   "au vu des faits du jour)",
        cours=f"{v['sym']}{v['price']}", pru=f"{v['sym']}{cfg.get('entry_price')}",
        perf=(f"{v['chg_eur']:+.2f}% en euros" if v.get("chg_eur") is not None else ""),
        tech=(f"RSI {tech.get('rsi')} | momentum 1 mois {tech.get('momentum_1m')}% | "
              f"cours vs MM200 {tech.get('ma200_dist_pct')}%"),
        dossier=dossier[:9000])
    try:
        from ai_provider import get_provider
        rep = get_provider("final").complete(prompt, max_tokens=600)
    except Exception as e:
        return {"verdict": None, "raison": f"IA indisponible ({e})"}
    m = re.search(r"VERDICT\s*:\s*(MAINTENIR|THESE[_ ]CASSEE)\s*[—-]?\s*(.*)", rep or "", re.I)
    if not m:
        return {"verdict": None, "raison": "réponse illisible"}
    verdict = "THESE_CASSEE" if "CASSEE" in m.group(1).upper().replace(" ", "_") else "MAINTENIR"
    raison = m.group(2).strip()
    if verdict == "THESE_CASSEE" and len(raison.split()) < 6:
        return {"verdict": None, "raison": "thèse dite cassée sans fait précis — ignoré"}
    return {"verdict": verdict, "raison": raison[:300]}


# Motifs de refus qui disent « l'achat était mauvais » — par opposition aux
# vetos techniques (surchauffe, volatilité, profil) qui disent seulement « pas
# maintenant », et qu'un gagnant en pleine hausse déclenche aussi.
# Le veto Jev n'y figure plus (13/11/2026) : il juge si NOS données de
# recherche contiennent les preuves, pas la société. Yahoo en panne → aucune
# news lue → « preuves absentes » → HUM marqué « thèse cassée » et son TP
# ramené de 663,77 à 453,25 $, le jour même où le jugement de fond (modèle
# principal) disait MAINTENIR. Un juge plus léger ne casse pas une thèse.
MOTIFS_ACHAT_NUL = ("défaut cité par l'IA", "thèse refusée par la porte",
                    "au-dessus de l'objectif analystes")


def achat_nul_aujourdhui(nom: str, cfg: dict) -> str | None:
    """L'analyse complète du jour refuserait-elle cet achat, pour une raison de
    FOND ? Raison du refus, ou None. 14/10/2026 : la question « un fait casse-
    t-il la thèse ? » maintenait KRT, alors que l'analyse du jour le refusait
    (« hausse portée par remboursement exceptionnel ») — un défaut déjà
    présent à l'achat, pas un fait nouveau."""
    import analysis
    import decisions
    try:
        r = analysis.validate_candidate(cfg["ticker"], mode="review",
                                        cash=portfolio.get_cash())
    except Exception as e:
        print(f"[reanalyse] validation {nom} : {e}")
        return None
    if r.get("verdict") == "ACHAT" or not r.get("price"):
        return None
    raison = r.get("reason") or ""
    if decisions.categorie_motif(raison) in MOTIFS_ACHAT_NUL and not motif_de_timing(raison):
        return raison
    return None


# Mots d'un refus qui porte sur le MOMENT d'achat, pas sur la société. Le
# motif « défaut cité par l'IA » attrape tout ce que l'IA écrit elle-même :
# « surchauffe sous résistance, aucun repli » y tombait et marquait BTSG
# « thèse cassée » (11/11/2026) — alors que le jugement de fond du même matin
# disait MAINTENIR (T3 en hausse, guidance relevée). Un gagnant en pleine
# hausse déclenche toujours ce genre de refus : il ne prouve pas un achat nul.
_MOTS_TIMING = ("surchauff", "résistance", "resistance", "repli", "pullback",
                " rsi", "rsi ", "suracheté", "surachat", "extension", "étiré", "etire",
                "trop haut", "plus haut", "sommet", "momentum", "point d'entrée",
                "entrée tardive", "attendre", "timing", "après l'envolée")


def motif_de_timing(raison: str) -> bool:
    r = (raison or "").lower()
    return any(m in r for m in _MOTS_TIMING)


def plus_haut_recent(ticker: str) -> float | None:
    """Plus haut des 3 derniers mois, dans l'unité du cours (pence → livres)."""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="3mo")["High"].dropna()
        return float(h.max()) / prices.price_divisor(ticker) if len(h) else None
    except Exception:
        return None


def plan_protection(cfg: dict, prix: float, niveau: float,
                    haut: float | None, atr_pct: float | None) -> tuple[float, float] | None:
    """(nouveau SL, nouveau TP) pour une position à thèse cassée, ou None si
    rien ne change. `niveau` = cours qui rapporte GAIN_MINI_EUR net."""
    sl, tp = cfg.get("target_low") or 0, cfg.get("target_high") or 0
    new_sl, new_tp = sl, tp
    # Objectif posé à la main (/tp) : décision de l'utilisateur, jamais
    # rabaissée par la réanalyse (BTSG 12/11/2026 : 100.48 → 73.16 la veille
    # de... rien du tout, sur une thèse dite cassée pour un motif de timing).
    if cfg.get("tp_manuel"):
        haut = None
    # Objectif raisonnable : sous la résistance des 3 derniers mois, si elle est
    # au-dessus du cours (sinon le titre est sur ses plus hauts : on garde le TP).
    if haut and prix and haut > prix * 1.02 and (not tp or haut * 0.995 < tp):
        new_tp = round(max(haut * 0.995, niveau), 2)
    # Gain minimum garanti dès qu'il y a de la marge.
    marge = max(atr_pct or 0, 2.0) / 100
    if prix >= niveau * (1 + marge) and sl < niveau:
        new_sl = niveau
    if new_sl == sl and (not tp or new_tp >= tp * 0.995):
        return None
    return new_sl, new_tp


def _marquer(nom: str, raison: str) -> None:
    d = portfolio.load()
    if nom in d.get("positions", {}):
        d["positions"][nom]["these_cassee"] = {
            "date": datetime.now(PARIS).strftime("%Y-%m-%d"), "raison": raison}
        portfolio.save(d)


def sortie_immediate(cfg: dict, prix: float | None) -> bool:
    """Thèse cassée + cours trop près du PRU pour qu'il y ait un gain à
    protéger → vendre plutôt qu'attendre le stop. Vrai aussi sous le PRU :
    la thèse ne tient plus, le stop vendrait plus bas."""
    entry = cfg.get("entry_price") or 0
    if SORTIE_PCT <= 0 or not (entry and prix):
        return False
    return prix < entry * (1 + SORTIE_PCT / 100)


# Une annonce et une tentative de vente par position et par jour : la sync
# horaire rappelle ajuster() tant que la position existe.
_sortie_annoncee: dict[str, str] = {}
_sortie_tentee: dict[str, str] = {}


def vendre_these_cassee(nom: str, cfg: dict, prix: float, send_fn) -> bool:
    """Vente au marché d'une position à thèse cassée près du PRU. True si la
    vente a été traitée (envoyée, ou reportée avec un message) — l'appelant
    ne pose alors pas de protection par-dessus."""
    import bot_mode
    import market
    import playwright_session
    import stale_exit
    jour = datetime.now(PARIS).strftime("%Y-%m-%d")
    entry = cfg["entry_price"]
    ecart = (prix / entry - 1) * 100
    if _sortie_tentee.get(nom) == jour:
        return True
    pourquoi = (f"cours {prix:g} ({ecart:+.1f} % du PRU), à moins de "
                f"{SORTIE_PCT:g} % au-dessus : rien à protéger, le stop "
                f"({cfg.get('target_low')}) vendrait plus bas")
    pret = bot_mode.is_playwright() and playwright_session.is_connected()
    if not (pret and market.is_open_now(cfg["ticker"])):
        if _sortie_annoncee.get(nom) != jour:
            _sortie_annoncee[nom] = jour
            attente = ("marché fermé — vente à l'ouverture (sync horaire)" if pret else
                       "session Bourse Direct non connectée — /connect, la vente "
                       "partira à la sync suivante")
            send_fn(f"🔴 {nom} (thèse cassée) : {pourquoi}.\n→ Vente au marché prévue : "
                    f"{attente}. La protection actuelle reste en place d'ici là.")
        return True
    _sortie_tentee[nom] = jour
    send_fn(f"🔴 SORTIE — {nom} (thèse cassée) : {pourquoi}.\nAnnulation de la "
            f"protection puis vente au marché…")
    if not stale_exit._annuler_protections(cfg, nom):
        send_fn(stale_exit.message_echec_annulation(cfg, nom, "vente"))
        return True
    if stale_exit._vendre_au_marche(cfg, nom, send_fn):
        send_fn(f"💰 {nom} : vente au marché envoyée (thèse cassée). Le sync "
                f"enregistrera la clôture.")
    else:
        send_fn(f"🚨 {nom} : protection annulée mais VENTE NON ENVOYÉE — position "
                f"SANS STOP.\n/ordre vendre {cfg['ticker']} {abs(int(cfg['qty']))} marche")
    return True


def ajuster(nom: str, cfg: dict, send_fn) -> bool:
    """Position à thèse cassée : vente si le cours est près du PRU
    (`sortie_immediate`), sinon plan_protection. True si une action a été
    lancée."""
    import playwright_session
    import protection_ops
    prix = (prices.get_quote(cfg["ticker"]) or {}).get("price")
    if sortie_immediate(cfg, prix):
        return vendre_these_cassee(nom, cfg, prix, send_fn)
    niveau = protection_ops.stop_gain_minimum(cfg, GAIN_MINI_EUR)
    if not (niveau and prix):
        return False
    plan = plan_protection(cfg, prix, niveau, plus_haut_recent(cfg["ticker"]),
                           (prices.get_technicals(cfg["ticker"]) or {}).get("atr_pct"))
    if not plan:
        return False
    sl, tp = plan
    morceaux = []
    if sl != cfg.get("target_low"):
        morceaux.append(f"stop {cfg.get('target_low')} → {sl} (au moins "
                        f"+{GAIN_MINI_EUR:g} € net garantis)")
    if tp != cfg.get("target_high"):
        morceaux.append(f"objectif {cfg.get('target_high')} → {tp} (sous le plus haut "
                        f"des 3 derniers mois)")
    quoi = " ; ".join(morceaux)
    if not playwright_session.is_connected():
        send_fn(f"⚠️ {nom} (thèse cassée) : session Bourse Direct non connectée — "
                f"à poser : {quoi}.\n/connect, le bot réessaiera à la prochaine sync.")
        return False
    protection_ops.remplacer_protection(
        nom, cfg, sl, tp, send_fn, "reanalyse",
        lambda od, adj: send_fn(f"✅ {nom} (thèse cassée) : {quoi} — posé sur BD."))
    return True


def ajuster_cassees(send_fn) -> None:
    """Sans IA, à chaque sync horaire : remonte le stop des positions à thèse
    cassée dès que la marge le permet, et abaisse leur objectif si besoin."""
    if not ACTIF:
        return
    for nom, cfg in portfolio.get_managed_positions().items():
        if cfg.get("these_cassee") and not cfg.get("hold"):
            try:
                ajuster(nom, cfg, send_fn)
            except Exception as e:
                print(f"[reanalyse] ajustement {nom} : {e}")


def cycle(send_fn, verbose: bool = False) -> None:
    """Réanalyse les positions gérées ; thèse cassée → protection (ajuster)."""
    if not ACTIF:
        return
    positions = portfolio.get_managed_positions()
    if not positions:
        if verbose:
            send_fn("Aucune position gérée à réanalyser.")
        return
    lignes = []
    for nom, cfg in positions.items():
        if cfg.get("hold") or not cfg.get("qty"):
            continue
        if cfg.get("these_cassee"):
            lignes.append(f"🔴 {nom} — thèse cassée le {cfg['these_cassee'].get('date')} : "
                          f"{cfg['these_cassee'].get('raison', '')[:120]}")
            ajuster(nom, cfg, send_fn)
            continue
        j = juger(nom, cfg)
        print(f"[reanalyse] {nom} : {j}")
        if j["verdict"] == "MAINTENIR":
            nul = achat_nul_aujourdhui(nom, cfg)
            if nul:
                j = {"verdict": "THESE_CASSEE",
                     "raison": f"l'analyse complète du jour refuserait cet achat : {nul}"}
        if j["verdict"] != "THESE_CASSEE":
            lignes.append(f"{'✅' if j['verdict'] == 'MAINTENIR' else '⬜'} {nom} — "
                          f"{j['raison'][:140]}")
            continue
        _marquer(nom, j["raison"])
        prix = (prices.get_quote(cfg["ticker"]) or {}).get("price")
        if sortie_immediate(cfg, prix):
            suite = (f"→ Cours à moins de {SORTIE_PCT:g} % au-dessus du PRU : "
                     f"vente au marché (dès que le marché est ouvert).")
        else:
            suite = (f"→ Gain à protéger : objectif ramené sous le plus haut récent "
                     f"(sauf objectif posé avec /tp), stop remonté à "
                     f"+{GAIN_MINI_EUR:g} € net dès que la marge le permet (vérifié "
                     f"à chaque sync).")
        send_fn(f"🔴 RÉANALYSE — {nom} : l'achat ne tient plus\n{j['raison']}\n\n{suite}")
        lignes.append(f"🔴 {nom} — thèse cassée : {j['raison'][:120]}")
        ajuster(nom, (portfolio.get_managed_positions() or {}).get(nom, cfg), send_fn)
    if verbose and lignes:
        send_fn("🔎 RÉANALYSE DES POSITIONS\n" + "\n".join(lignes))
