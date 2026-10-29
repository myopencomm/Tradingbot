"""
Pronostics de résultats trimestriels — essai FANTÔME (jamais d'achat).

Demandé le 29/10/2026 : « étudier les actions qui publient bientôt et prédire
avec les infos et d'autres sources si elles vont bien réagir ». Une étude sur
3 018 publications US (211 titres, 4 ans) disait non : 49 % de hausses, ±6,9 %
de mouvement moyen, et aucun indice public (surprise ou réaction précédentes,
course des 21 séances avant) n'est corrélé à la réaction (|r| < 0,03). Même
connaître le BPA réel ne donne que 55 % de hausses. Mais l'étude ne lisait pas
les news : c'est ce que l'IA ajoute, et c'est ce que cet essai mesure.

Chaque matin, pour les titres du screen momentum (et les positions détenues)
qui publient dans les 4 jours, l'IA fait une recherche web dédiée aux
résultats puis pronostique HAUSSE ou BAISSE. Rien n'est acheté. Après la
publication, la réaction est mesurée de la clôture de la veille à la clôture
du lendemain (couvre une publication avant l'ouverture comme après la
clôture). `/pronostics` en donne le bilan. L'IA n'aide que si elle dépasse
50 % de bons sens avec ≥ 30 cas et un IC 95 % entièrement au-dessus de 50 %.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path

JOURNAL_PATH = Path(__file__).with_name("earnings_bets_journal.json")
HORIZON_JOURS = 4       # publications prises en compte : J+1 à J+4 (calendaire)
MAX_PAR_JOUR = 3        # pronostics par jour (recherche web ~2-4 min chacun)
TOP_SCREEN = 40         # titres du screen momentum examinés
TAILLE_REF = 900.0      # taille de ligne de référence pour chiffrer les frais
MIN_CAS = 30

RESEARCH_PROMPT = """Fais une recherche web sur {nom} ({ticker}), qui publie ses
résultats trimestriels le {jour}. Date du jour : {date}. Chaque fait : date +
source. Si une rubrique n'a rien de vérifiable, écris « rien de vérifié ».
N'invente rien.

CONSENSUS (BPA et chiffre d'affaires attendus, nombre d'analystes, révisions
des 30 derniers jours à la hausse ou à la baisse)

GUIDANCE (ce que la direction a annoncé au trimestre précédent, l'a-t-elle
relevée ou abaissée depuis)

HISTORIQUE (battu / manqué le consensus aux 4 derniers trimestres, et réaction
du cours à chaque fois)

INDICES DU TRIMESTRE (résultats déjà publiés par les concurrents, clients ou
fournisseurs ce trimestre ; données sectorielles ; annonces de la société)

ATTENTES DU MARCHÉ (mouvement implicite des options si disponible, course du
cours avant la publication, positionnement, ce qu'il faut pour surprendre)
"""

VERDICT_PROMPT = """Tu pronostiques la réaction boursière de {ticker} à ses
résultats du {jour} : de la clôture de la veille à la clôture du lendemain,
le cours sera-t-il plus HAUT ou plus BAS ?

Rappel de base : sur 3 018 publications US récentes, 49 % de hausses, 69 % des
sociétés battent le consensus mais seules 55 % de celles-là montent — le cours
réagit à la guidance et aux attentes implicites, pas au chiffre affiché.
Course du cours sur 21 séances avant le pronostic : {course:+.1f} %.

DOSSIER DE RECHERCHE
{dossier}

Réponds UNIQUEMENT par un objet JSON sur une ligne :
{{"sens": "hausse" ou "baisse", "confiance": entier de 50 à 90, "raison": "une phrase"}}
Confiance 50 = pile ou face. Ne dépasse 65 que sur un indice précis et sourcé.
"""


def _load() -> list[dict]:
    try:
        return json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    JOURNAL_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=0),
                            encoding="utf-8")


def a_pronostiquer(tickers: list[str], deja: set[tuple[str, str]],
                   aujourdhui: date | None = None, prochaine=None) -> list[tuple[str, date]]:
    """(ticker, date de publication) des titres qui publient de J+1 à
    J+HORIZON_JOURS et n'ont pas encore de pronostic pour cette date. J+1 au
    plus tôt : le pronostic doit précéder la clôture de la veille."""
    aujourdhui = aujourdhui or date.today()
    if prochaine is None:
        from news_alert import fetch_next_earnings as prochaine
    out = []
    for t in dict.fromkeys(tickers):
        d = prochaine(t)
        if d is None or not (aujourdhui < d <= aujourdhui + timedelta(days=HORIZON_JOURS)):
            continue
        if (t, d.isoformat()) not in deja:
            out.append((t, d))
    return out


def _dossier(ticker: str, nom: str, jour: date) -> str:
    """Recherche web dédiée aux résultats : CLI claude (abonnement), sinon
    Gemini + Google Search. Jamais la clé API Anthropic (essai gratuit)."""
    import research
    prompt = RESEARCH_PROMPT.format(nom=nom or ticker, ticker=ticker,
                                    jour=jour.strftime("%d/%m/%Y"),
                                    date=datetime.now().strftime("%d/%m/%Y"))
    txt = ""
    try:
        import ai_provider
        if ai_provider.cli_path():
            txt = ai_provider.ClaudeCliProvider().research(prompt)
    except Exception as e:
        print(f"[pronostics] recherche CLI {ticker} : {e}")
    if len(txt or "") < 300:
        txt = research._deep_gemini(prompt, ticker)
    return txt if len(txt or "") >= 300 else ""


def parse_verdict(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except Exception:
        return None
    sens = str(v.get("sens", "")).lower()
    if sens not in ("hausse", "baisse"):
        return None
    try:
        conf = max(50, min(90, int(v.get("confiance", 50))))
    except Exception:
        conf = 50
    return {"sens": sens, "confiance": conf, "raison": str(v.get("raison", ""))[:200]}


def pronostiquer(tickers: list[str], noms: dict | None = None, course: dict | None = None,
                 dossier=None, ai=None, prochaine=None) -> list[dict]:
    """Note jusqu'à MAX_PAR_JOUR pronostics. Sans dossier de recherche, pas de
    pronostic (on mesurerait la mémoire du modèle, pas l'information)."""
    rows = _load()
    deja = {(r["ticker"], r["publication"]) for r in rows}
    noms, course = noms or {}, course or {}
    dossier = dossier or _dossier
    if ai is None:
        from ai_provider import get_provider
        ai = get_provider("decision")
    nouveaux = []
    for t, d in a_pronostiquer(tickers, deja, prochaine=prochaine):
        if len(nouveaux) >= MAX_PAR_JOUR:
            break
        doc = dossier(t, noms.get(t, ""), d)
        if not doc:
            print(f"[pronostics] {t} : pas de recherche fiable — ignoré")
            continue
        v = parse_verdict(ai.complete(VERDICT_PROMPT.format(
            ticker=t, jour=d.strftime("%d/%m/%Y"), course=course.get(t, 0.0) or 0.0,
            dossier=doc[:12000]), max_tokens=300))
        if not v:
            print(f"[pronostics] {t} : verdict illisible — ignoré")
            continue
        r = {"ticker": t, "publication": d.isoformat(), "note_le": date.today().isoformat(),
             **v, "statut": "ouvert"}
        rows.append(r)
        nouveaux.append(r)
        _save(rows)
    return nouveaux


def resoudre(download=None, aujourdhui: date | None = None) -> list[dict]:
    """Mesure la réaction (clôture veille → clôture lendemain) des pronostics
    dont la séance du lendemain est close."""
    aujourdhui = aujourdhui or date.today()
    rows = _load()
    ouverts = [r for r in rows if r["statut"] == "ouvert"
               and date.fromisoformat(r["publication"]) < aujourdhui]
    if not ouverts:
        return []
    if download is None:
        import yfinance as yf
        download = yf.download
    debut = (min(date.fromisoformat(r["publication"]) for r in ouverts)
             - timedelta(days=10)).isoformat()
    tickers = sorted({r["ticker"] for r in ouverts})
    raw = download(tickers, start=debut, auto_adjust=True, progress=False)["Close"]
    from config import roundtrip_fee
    resolus = []
    for r in ouverts:
        try:
            p = (raw[r["ticker"]] if hasattr(raw, "columns") else raw).dropna()
        except Exception:
            continue
        jours = p.index.strftime("%Y-%m-%d")
        avant = p[jours < r["publication"]]
        apres = p[jours > r["publication"]]
        if avant.empty or apres.empty:
            # Date décalée par la société : abandon au bout de 3 semaines.
            if (aujourdhui - date.fromisoformat(r["publication"])).days > 21:
                r["statut"] = "abandonne"
            continue
        reaction = (float(apres.iloc[0]) / float(avant.iloc[-1]) - 1) * 100
        frais = roundtrip_fee(r["ticker"], TAILLE_REF) / TAILLE_REF * 100
        r.update(statut="resolu", reaction_pct=round(reaction, 2),
                 juste=(reaction > 0) == (r["sens"] == "hausse"),
                 # Achat la veille au soir si « hausse », revente le lendemain soir.
                 net_achat_pct=round(reaction - frais, 2) if r["sens"] == "hausse" else None)
        resolus.append(r)
    _save(rows)
    return resolus


def _wilson(k: int, n: int) -> tuple[float, float]:
    z = 1.96
    c = (k + z * z / 2) / (n + z * z)
    h = z * math.sqrt(k * (n - k) / n + z * z / 4) / (n + z * z)
    return round(c * 100 - h * 100, 1), round(c * 100 + h * 100, 1)


def bilan() -> dict:
    rows = _load()
    res = [r for r in rows if r["statut"] == "resolu"]
    n = len(res)
    k = sum(r["juste"] for r in res)
    forts = [r for r in res if r["confiance"] >= 65]
    achats = [r["net_achat_pct"] for r in res if r.get("net_achat_pct") is not None]
    ic = _wilson(k, n) if n else None
    return {
        "ouverts": sum(r["statut"] == "ouvert" for r in rows), "resolus": n,
        "justes_pct": round(k / n * 100, 1) if n else None, "ic95": ic,
        "forts": len(forts),
        "forts_justes_pct": round(sum(r["juste"] for r in forts) / len(forts) * 100)
        if forts else None,
        "achats": len(achats),
        "achats_moy": round(sum(achats) / len(achats), 2) if achats else None,
        "prouve": bool(n >= MIN_CAS and ic and ic[0] > 50),
    }


def texte_nouveaux(rows: list[dict]) -> str:
    l = ["🔮 PRONOSTICS DE RÉSULTATS — fantôme, rien n'est acheté"]
    for r in rows:
        f = "📈" if r["sens"] == "hausse" else "📉"
        l.append(f"{f} {r['ticker']} (résultats {date.fromisoformat(r['publication']):%d/%m}) : "
                 f"{r['sens']}, confiance {r['confiance']} %\n   {r['raison']}")
    return "\n".join(l)


def texte_resolus(rows: list[dict]) -> str:
    l = ["🔮 Pronostics de résultats vérifiés"]
    for r in rows:
        l.append(f"{'✅' if r['juste'] else '❌'} {r['ticker']} : {r['sens']} annoncée, "
                 f"réaction {r['reaction_pct']:+.1f} %")
    return "\n".join(l) + "\n/pronostics pour le bilan"


def texte_bilan() -> str:
    b = bilan()
    l = ["🔮 PRONOSTICS DE RÉSULTATS — essai fantôme",
         "L'IA lit les news avant chaque publication et annonce hausse ou baisse "
         "(clôture veille → clôture lendemain). Rien n'est acheté.", ""]
    if not b["resolus"]:
        l.append(f"Aucun pronostic vérifié pour l'instant · {b['ouverts']} en attente.")
    else:
        etat = ("✅ meilleure que le hasard" if b["prouve"] else
                f"{b['resolus']}/{MIN_CAS} cas" if b["resolus"] < MIN_CAS
                else "❌ pas mieux que pile ou face")
        l.append(f"{b['resolus']} vérifiés · {b['justes_pct']} % justes "
                 f"(IC95 {b['ic95'][0]}–{b['ic95'][1]} %) · {etat}")
        if b["forts"]:
            l.append(f"Confiance ≥ 65 % : {b['forts']} cas, {b['forts_justes_pct']} % justes")
        if b["achats"]:
            l.append(f"Si on avait acheté chaque « hausse » : {b['achats']} trades, "
                     f"{b['achats_moy']:+.2f} % net moyen (frais BD sur {TAILLE_REF:.0f} €)")
        l.append(f"{b['ouverts']} en attente de publication.")
    l += ["", "Repère : sur 3 018 publications passées, 49 % de hausses. L'IA n'apporte "
          f"quelque chose qu'au-dessus de 50 % avec ≥ {MIN_CAS} cas ET un IC95 entièrement "
          "au-dessus de 50 %."]
    return "\n".join(l)
