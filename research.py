"""
Recherche web via DuckDuckGo HTML (requests pur — pas de clé API).
Adapte la langue des requêtes au marché du ticker.
"""
import json
import re
import requests
from datetime import datetime
from pathlib import Path

# Cache volume de messages par ticker pour détecter les pics d'activité
_SENTIMENT_CACHE = Path(__file__).parent / "sentiment_cache.json"

_vader = None

def _get_vader():
    global _vader
    if _vader is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _vader = SentimentIntensityAnalyzer()
    return _vader

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


_last_search = 0.0


def _search(query: str, max_results: int = 5, lang: str = "fr-fr") -> list[dict]:
    """Recherche DuckDuckGo via l'endpoint HTML.

    DDG bride les rafales : HTTP 202 + page « anomaly » au lieu des résultats.
    Un scan enchaîne ~50 requêtes ; sans pause ni nouvel essai, une partie des
    candidats recevait une recherche VIDE et l'IA les jugeait à l'aveugle
    (01/10/2026). Espacement minimal entre requêtes + 2 nouveaux essais.
    """
    import time
    global _last_search
    try:
        r = None
        for attempt in range(3):
            wait = 1.0 - (time.time() - _last_search)
            if wait > 0:
                time.sleep(wait)
            _last_search = time.time()
            r = requests.post(
                DDG_URL,
                data={"q": query, "s": "0", "kl": lang},
                headers=HEADERS,
                timeout=10,
            )
            if r.status_code == 200 and "result__a" in r.text:
                break
            if r.status_code == 200:          # vraie réponse sans résultat
                return []
            time.sleep(3 * (attempt + 1))     # bridé : on attend et on réessaie
        if r is None or r.status_code != 200:
            print(f"⚠️ DDG bridé ({r.status_code if r is not None else '?'}) : {query[:60]}")
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


def _fear_greed() -> dict:
    """CNN Fear & Greed index (0-100). Retourne {"score": int, "rating": str} ou {}."""
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=HEADERS, timeout=8,
        )
        if r.status_code == 200:
            fg = r.json().get("fear_and_greed", {})
            score = fg.get("score")
            if score is not None:
                return {"score": round(score), "rating": fg.get("rating", "")}
    except Exception as e:
        print(f"⚠️ Fear&Greed error: {e}")
    return {}


def market_mood() -> str:
    """Ligne sentiment marché temps réel : VIX + Fear & Greed."""
    import prices
    parts = []
    vix = prices.get_vix()
    if vix:
        parts.append(f"VIX {vix['level']} ({vix['change_pct']:+}% 1j) — {vix['label']}")
    fg = _fear_greed()
    if fg:
        parts.append(f"Fear & Greed {fg['score']}/100 ({fg['rating']})")
    return " | ".join(parts)


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
    mood = market_mood()
    mood_line = f"SENTIMENT MARCHÉ TEMPS RÉEL : {mood}\n" if mood else ""
    return mood_line + ("\n".join(snippets[:15]) or "Données web indisponibles.")


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


def _load_sentiment_cache() -> dict:
    try:
        if _SENTIMENT_CACHE.exists():
            return json.loads(_SENTIMENT_CACHE.read_text())
    except Exception:
        pass
    return {}


def _save_sentiment_cache(cache: dict) -> None:
    try:
        _SENTIMENT_CACHE.write_text(json.dumps(cache))
    except Exception:
        pass


def _boursorama_titles(base: str, max_items: int = 5) -> list[str]:
    """Titres récents du forum Boursorama pour une valeur Euronext Paris."""
    try:
        r = requests.get(
            f"https://www.boursorama.com/bourse/forum/1rP{base}/",
            headers=HEADERS, timeout=8,
        )
        if r.status_code != 200:
            return []
        titles = re.findall(
            rf'href="/bourse/forum/1rP{base}/detail/[^"]+"[^>]*>\s*([^<]{{5,120}})',
            r.text,
        )
        clean = []
        for t in titles:
            t = " ".join(t.split())
            if t and t not in clean:
                clean.append(t)
            if len(clean) >= max_items:
                break
        return clean
    except Exception as e:
        print(f"⚠️ Boursorama {base}: {e}")
        return []


def _boursorama_messages(base: str, max_threads: int = 3, max_msgs: int = 8) -> list[str]:
    """Corps des messages récents du forum Boursorama (pour scoring IA)."""
    try:
        r = requests.get(
            f"https://www.boursorama.com/bourse/forum/1rP{base}/",
            headers=HEADERS, timeout=8,
        )
        if r.status_code != 200:
            return []
        threads = re.findall(rf'href="(/bourse/forum/1rP{base}/detail/[^"]+)"', r.text)
        seen_threads = list(dict.fromkeys(threads))[:max_threads]
        messages = []
        for path in seen_threads:
            try:
                rd = requests.get(f"https://www.boursorama.com{path}",
                                  headers=HEADERS, timeout=8)
                if rd.status_code != 200:
                    continue
                bodies = re.findall(r'class="c-message__text[^"]*"[^>]*>(.*?)</',
                                    rd.text, re.DOTALL)
                for b in bodies:
                    txt = " ".join(re.sub(r"<[^>]+>", " ", b).split())
                    if len(txt) > 20 and txt not in messages:
                        messages.append(txt[:300])
                    if len(messages) >= max_msgs:
                        return messages
            except Exception:
                continue
        return messages
    except Exception as e:
        print(f"⚠️ Boursorama messages {base}: {e}")
        return []


def _score_french_texts(texts: list[str]) -> int | None:
    """Score sentiment -100/+100 de textes français via le modèle IA économique."""
    if not texts:
        return None
    # Jev d'abord : jugement typé sur du texte, son point fort — et sans coût
    # de modèle de langage. Échelle 0-4 ramenée à -100/+100.
    try:
        import jev
        ans = jev.ask({"messages": texts[:10]}, {"s": {
            "type": "score",
            "instructions": "Overall investor sentiment expressed in `messages` "
                            "(French stock-forum posts) about the stock they discuss.",
            "criteria": ["very bearish", "bearish", "neutral", "bullish", "very bullish"]}},
            label="sentiment")
        v = jev.score(ans, "s")
        if v is not None:
            return int(round((v - 2) * 50))
    except Exception as e:
        print(f"⚠️ Score sentiment Jev: {e}")
    try:
        from ai_provider import get_provider
        joined = "\n".join(f"- {t}" for t in texts[:10])
        raw = get_provider().complete_cheap(
            "Voici des messages de forum boursier sur une action. "
            "Note le sentiment global des investisseurs de -100 (très bearish) "
            "à +100 (très bullish). Réponds UNIQUEMENT avec le nombre entier, rien d'autre.\n\n"
            f"{joined}",
            max_tokens=8,
        )
        m = re.search(r"-?\d+", raw)
        if m:
            return max(-100, min(100, int(m.group())))
    except Exception as e:
        print(f"⚠️ Score sentiment FR: {e}")
    return None


def get_social_sentiment(ticker: str) -> str:
    """
    Sentiment social composite : StockTwits + Reddit + Boursorama (valeurs .PA).

    - Score -100 (très bearish) à +100 (très bullish) combinant les tags
      StockTwits, l'analyse VADER des textes anglais (messages + titres Reddit)
      et un scoring IA des messages français Boursorama (valeurs .PA).
    - Vélocité : compare le volume de messages au dernier passage pour
      détecter les pics d'activité (souvent plus prédictifs que le sentiment).
    """
    base = ticker.upper().split(".")[0]
    is_fr = ticker.upper().endswith((".PA", ".BR", ".AS"))
    lines = []
    en_texts: list[str] = []   # textes anglais à scorer avec VADER
    tag_score = None           # score issu des tags Bullish/Bearish StockTwits
    volume = 0

    # ── StockTwits ────────────────────────────────────────────────────────────
    try:
        st_url = f"https://api.stocktwits.com/api/2/streams/symbol/{base}.json"
        r = requests.get(st_url, headers=HEADERS, timeout=6)
        if r.status_code == 200:
            messages = r.json().get("messages", [])
            volume += len(messages)

            def _st_sentiment(m):
                ent = m.get("entities") or {}
                return (ent.get("sentiment") or {}).get("basic")

            bullish = sum(1 for m in messages if _st_sentiment(m) == "Bullish")
            bearish = sum(1 for m in messages if _st_sentiment(m) == "Bearish")
            total   = bullish + bearish
            if total > 0:
                bull_pct  = round(bullish / total * 100)
                tag_score = (bull_pct - 50) * 2  # 0% bull → -100, 100% bull → +100
                lines.append(f"StockTwits ({len(messages)} msgs) : {bull_pct}% bullish / {100-bull_pct}% bearish")
            for m in messages[:15]:
                body = m.get("body", "").replace("\n", " ")
                if body:
                    en_texts.append(body)
            for m in messages[:3]:
                sent = _st_sentiment(m) or ""
                body = m.get("body", "").replace("\n", " ")[:120]
                if body:
                    tag = f"[{sent}] " if sent else ""
                    lines.append(f"  • {tag}{body}")
    except Exception as e:
        print(f"⚠️ StockTwits {ticker}: {e}")

    # ── Reddit (JSON API publique, sans clé) ──────────────────────────────────
    try:
        reddit_headers = {**HEADERS, "User-Agent": "TradingBot/1.0 (research tool)"}
        subreddits = ["stocks", "investing", "wallstreetbets"]
        if is_fr:
            subreddits += ["vosfinances", "EuropeFIRE"]
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
                    volume += 1
                    reddit_lines.append(f"  • r/{sub} (+{score}) {title}")
                    if sub not in ("vosfinances",):  # VADER = anglais uniquement
                        en_texts.append(title)
        if reddit_lines:
            lines.append("Reddit (7 derniers jours) :")
            lines.extend(reddit_lines[:5])
    except Exception as e:
        print(f"⚠️ Reddit {ticker}: {e}")

    # ── Boursorama (valeurs Euronext Paris) : titres + scoring IA des corps ──
    fr_score = None
    if ticker.upper().endswith(".PA"):
        bourso = _boursorama_titles(base)
        if bourso:
            volume += len(bourso)
            lines.append("Forum Boursorama (titres récents) :")
            lines.extend(f"  • {t}" for t in bourso)
            fr_score = _score_french_texts(_boursorama_messages(base))

    # ── Score composite : tags StockTwits + VADER (anglais) + IA (français) ──
    vader_score = None
    if en_texts:
        try:
            analyzer  = _get_vader()
            compounds = [analyzer.polarity_scores(t)["compound"] for t in en_texts]
            vader_score = round(sum(compounds) / len(compounds) * 100)
        except Exception as e:
            print(f"⚠️ VADER {ticker}: {e}")

    # Pondération : tags 3 / VADER 2 / IA français 2 (composants absents ignorés)
    parts = [(s, w) for s, w in ((tag_score, 3), (vader_score, 2), (fr_score, 2))
             if s is not None]
    composite = round(sum(s * w for s, w in parts) / sum(w for _, w in parts)) if parts else None

    # ── Vélocité : volume vs dernier passage ──────────────────────────────────
    velocity = ""
    cache = _load_sentiment_cache()
    prev = cache.get(base, {})
    prev_volume = prev.get("volume", 0)
    if prev_volume and volume:
        ratio = volume / prev_volume
        if ratio >= 2:
            velocity = f" | ⚠️ volume x{ratio:.1f} vs dernier check — pic d'activité"
        elif ratio <= 0.5:
            velocity = f" | volume en baisse ({ratio:.1f}x)"
    cache[base] = {"volume": volume, "score": composite,
                   "date": datetime.now().strftime("%Y-%m-%d %H:%M")}
    _save_sentiment_cache(cache)

    # ── Assemblage ────────────────────────────────────────────────────────────
    if composite is not None:
        if composite >= 30:
            label = "bullish"
        elif composite <= -30:
            label = "bearish"
        else:
            label = "neutre"
        header = f"SCORE SENTIMENT : {composite:+d}/100 ({label}){velocity}"
        lines.insert(0, header)
    elif velocity:
        lines.insert(0, f"SENTIMENT : volume seul disponible{velocity}")

    return "\n".join(lines) if lines else "Sentiment social : aucune donnée disponible."




# ── Recherche approfondie (Claude + recherche web), 14/10/2026 ───────────────
# La recherche DuckDuckGo ci-dessus ne ramène presque que des titres de pages
# (« Earnings Date and Reports… », « Stock Forecast… ») et elle est bridée à
# chaque scan (73 requêtes bridées pour 43 validations le 14/10). L'IA décidait
# donc presque à l'aveugle — DGII a été acheté sur une preuve StockTwits.
# Ici, Claude cherche lui-même (outil WebSearch seul, sur l'abonnement) et rend
# un dossier daté et sourcé. Mis en cache pour la journée : le scan et le
# contrôle pré-achat du même titre ne paient qu'une recherche.
_DEEP_CACHE = Path(__file__).parent / "research_cache.json"

DEEP_PROMPT = """Fais une recherche web sur {nom} ({ticker}) et rends un dossier factuel.
Date du jour : {date}. Chaque fait : date + source. Si une rubrique n'a rien de
vérifiable, écris « rien de vérifié ». N'invente rien, ne recommande rien.

ACTUALITÉS (30 derniers jours, les plus importantes, max 5)
- JJ/MM/AAAA — fait précis, chiffré — source

ÉVÉNEMENTS À VENIR (dates exactes : résultats, journée investisseurs,
décision réglementaire, fin d'offre, assemblée)
- JJ/MM/AAAA — événement — source

ANALYSTES (objectif moyen, nombre d'analystes, révisions récentes datées)

QUALITÉ DE LA CROISSANCE (ce qui est RÉCURRENT et ce qui est EXCEPTIONNEL dans
les derniers résultats : gains ponctuels, remboursements, cessions, effets de
change — avec montants)

RISQUES SPÉCIFIQUES (dette, dilution, litiges, dépendance client, réglementaire)

POURQUOI LE COURS A BOUGÉ (explication sourcée des mouvements des 3 derniers mois)
"""


# ── Secours : Gemini + Google Search (24/10/2026) ────────────────────────────
# Du 12 au 23/10, les 40 validations ont TOUTES été refusées pour « recherche
# web fiable indisponible » : la session OAuth de la CLI claude avait expiré et
# ne se rafraîchissait plus sous launchd. Aucun achat possible pendant 11 jours,
# sans que rien ne le signale. Désormais : la CLI d'abord (abonnement), puis
# la clé API Anthropic au seul contrôle pré-achat, puis Gemini avec
# l'ancrage Google Search, et une alerte Telegram si la CLI est
# déconnectée. Une réponse Gemini SANS recherche réellement effectuée (aucune
# requête, aucune source dans groundingMetadata) est rejetée : ce serait de la
# mémoire du modèle, pas un dossier du jour.
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
_DEEP_SYSTEM = ("Analyste financier. Tu cherches sur le web et ne rapportes que des "
                "faits datés et sourcés. Aucun fait de mémoire. Aucune recommandation.")
_last_cli_alert = 0.0


def _alerte_cli(err: Exception) -> None:
    """Une alerte Telegram toutes les 6 h au plus quand la CLI est déconnectée."""
    global _last_cli_alert
    import time
    m = str(err).lower()
    if not any(s in m for s in ("authenticate", "oauth", "not logged in", "login")):
        return
    if time.time() - _last_cli_alert < 6 * 3600:
        return
    _last_cli_alert = time.time()
    try:
        import tg
        tg.send("🔑 CLI claude déconnectée (session expirée)\n"
                "La recherche web avant achat bascule sur Gemini + Google Search.\n"
                "Réparer : dans un terminal, claude setup-token, puis mettre le "
                "jeton dans .env (CLAUDE_CODE_OAUTH_TOKEN=...) et ./bot.sh restart.")
    except Exception as e:
        print(f"[deep research] alerte Telegram impossible : {e}")


def _deep_gemini(prompt: str, ticker: str) -> str:
    """Dossier via Gemini Pro + Google Search, ou "" si pas de recherche réelle."""
    import os
    from config import GEMINI_API_KEY
    key = os.environ.get("GEMINI_API_KEY") or GEMINI_API_KEY
    if not key or os.getenv("DEEP_RESEARCH_GEMINI", "1") == "0":
        return ""
    model = os.getenv("DEEP_RESEARCH_GEMINI_MODEL", "gemini-pro-latest")
    for essai in (1, 2):
        try:
            r = requests.post(_GEMINI_URL.format(m=model),
                              headers={"x-goog-api-key": key}, timeout=240,
                              json={"contents": [{"parts": [{"text": prompt}]}],
                                    "tools": [{"google_search": {}}],
                                    "systemInstruction": {"parts": [{"text": _DEEP_SYSTEM}]}})
            d = r.json()
            if r.status_code != 200:
                print(f"⚠️ Recherche Gemini {ticker} : HTTP {r.status_code} "
                      f"{str(d.get('error', {}).get('message', ''))[:150]}")
                continue
        except Exception as e:
            print(f"⚠️ Recherche Gemini {ticker} : {e}")
            continue
        try:
            import api_costs
            u = d.get("usageMetadata") or {}
            cache = u.get("cachedContentTokenCount", 0) or 0
            # Les jetons de réflexion sont facturés comme la sortie.
            api_costs.record(d.get("modelVersion") or model,
                             max(0, (u.get("promptTokenCount", 0) or 0) - cache),
                             (u.get("candidatesTokenCount", 0) or 0)
                             + (u.get("thoughtsTokenCount", 0) or 0), 0, cache)
        except Exception as e:
            print(f"[api costs] suivi recherche Gemini impossible : {e}")
        c = (d.get("candidates") or [{}])[0]
        txt = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", [])).strip()
        gm = c.get("groundingMetadata") or {}
        sources = [ch.get("web", {}).get("title", "") for ch in gm.get("groundingChunks", [])]
        sources = list(dict.fromkeys(s for s in sources if s))
        if not gm.get("webSearchQueries") or not sources:
            print(f"⚠️ Recherche Gemini {ticker} : réponse sans recherche web "
                  f"(essai {essai}) — rejetée")
            continue
        if len(txt) < 300:
            continue
        return (txt + "\n\nSOURCES CONSULTÉES (Google Search via Gemini)\n"
                + "\n".join(f"- {s}" for s in sources[:15]))
    return ""


def _deep_api(prompt: str, ticker: str) -> str:
    """Dossier via la clé API Anthropic (Opus 5.5 + web search serveur), ou "".

    Payant (~0,25 $ : recherches + résultats lus en entrée) : réservé au
    CONTRÔLE PRÉ-ACHAT (au plus AUTO_MAX_ENTRIES_PER_DAY par jour), comme le
    reste de la chaîne de secours (budget clé API ≈ 10 $/an). Rejeté si aucune
    recherche n'a réellement abouti."""
    import os
    if os.getenv("DEEP_RESEARCH_API", "1") == "0":
        return ""
    try:
        import ai_provider
        if not ai_provider._env_key("ANTHROPIC_API_KEY"):
            return ""
        prov = ai_provider.AnthropicProvider()
        messages = [{"role": "user", "content": prompt}]
        sources, txt = [], ""
        for _ in range(3):        # pause_turn : le serveur rend la main, on relance
            msg = prov.client.messages.create(
                model=prov.DEFAULT_MODEL, max_tokens=16000, system=_DEEP_SYSTEM,
                output_config={"effort": "medium"}, messages=messages,
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}])
            prov._track(prov.DEFAULT_MODEL, msg)
            for b in msg.content:
                if getattr(b, "type", "") == "web_search_tool_result" and isinstance(b.content, list):
                    sources += [getattr(r, "url", "") for r in b.content]
            if msg.stop_reason != "pause_turn":
                txt = prov._text(msg)
                break
            messages = [messages[0], {"role": "assistant", "content": msg.content}]
    except Exception as e:
        print(f"⚠️ Recherche API Anthropic {ticker} : {str(e)[:200]}")
        return ""
    domaines = list(dict.fromkeys(re.sub(r"^https?://(www\.)?([^/]+).*$", r"\2", u)
                                  for u in sources if u))
    if not domaines or len(txt) < 300:
        print(f"⚠️ Recherche API Anthropic {ticker} : sans recherche web aboutie — rejetée")
        return ""
    return (txt + "\n\nSOURCES CONSULTÉES (recherche web Claude, clé API)\n"
            + "\n".join(f"- {d}" for d in domaines[:15]))


def deep_research(ticker: str, company_name: str = "", api: bool = False) -> str:
    """Dossier de recherche sourcé du jour. Chaîne : CLI claude (abonnement)
    → clé API Anthropic (seulement si `api`, c.-à-d. au contrôle pré-achat)
    → Gemini + Google Search. "" si tout échoue — l'appelant doit alors
    refuser d'acheter : pas d'achat sans recherche fiable."""
    import os
    from datetime import datetime
    if os.getenv("DEEP_RESEARCH", "1") == "0":
        return ""
    jour = datetime.now().strftime("%Y-%m-%d")
    cle = f"{ticker.upper()}|{jour}"
    cle_src = f"{ticker.upper()}|src|{jour}"
    try:
        cache = json.loads(_DEEP_CACHE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    # Un dossier Gemini du scan ne dispense pas le contrôle pré-achat de
    # retenter Claude (CLI puis clé API) : c'est la chaîne de l'achat.
    if cache.get(cle) and not (api and cache.get(cle_src) == "gemini"):
        return cache[cle]
    prompt = DEEP_PROMPT.format(nom=company_name or ticker, ticker=ticker,
                                date=datetime.now().strftime("%d/%m/%Y"))
    txt, src = "", "claude"
    try:
        import ai_provider
        if ai_provider.cli_path():
            txt = ai_provider.ClaudeCliProvider().research(prompt)
    except Exception as e:
        print(f"⚠️ Recherche approfondie {ticker} : {e}")
        _alerte_cli(e)
    if len(txt or "") < 300 and api:
        txt, src = _deep_api(prompt, ticker), "api"
        if txt:
            print(f"[deep research] {ticker} : dossier via la clé API Anthropic")
    if len(txt or "") < 300 and cache.get(cle):
        return cache[cle]                  # le dossier Gemini du jour reste valable
    if len(txt or "") < 300:
        txt, src = _deep_gemini(prompt, ticker), "gemini"
        if txt:
            print(f"[deep research] {ticker} : dossier via Gemini + Google Search")
    if len(txt or "") < 300:
        return ""
    cache = {k: v for k, v in cache.items() if k.endswith(jour)}   # purge des jours passés
    cache[cle], cache[cle_src] = txt, src
    try:
        _DEEP_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return txt
