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
MOTIFS_ACHAT_NUL = ("défaut cité par l'IA", "thèse refusée par la porte", "veto Jev",
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
    if decisions.categorie_motif(raison) in MOTIFS_ACHAT_NUL:
        return raison
    return None


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


def ajuster(nom: str, cfg: dict, send_fn) -> bool:
    """Applique plan_protection à une position à thèse cassée. True si un
    remplacement a été lancé."""
    import playwright_session
    import protection_ops
    niveau = protection_ops.stop_gain_minimum(cfg, GAIN_MINI_EUR)
    prix = (prices.get_quote(cfg["ticker"]) or {}).get("price")
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
        send_fn(f"🔴 RÉANALYSE — {nom} : l'achat ne tient plus\n{j['raison']}\n\n"
                f"→ Protection sans couper le potentiel : objectif ramené sous le plus "
                f"haut récent, stop remonté à +{GAIN_MINI_EUR:g} € net dès que la marge "
                f"le permet (vérifié à chaque sync).")
        lignes.append(f"🔴 {nom} — thèse cassée : {j['raison'][:120]}")
        ajuster(nom, (portfolio.get_managed_positions() or {}).get(nom, cfg), send_fn)
    if verbose and lignes:
        send_fn("🔎 RÉANALYSE DES POSITIONS\n" + "\n".join(lignes))
