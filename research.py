"""
Recherche web via DuckDuckGo HTML (requests pur — pas de clé API, pas de dépendance httpx).
"""
import re
import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
DDG_URL = "https://html.duckduckgo.com/html/"


def _search(query: str, max_results: int = 4) -> list[dict]:
    """Recherche DuckDuckGo via l'endpoint HTML — fonctionne sans lib externe."""
    try:
        r = requests.post(
            DDG_URL,
            data={"q": query, "s": "0", "kl": "fr-fr"},
            headers=HEADERS,
            timeout=8,
        )
        if r.status_code != 200:
            return []

        # Extraction titre + snippet depuis le HTML brut
        titles   = re.findall(r'class="result__a"[^>]*>(.*?)</a>', r.text, re.DOTALL)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', r.text, re.DOTALL)

        results = []
        for title, snippet in zip(titles, snippets):
            t = re.sub(r'<[^>]+>', '', title).strip()
            s = re.sub(r'<[^>]+>', '', snippet).strip()
            s = re.sub(r'&#x27;', "'", s)
            if t or s:
                results.append({"title": t, "body": s})
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        print(f"⚠️ DDG search error: {e}")
        return []


def _snippets(results: list[dict], max_chars: int = 200) -> list[str]:
    return [
        f"• {r.get('title', '')}: {r.get('body', '')[:max_chars]}"
        for r in results if r.get("title") or r.get("body")
    ]


def market_context() -> str:
    """Contexte macro + marchés mondiaux pour le briefing IA."""
    queries = [
        "CAC 40 S&P 500 marchés actions actualité aujourd'hui 2026",
        "Federal Reserve BCE taux directeurs macro économie semaine 2026",
        "Wall Street NYSE NASDAQ tendance hausse baisse 2026",
        "marchés européens Euronext opportunités actions 2026",
    ]
    snippets = []
    for q in queries:
        snippets += _snippets(_search(q, max_results=2))
    return "\n".join(snippets[:12]) or "Données web indisponibles."


def research_stock(ticker: str, company_name: str = "") -> str:
    """Recherche approfondie sur une action spécifique."""
    name = company_name or ticker
    queries = [
        f"{name} bourse actualités résultats 2026",
        f"{ticker} cours objectif analyste recommandation achat",
        f"{name} catalyseurs perspectives prochains mois",
    ]
    snippets = []
    for q in queries:
        snippets += _snippets(_search(q, max_results=3))
    return "\n".join(snippets[:10]) or "Aucune donnée web trouvée."


def search_catalysts(ticker: str, company_name: str = "") -> str:
    """Catalyseurs imminents pour un titre : résultats, contrats, OPA, rachats."""
    name = company_name or ticker
    queries = [
        f"{name} résultats financiers publication date juin juillet 2026",
        f"{name} contrat partenariat accord annonce 2026",
        f"{name} OPA rachat actions dividende exceptionnel fusion 2026",
        f"{ticker} hausse fort catalyseur analyste objectif cours 2026",
    ]
    snippets = []
    for q in queries:
        snippets += _snippets(_search(q, max_results=2))
    return "\n".join(snippets[:8]) or "Aucun catalyseur imminent identifié."


def market_catalysts() -> str:
    """Scrute les marchés mondiaux pour des actions avec catalyseurs imminents à fort potentiel."""
    queries = [
        "Euronext Paris small cap résultats publication prochaines semaines 2026 hausse",
        "action Europe catalyseur fort OPA contrat partenariat annonce hausse 2026",
        "NYSE NASDAQ stock earnings catalyst buy recommendation analyst target 2026",
        "biotech pharma FDA approval catalyst stock surge 2026",
        "tech stock momentum breakout analyst upgrade 2026",
    ]
    snippets = []
    for q in queries:
        snippets += _snippets(_search(q, max_results=3))
    return "\n".join(snippets[:15]) or "Aucune donnée catalyseurs disponible."


def scan_sector(sector: str) -> str:
    """Recherche les meilleures opportunités dans un secteur, tous marchés."""
    queries = [
        f"best {sector} stocks buy recommendation analyst target 2026",
        f"{sector} actions Euronext NYSE catalyseur hausse 2026",
    ]
    snippets = []
    for q in queries:
        snippets += _snippets(_search(q, max_results=3))
    return "\n".join(snippets[:8]) or "Aucune donnée web trouvée."
