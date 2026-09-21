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
import history
from config import (ENTRY_QUALITY_VETO, ENTRY_MIN_VOL_RATIO, ENTRY_MAX_MOM_1M)

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
    # « Gap » = le cours a franchi le SL SANS s'y arrêter. Ça ne se mesure pas
    # à un seuil fixe : avec MAX_SL_PCT=10, un stop touché normalement sur un
    # titre volatil sort à -9.6% et se faisait taguer « gap » à tort (AGRO,
    # SL à -9.6%). Le repère est la distance du SL RÉELLEMENT posé, mémorisée
    # à l'entrée ; -9 ne sert que pour les contextes anciens qui l'ignorent.
    sl_pct = ctx.get("sl_pct")
    gap_floor = (sl_pct - 1.5) if isinstance(sl_pct, (int, float)) and sl_pct < 0 else -9
    if chg <= gap_floor:
        tags.append("gap sous le SL — titre peu liquide / volatil")
    if not tags:
        tags.append("perte sans signal d'alerte évident à l'entrée")
    return tags


# ── Brique 3 : agrégation → bloc de leçons pour les prompts ──────────────────

def build_lessons_block(max_lines: int = 6) -> str:
    """
    Bloc texte compact injecté dans les prompts. Vide si pas assez de données
    (< 3 trades tagués) — on n'invente pas de leçons prématurément.
    """
    trades = history.closed_trades()
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

def entry_quality_veto(tech: dict) -> str | None:
    """
    Refuse AVANT l'ordre les deux défauts d'entrée que `post_mortem` ne savait
    nommer qu'APRÈS la perte. Retourne la raison du refus, ou None.

    C'est la moitié manquante de la brique 3 : jusqu'au 21/09/2026 les leçons
    n'étaient QUE du texte injecté dans les prompts (`build_lessons_block`) —
    un rappel que le modèle pouvait ignorer, et que rien ne vérifiait sur les
    chiffres au moment de passer l'ordre. Ici la règle est dure et lit les
    mêmes indicateurs que le post-mortem.

    Donnée manquante = pas de veto : on ne refuse jamais une entrée sur une
    absence d'information (yfinance rend None sur les titres peu suivis).
    """
    if not ENTRY_QUALITY_VETO:
        return None
    tech = tech or {}
    vol = tech.get("vol_ratio")
    if isinstance(vol, (int, float)) and vol < ENTRY_MIN_VOL_RATIO:
        return (f"volume à {vol:.2f}× sa moyenne 20 j (seuil {ENTRY_MIN_VOL_RATIO}×) — "
                f"hausse non confirmée par les échanges")
    mom = tech.get("momentum_1m")
    if isinstance(mom, (int, float)) and mom > ENTRY_MAX_MOM_1M:
        return (f"momentum 1 mois {mom:+.1f}% (seuil +{ENTRY_MAX_MOM_1M:.0f}%) — "
                f"entrée APRÈS l'envolée, le risque de retour à la moyenne est pour nous")
    return None


# ── Brique 1 : capture du contexte d'entrée ─────────────────────────────────

def build_entry_context(ticker: str, source: str, thesis: str = "",
                        entry: float | None = None, sl: float | None = None,
                        tp: float | None = None) -> dict:
    """
    Contexte d'entrée prêt à mémoriser : indicateurs du moment + distances
    SL/TP. Ne fait AUCUNE écriture — le sync tient déjà son `data` en mémoire
    et un `portfolio.save` intercalé écraserait ses propres modifications.

    Best-effort : dict vide si les cours ne répondent pas. Un contexte vide
    vaut mieux qu'une exception dans un chemin d'achat.
    """
    try:
        import prices
        tech = prices.get_technicals(ticker) or {}
        pctx = prices.get_price_context(ticker) or {}
    except Exception as e:
        print(f"[lessons] contexte {ticker}: {e}")
        return {}
    ctx = {
        "source":       source,
        "thesis":       (thesis or "")[:200],
        "rsi":          tech.get("rsi"),
        "momentum_1m":  tech.get("momentum_1m"),
        "mom_12_1":     tech.get("mom_12_1"),
        "above_ma200":  tech.get("above_ma200"),
        "atr_pct":      tech.get("atr_pct"),
        "vol_ratio":    tech.get("vol_ratio"),
        "perf_1y":      pctx.get("perf_1y"),
        "from_52w_low": pctx.get("from_52w_low"),
    }
    ctx.update(entry_distances(entry, sl, tp))
    return {k: v for k, v in ctx.items() if v is not None}


def entry_distances(entry: float | None, sl: float | None,
                    tp: float | None) -> dict:
    """Distances SL/TP en % de l'entrée. `sl_pct` est ce qui permet au
    post-mortem de distinguer un stop touché normalement d'un vrai gap."""
    out = {}
    if not entry:
        return out
    out["entry"] = round(entry, 4)
    if sl:
        out["sl_pct"] = round((sl - entry) / entry * 100, 1)
    if tp:
        out["tp_pct"] = round((tp - entry) / entry * 100, 1)
    return out


def capture_entry_context(ticker: str, source: str, thesis: str = "",
                          entry: float | None = None, sl: float | None = None,
                          tp: float | None = None) -> bool:
    """
    Mémorise le contexte d'entrée s'il manque, et le COMPLÈTE s'il existe déjà
    sans distances SL/TP (cas d'un contexte capturé au scan, avant que le SL
    réel soit connu). Retourne True si quelque chose a été écrit.

    Ne remplace jamais un contexte existant : celui du scan a été pris au
    moment de la DÉCISION, il vaut mieux que celui du moment de l'exécution.
    """
    try:
        import portfolio
        existing = portfolio.get_entry_context(ticker)
        if not existing:
            ctx = build_entry_context(ticker, source, thesis, entry, sl, tp)
            if not ctx:
                return False
            portfolio.set_entry_context(ticker, ctx)
            return True
        missing = {k: v for k, v in entry_distances(entry, sl, tp).items()
                   if existing.get(k) is None}
        if missing:
            existing.update(missing)
            portfolio.set_entry_context(ticker, existing)
            return True
        return False
    except Exception as e:
        print(f"[lessons] capture contexte {ticker}: {e}")
        return False



def recent_loss(ticker: str, days: int = 10) -> dict | None:
    """
    Ce ticker a-t-il été clôturé en PERTE récemment ? Retourne le trade ou None.
    Évite de re-rentrer immédiatement sur un titre qui vient de coûter.
    """
    base = (ticker or "").upper().split(".")[0]
    cutoff = datetime.now(PARIS).date() - timedelta(days=days)
    for t in reversed(history.closed_trades()):
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
    n = 0
    for t in reversed(history.closed_trades()):
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
