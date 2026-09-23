"""
Alerte NEWS et RÉSULTATS sur les positions détenues — alerte seule, jamais de vente.

POURQUOI CE MODULE ET PAS UNE RÈGLE DE SORTIE
---------------------------------------------
Le 23/09/2026, toutes les sorties anticipées basées sur le PRIX ont été
backtestées sur les positions détenues (`backtest.py --review` : MM50 cassée,
chandelier, séance de choc — 137 puis 679 titres). Aucune ne bat la référence ;
la vente sur MM50 coûte 500 à 800 €. Ce que le backtest ne voit pas, faute
d'historique, ce sont les NOUVELLES : une dégradation, un avertissement, un
procès. C'est ce trou-là que ce module couvre — en prévenant, pas en vendant.

QUI JUGE LES TITRES
-------------------
Jev (TypeSafe), un modèle de décision structurée : pour chaque titre d'article,
deux probabilités — « parle-t-il de CETTE société ? » et « est-ce une mauvaise
nouvelle pour son cours ? ». Testé le 23/09 : fiable sur du texte (le titre
« Why Adecoagro stock dropped… » ressort négatif à 92 %, un article sur ADM est
reconnu hors sujet) mais PAS sur des chiffres (il a jugé CBLL « thèse cassée »
au-dessus de ses moyennes mobiles). On ne lui envoie donc QUE du texte public :
nom de la société, titres et résumés d'articles. Aucun montant, aucune quantité.

COÛT
----
Seuls les articles jamais vus sont envoyés, un appel par position qui en a,
deux fois par semaine par défaut. Sans clé TYPESAFE_API_KEY, le module se tait.
"""
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from config import (EARNINGS_ALERT_DAYS, NEWS_ALERT_LOOKBACK_DAYS,
                    NEWS_ALERT_THRESHOLD)

STATE_PATH = Path(__file__).parent / "news_alert_state.json"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
MAX_HEADLINES = 8           # par position et par passage
SEEN_KEEP = 200             # ids mémorisés par position (anti-doublon)


# ── État (articles déjà vus, résultats déjà annoncés) ─────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"seen": {}, "earnings": {}}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=1, ensure_ascii=False))


# ── Sources ───────────────────────────────────────────────────────────────────

def _parse_news(items: list) -> list[dict]:
    """Articles yfinance, ancien format (champs à plat) ou nouveau (`content`)."""
    out = []
    for it in items or []:
        c = it.get("content") if isinstance(it.get("content"), dict) else it
        title = (c.get("title") or "").strip()
        if not title:
            continue
        pub = c.get("pubDate") or ""
        if not pub and c.get("providerPublishTime"):
            pub = datetime.fromtimestamp(c["providerPublishTime"], timezone.utc).isoformat()
        url = ((c.get("canonicalUrl") or {}).get("url")
               or (c.get("clickThroughUrl") or {}).get("url") or c.get("link") or "")
        provider = (c.get("provider") or {}).get("displayName") or c.get("publisher") or ""
        out.append({"id": it.get("id") or c.get("id") or url or title,
                    "title": title, "summary": (c.get("summary") or "")[:300],
                    "date": pub[:10], "url": url, "provider": provider})
    return out


def fetch_news(ticker: str) -> list[dict]:
    try:
        import yfinance as yf
        return _parse_news(yf.Ticker(ticker).news or [])
    except Exception as e:
        print(f"[news] {ticker} : {e}")
        return []


def fetch_next_earnings(ticker: str) -> date | None:
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        futures = sorted(d for d in (dates or []) if d >= date.today())
        return futures[0] if futures else None
    except Exception as e:
        print(f"[news] résultats {ticker} : {e}")
        return None


# ── Jev ───────────────────────────────────────────────────────────────────────

def _api_key() -> str:
    import os
    return os.environ.get("TYPESAFE_API_KEY", "")


def classify(company: str, articles: list[dict]) -> dict[str, dict]:
    """{id article: {"about": p, "negative": p}} — un seul appel pour tous les
    articles d'une société. {} si la clé manque ou si l'appel échoue : une
    panne de Jev ne doit jamais produire d'alerte (ni fausse, ni manquante
    en silence — elle est journalisée)."""
    key = _api_key()
    if not key or not articles:
        return {}
    state = {"company": company,
             "articles": {f"a{i}": {"title": a["title"], "summary": a["summary"]}
                          for i, a in enumerate(articles)}}
    questions = {}
    for i in range(len(articles)):
        questions[f"about{i}"] = {
            "type": "noul",
            "instructions": f"Is article `articles.a{i}` mainly about `company` itself, "
                            "rather than another company, a sector roundup or the whole market?"}
        questions[f"neg{i}"] = {
            "type": "noul",
            "instructions": f"Does article `articles.a{i}` report news likely to push the share "
                            "price of `company` down: weak results, guidance cut, analyst "
                            "downgrade, lawsuit, regulatory action, recall, executive departure, "
                            "dilution, or an explained share drop?"}
    body = {"model": "jev-latest", "state": state, "questions": questions}
    for essai in range(3):
        try:
            r = requests.post(JEV_URL, json=body, timeout=30,
                              headers={"Authorization": f"Bearer {key}"})
            if r.status_code in (429, 529):
                time.sleep(2 ** essai * 2)
                continue
            if r.status_code != 200:
                print(f"[news] Jev HTTP {r.status_code} : {r.text[:200]}")
                return {}
            ans = r.json().get("answers", {})
            return {a["id"]: {"about": ans.get(f"about{i}", {}).get("noul", 0.0),
                              "negative": ans.get(f"neg{i}", {}).get("noul", 0.0)}
                    for i, a in enumerate(articles)}
        except Exception as e:
            print(f"[news] Jev : {e}")
            return {}
    print("[news] Jev saturé, passage abandonné")
    return {}


# ── Messages ──────────────────────────────────────────────────────────────────

def _sym(ticker: str) -> str:
    return "€" if "." in ticker else "$"


def _position_line(cfg: dict, price: float | None) -> str:
    t, sym = cfg.get("ticker", ""), _sym(cfg.get("ticker", ""))
    parts = []
    if price:
        parts.append(f"Cours {price:.2f} {sym}")
        if cfg.get("target_low"):
            parts.append(f"SL {cfg['target_low']:.2f} {sym} "
                         f"({(cfg['target_low'] / price - 1) * 100:+.1f} %)")
        if cfg.get("entry_price"):
            parts.append(f"vs achat {(price / cfg['entry_price'] - 1) * 100:+.1f} %")
    return " · ".join(parts)


def _price(ticker: str) -> float | None:
    try:
        import prices
        return prices.get_quote(ticker).get("price")
    except Exception:
        return None


# ── Cycle ─────────────────────────────────────────────────────────────────────

def _positions() -> dict:
    """Positions suivies : gérées par le bot (pas HOLD), cotées (pas actées
    sans valeur)."""
    import portfolio
    return {k: v for k, v in portfolio.get_managed_positions().items()
            if not v.get("worthless") and v.get("ticker")}


def news_alert_cycle(send_fn, verbose: bool = False) -> int:
    """Un passage : news négatives puis résultats imminents. Retourne le
    nombre d'alertes envoyées. `verbose` (commande /news) rend compte de
    chaque position, même quand il n'y a rien."""
    state = _load_state()
    seen, earnings_done = state.setdefault("seen", {}), state.setdefault("earnings", {})
    positions = _positions()
    if not positions:
        if verbose:
            send_fn("Aucune position suivie.")
        return 0
    if not _api_key():
        msg = "[news] TYPESAFE_API_KEY absente : tri des news désactivé"
        print(msg)
        if verbose:
            send_fn("Tri des news désactivé : TYPESAFE_API_KEY absente du .env.")

    horizon = (date.today() - timedelta(days=NEWS_ALERT_LOOKBACK_DAYS)).isoformat()
    sent, rapport = 0, []
    for name, cfg in positions.items():
        ticker = cfg["ticker"]
        company = f"{cfg.get('bd_name') or name} ({ticker})"
        deja = set(seen.get(name, []))
        neufs = [a for a in fetch_news(ticker)
                 if a["id"] not in deja and (not a["date"] or a["date"] >= horizon)]
        neufs = neufs[:MAX_HEADLINES]
        verdicts = classify(company, neufs) if _api_key() else {}
        mauvaises = [a for a in neufs
                     if verdicts.get(a["id"], {}).get("about", 0) >= NEWS_ALERT_THRESHOLD
                     and verdicts.get(a["id"], {}).get("negative", 0) >= NEWS_ALERT_THRESHOLD]
        if mauvaises:
            lignes = [f"📰 {name} — news négative détectée"]
            for a in mauvaises:
                src = ", ".join(x for x in (a["provider"], a["date"]) if x)
                lignes.append(f"• « {a['title']} »" + (f" ({src})" if src else "")
                              + f" — négative à {verdicts[a['id']]['negative'] * 100:.0f} %")
                if a["url"]:
                    lignes.append(f"  {a['url']}")
            pos = _position_line(cfg, _price(ticker))
            if pos:
                lignes.append(pos)
            lignes.append(f"Aucune vente automatique. /research {ticker} pour réanalyser.")
            send_fn("\n".join(lignes))
            sent += 1
        # Un article n'est marqué vu QUE s'il a été jugé : si Jev était en
        # panne, il sera relu au passage suivant au lieu d'être perdu.
        if verdicts or not neufs:
            seen[name] = (list(deja) + [a["id"] for a in neufs])[-SEEN_KEEP:]
        if verbose:
            etat = (f"{len(mauvaises)} négative(s)" if mauvaises
                    else f"{len(neufs)} nouvel(s) article(s), rien de négatif" if neufs
                    else "aucun nouvel article")
            rapport.append(f"• {name} : {etat}")

        # Résultats imminents : un SL ne protège PAS d'un gap à l'ouverture.
        ed = fetch_next_earnings(ticker)
        if ed and (ed - date.today()).days <= EARNINGS_ALERT_DAYS:
            if earnings_done.get(name) != ed.isoformat():
                pos = _position_line(cfg, _price(ticker))
                send_fn(f"📅 {name} publie ses résultats le {ed.strftime('%d/%m')} "
                        f"(dans {(ed - date.today()).days} j).\n"
                        + (pos + "\n" if pos else "")
                        + "Un stop-loss ne protège pas d'un écart à l'ouverture après "
                          "résultats. À décider avant : garder, alléger ou vendre.")
                earnings_done[name] = ed.isoformat()
                sent += 1
            if verbose:
                rapport.append(f"  résultats le {ed.strftime('%d/%m')}")

    # Positions vendues : on oublie leurs articles.
    for k in list(seen):
        if k not in positions:
            seen.pop(k)
    for k in list(earnings_done):
        if k not in positions:
            earnings_done.pop(k)
    _save_state(state)
    if verbose:
        send_fn("Tri des news terminé.\n" + "\n".join(rapport))
    return sent
