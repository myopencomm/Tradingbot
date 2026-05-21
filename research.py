"""
Recherche web gratuite via DuckDuckGo (pas de clé API requise).
Fournit le contexte marché + analyse d'actions pour l'IA.
"""
try:
    from duckduckgo_search import DDGS
    _DDG_AVAILABLE = True
except ImportError:
    _DDG_AVAILABLE = False
    print("⚠️ duckduckgo-search non installé — pip install duckduckgo-search")


def _search(query: str, max_results: int = 4) -> list[dict]:
    if not _DDG_AVAILABLE:
        return []
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as e:
        print(f"⚠️ DDG search error: {e}")
        return []


def _snippets(results: list[dict], max_chars: int = 180) -> list[str]:
    return [
        f"• {r.get('title', '')}: {r.get('body', '')[:max_chars]}"
        for r in results
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
