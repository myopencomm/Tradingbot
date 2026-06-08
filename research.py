"""
Recherche web via DuckDuckGo HTML (requests pur — pas de clé API).
Adapte la langue des requêtes au marché du ticker.
"""
import re
import requests
from datetime import datetime

def _current_period() -> str:
    """Retourne 'juin 2026' ou 'june 2026' selon la langue."""
    now = datetime.now()
    months_fr = ["janvier","février","mars","avril","mai","juin",
                 "juillet","août","septembre","octobre","novembre","décembre"]
    return f"{months_fr[now.month-1]} {now.year}"

def _current_period_en() -> str:
    return datetime.now().strftime("%B %Y")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
DDG_URL = "https://html.duckduckgo.com/html/"


def _detect_market(ticker: str) -> str:
    """Détecte le marché depuis le suffixe du ticker."""
    t = ticker.upper()
    if t.endswith(".PA") or t.endswith(".BR") or t.endswith(".AS") or t.endswith(".LI"):
        return "fr"
    if t.endswith(".L"):
        return "uk"
    if t.endswith(".DE") or t.endswith(".F") or t.endswith(".HM"):
        return "de"
    return "us"


def _search(query: str, max_results: int = 5, lang: str = "fr-fr") -> list[dict]:
    """Recherche DuckDuckGo via l'endpoint HTML."""
    try:
        r = requests.post(
            DDG_URL,
            data={"q": query, "s": "0", "kl": lang},
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return []

        titles   = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text, re.DOTALL)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.DOTALL)

        results = []
        for title, snippet in zip(titles, snippets):
            t = re.sub(r'<[^>]+>', '', title).strip()
            s = re.sub(r'<[^>]+>', '', snippet).strip()
            s = re.sub(r'&#x27;', "'", s)
            s = re.sub(r'&amp;', "&", s)
            s = re.sub(r'&quot;', '"', s)
            if t or s:
                results.append({"title": t, "body": s})
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        print(f"⚠️ DDG search error: {e}")
        return []


def _snippets(results: list[dict], max_chars: int = 500) -> list[str]:
    return [
        f"• {r.get('title', '')}: {r.get('body', '')[:max_chars]}"
        for r in results if r.get("title") or r.get("body")
    ]


def market_context() -> str:
    """Contexte macro + marchés mondiaux pour le briefing IA."""
    p = _current_period()
    p_en = _current_period_en()
    queries_fr = [
        (f"CAC 40 S&P 500 marchés actions actualité {p}", "fr-fr"),
        (f"Federal Reserve BCE taux directeurs décision {p}", "fr-fr"),
        (f"Wall Street NYSE NASDAQ tendance {p_en}", "us-en"),
        (f"marchés européens Euronext secteurs opportunités {p}", "fr-fr"),
        (f"inflation croissance PIB risques macro {p}", "fr-fr"),
    ]
    snippets = []
    for q, lang in queries_fr:
        snippets += _snippets(_search(q, max_results=3, lang=lang))
    return "\n".join(snippets[:15]) or "Données web indisponibles."


def research_stock(ticker: str, company_name: str = "") -> str:
    """Recherche approfondie sur une action spécifique."""
    name   = company_name or ticker.split(".")[0]
    market = _detect_market(ticker)

    p = _current_period()
    p_en = _current_period_en()
    if market == "us":
        queries = [
            (f"{name} stock news earnings analyst {p_en}", "us-en"),
            (f"{ticker} price target analyst rating buy sell {p_en}", "us-en"),
            (f"{name} revenue growth catalyst outlook {p_en}", "us-en"),
            (f"{name} stock latest news {p_en}", "us-en"),
        ]
    elif market == "uk":
        queries = [
            (f"{name} share news results analyst {p_en}", "uk-en"),
            (f"{ticker} price target recommendation {p_en}", "uk-en"),
            (f"{name} outlook catalyst growth {p_en}", "uk-en"),
        ]
    elif market == "de":
        queries = [
            (f"{name} Aktie Nachrichten Analyse {p}", "de-de"),
            (f"{ticker} Kursziel Empfehlung Analysten {p}", "de-de"),
            (f"{name} Ergebnisse Katalysatoren {p}", "de-de"),
        ]
    else:  # fr + autres Euronext
        queries = [
            (f"{name} bourse actualités résultats {p}", "fr-fr"),
            (f"{ticker} cours objectif analyste recommandation {p}", "fr-fr"),
            (f"{name} catalyseurs perspectives prochains mois {p}", "fr-fr"),
            (f"{name} news analyst target {p_en}", "us-en"),
        ]

    snippets = []
    for q, lang in queries:
        snippets += _snippets(_search(q, max_results=3, lang=lang))
    return "\n".join(snippets[:14]) or "Aucune donnée web trouvée."


def search_catalysts(ticker: str, company_name: str = "") -> str:
    """Catalyseurs imminents : résultats, contrats, OPA, FDA, rachats."""
    name   = company_name or ticker.split(".")[0]
    market = _detect_market(ticker)

    p = _current_period()
    p_en = _current_period_en()
    if market == "us":
        queries = [
            (f"{name} earnings date Q2 Q3 {p_en} calendar upcoming", "us-en"),
            (f"{name} FDA approval catalyst pipeline {p_en}", "us-en"),
            (f"{name} merger acquisition contract partnership {p_en}", "us-en"),
            (f"{ticker} analyst upgrade price target raise {p_en}", "us-en"),
        ]
    else:
        queries = [
            (f"{name} résultats publication date prochains mois {p}", "fr-fr"),
            (f"{name} contrat partenariat accord OPA annonce {p}", "fr-fr"),
            (f"{name} dividende rachat actions assemblée générale {p}", "fr-fr"),
            (f"{name} catalyseur hausse analyste objectif {p}", "fr-fr"),
            (f"{name} earnings catalyst upcoming {p_en}", "us-en"),
        ]

    snippets = []
    for q, lang in queries:
        snippets += _snippets(_search(q, max_results=3, lang=lang))
    return "\n".join(snippets[:12]) or "Aucun catalyseur imminent identifié."


def market_catalysts() -> str:
    """Actions avec catalyseurs imminents à fort potentiel sur tous marchés."""
    p = _current_period()
    p_en = _current_period_en()
    queries = [
        (f"Euronext Paris small cap résultats publication prochaines semaines {p} hausse", "fr-fr"),
        (f"action Europe catalyseur fort OPA contrat annonce hausse {p}", "fr-fr"),
        (f"NYSE NASDAQ stock earnings catalyst buy recommendation analyst {p_en}", "us-en"),
        (f"biotech pharma FDA approval catalyst stock surge {p_en}", "us-en"),
        (f"tech stock momentum breakout analyst upgrade {p_en}", "us-en"),
        (f"LSE UK stock catalyst earnings upgrade {p_en}", "uk-en"),
        (f"Xetra DAX small cap Katalysator Kursanstieg {p}", "de-de"),
    ]
    snippets = []
    for q, lang in queries:
        snippets += _snippets(_search(q, max_results=3, lang=lang))
    return "\n".join(snippets[:20]) or "Aucune donnée catalyseurs disponible."


def get_social_sentiment(ticker: str) -> str:
    """
    Sentiment social via StockTwits (gratuit, sans clé) + Reddit JSON API.
    Retourne un résumé texte injecté dans les prompts /research et /scan.

    StockTwits : fonctionne bien pour les US stocks et quelques stocks FR majeurs.
    Reddit : r/stocks, r/investing, r/wallstreetbets — surtout pertinent US.
    """
    # Ticker base sans suffixe (EXENS.PA → EXENS, ILMN → ILMN)
    base = ticker.upper().split(".")[0]
    lines = []

    # ── StockTwits ────────────────────────────────────────────────────────────
    try:
        st_url = f"https://api.stocktwits.com/api/2/streams/symbol/{base}.json"
        r = requests.get(st_url, headers=HEADERS, timeout=6)
        if r.status_code == 200:
            data = r.json()
            messages = data.get("messages", [])

            def _st_sentiment(m):
                ent = m.get("entities") or {}
                return (ent.get("sentiment") or {}).get("basic")

            bullish = sum(1 for m in messages if _st_sentiment(m) == "Bullish")
            bearish = sum(1 for m in messages if _st_sentiment(m) == "Bearish")
            total   = bullish + bearish
            if total > 0:
                bull_pct = round(bullish / total * 100)
                lines.append(f"StockTwits ({len(messages)} msgs) : {bull_pct}% bullish / {100-bull_pct}% bearish")
                for m in messages[:5]:
                    sent = _st_sentiment(m) or ""
                    body = m.get("body", "").replace("\n", " ")[:120]
                    if body:
                        tag = f"[{sent}] " if sent else ""
                        lines.append(f"  • {tag}{body}")
            elif messages:
                lines.append(f"StockTwits : {len(messages)} messages récents, sentiment non tagué")
    except Exception as e:
        print(f"⚠️ StockTwits {ticker}: {e}")

    # ── Reddit (JSON API publique, sans clé) ──────────────────────────────────
    try:
        reddit_headers = {**HEADERS, "User-Agent": "TradingBot/1.0 (research tool)"}
        subreddits = ["stocks", "investing", "wallstreetbets"]
        reddit_lines = []
        for sub in subreddits:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/search.json",
                params={"q": base, "sort": "relevance", "t": "week", "limit": 3},
                headers=reddit_headers, timeout=6,
            )
            if r.status_code != 200:
                continue
            posts = r.json().get("data", {}).get("children", [])
            for post in posts:
                d = post.get("data", {})
                title = d.get("title", "")[:100]
                score = d.get("score", 0)
                if title and score > 5:
                    reddit_lines.append(f"  • r/{sub} (+{score}) {title}")
        if reddit_lines:
            lines.append(f"Reddit (7 derniers jours) :")
            lines.extend(reddit_lines[:5])
    except Exception as e:
        print(f"⚠️ Reddit {ticker}: {e}")

    return "\n".join(lines) if lines else "Sentiment social : aucune donnée disponible."


def scan_sector(sector: str) -> str:
    """Meilleures opportunités dans un secteur, tous marchés."""
    queries = [
        (f"best {sector} stocks buy recommendation analyst target 2026", "us-en"),
        (f"{sector} actions Euronext NYSE catalyseur hausse 2026", "fr-fr"),
    ]
    snippets = []
    for q, lang in queries:
        snippets += _snippets(_search(q, max_results=3, lang=lang))
    return "\n".join(snippets[:10]) or "Aucune donnée web trouvée."
