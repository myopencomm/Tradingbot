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
    """Contexte macro + marché du jour pour le briefing IA."""
    queries = [
        "CAC 40 bourse Paris actualité aujourd'hui",
        "marchés européens taux macro économie semaine",
        "small cap France Euronext opportunités hausse",
    ]
    snippets = []
    for q in queries:
        snippets += _snippets(_search(q, max_results=2))
    return "\n".join(snippets[:9]) or "Données web indisponibles."


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


def scan_sector(sector: str) -> str:
    """Recherche les meilleures opportunités dans un secteur."""
    queries = [
        f"meilleures actions {sector} Euronext 2026 hausse",
        f"{sector} small cap France achat recommandation analyste",
    ]
    snippets = []
    for q in queries:
        snippets += _snippets(_search(q, max_results=3))
    return "\n".join(snippets[:8]) or "Aucune donnée web trouvée."
