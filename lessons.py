"""
Boucle d'apprentissage du bot (briques 2 et 3).

Un LLM n'apprend pas par entraînement ici. À la place :
  - brique 2 : POST-MORTEM — à la clôture, on croise le contexte d'entrée
    (thèse, régime, RSI/momentum/volume, source) avec le résultat pour taguer
    ce qui a marché / raté.
  - brique 3 : LEÇONS — on agrège les tags sur l'historique et on réinjecte un
    bloc de rappels dans les prompts de scan/validation, pour que l'IA évite de
    répéter les mêmes erreurs. Plus des garde-fous PILOTÉS PAR LES DONNÉES
    (indépendants de l'IA) : cooldown après perte, réduction de taille en série.
"""
from datetime import datetime, timedelta
import pytz

PARIS = pytz.timezone("Europe/Paris")


# ── Brique 2 : post-mortem d'un trade ────────────────────────────────────────

def post_mortem(ctx: dict, entry: float, exit_price: float, result: str) -> list[str]:
    """
    Retourne une liste de tags explicatifs (courts, réutilisables en agrégat).
    `ctx` = contexte d'entrée mémorisé (portfolio.get_entry_context).
    """
    # Contexte vide = la capture a raté (bug), PAS "aucun signal d'alerte".
    # Taguer "perte sans signal" sur un contexte absent empoisonne les leçons :
    # AF.PA 07/2026 avait RSI ~71 à résistance, tagué à tort "sans alerte".
    if not ctx:
        return ["contexte d'entrée non capturé (bug de capture) — leçon inexploitable"]

    tags = []
    rsi = ctx.get("rsi")
    mom = ctx.get("momentum_1m")
    vol = ctx.get("vol_ratio")
    perf1y = ctx.get("perf_1y")
    from_low = ctx.get("from_52w_low")
    win = result == "win"

    chg = (exit_price - entry) / entry * 100 if entry else 0

    if win:
        if rsi is not None and rsi >= 65:
            tags.append("gagnant malgré RSI élevé — momentum a tenu")
        else:
            tags.append("gagnant sur momentum sain")
        return tags

    # Perdants : on cherche le défaut d'ENTRÉE
    if rsi is not None and rsi >= 70:
        tags.append("entrée en surchauffe (RSI ≥ 70)")
    if mom is not None and mom < 0:
        tags.append("acheté en momentum 1M négatif")
    if perf1y is not None and perf1y < -20:
        tags.append("couteau qui tombe (perf 1 an < -20%)")
    if from_low is not None and from_low < 15:
        tags.append("acheté près du plus-bas 52s (< +15%)")
    if vol is not None and vol < 0.8:
        tags.append("volume faible à l'entrée (< 0.8×)")
    if chg <= -9:
        tags.append("gap sous le SL — titre peu liquide / volatil")
    if not tags:
        tags.append("perte sans signal d'alerte évident à l'entrée")
    return tags


# ── Brique 3 : agrégation → bloc de leçons pour les prompts ──────────────────

def _closed_with_context() -> list[dict]:
    import stats
    return [t for t in stats.load_history().get("closed_trades", [])
            if t.get("entry_context") or t.get("lessons")]


def build_lessons_block(max_lines: int = 6) -> str:
    """
    Bloc texte compact injecté dans les prompts. Vide si pas assez de données
    (< 3 trades tagués) — on n'invente pas de leçons prématurément.
    """
    import stats
    trades = stats.load_history().get("closed_trades", [])
    tagged = [t for t in trades if t.get("lessons")]
    if len(tagged) < 3:
        return ""

    losers = [t for t in tagged if t.get("result") == "loss"]
    from collections import Counter
    tag_counts = Counter(tag for t in losers for tag in t.get("lessons", []))

    lines = ["LEÇONS DES TRADES PASSÉS (évite de répéter ces erreurs) :"]
    for tag, n in tag_counts.most_common(max_lines):
        times = f" ({n}×)" if n > 1 else ""
        lines.append(f"- {tag}{times}")

    # Statistique de cadrage
    wins = sum(1 for t in tagged if t.get("result") == "win")
    wr = round(wins / len(tagged) * 100)
    lines.append(f"(historique tagué : {len(tagged)} trades, {wr}% gagnants — "
                 f"privilégie la QUALITÉ d'entrée à la quantité)")
    return "\n".join(lines)


# ── Garde-fous pilotés par les données (indépendants de l'IA) ────────────────

def recent_loss(ticker: str, days: int = 10) -> dict | None:
    """
    Ce ticker a-t-il été clôturé en PERTE récemment ? Retourne le trade ou None.
    Évite de re-rentrer immédiatement sur un titre qui vient de coûter.
    """
    import stats
    base = (ticker or "").upper().split(".")[0]
    cutoff = datetime.now(PARIS).date() - timedelta(days=days)
    for t in reversed(stats.load_history().get("closed_trades", [])):
        if t.get("result") != "loss":
            continue
        if (t.get("ticker", "").upper().split(".")[0] != base
                and t.get("name", "").upper() != base):
            continue
        try:
            d = datetime.fromisoformat(_date_iso(t.get("date", ""))).date()
        except Exception:
            continue
        if d >= cutoff:
            return t
    return None


def loss_streak() -> int:
    """Nombre de pertes consécutives sur les derniers trades clôturés."""
    import stats
    n = 0
    for t in reversed(stats.load_history().get("closed_trades", [])):
        if t.get("result") == "loss":
            n += 1
        else:
            break
    return n


def size_factor() -> float:
    """
    Multiplicateur de taille de position selon la série de pertes :
    2 pertes → 0.75, 3 → 0.5, 4+ → 0.35. Réduit l'exposition quand ça enchaîne.
    """
    s = loss_streak()
    return {0: 1.0, 1: 1.0, 2: 0.75, 3: 0.5}.get(s, 0.35)


def _date_iso(d: str) -> str:
    d = (d or "").strip()
    return d + "-01" if len(d) == 7 else (d or "1970-01-01")
