"""
Briques de CONTEXTE injectées dans les prompts IA.

Ce que le bot raconte au modèle avant de lui demander quoi que ce soit :
l'état du portefeuille, le régime de marché, les leçons des trades passés, le
contexte personnel de l'utilisateur. Plus les outils qui lisent la RÉPONSE
(verdict, avertissements).

Séparé de `analysis` — qui orchestre les appels IA — parce que ce sont deux
métiers : ici on FABRIQUE du texte à partir de données locales, sans jamais
appeler un modèle. C'est aussi ce qui rend ces fonctions testables sans réseau
(cf. tests/test_position_view.py::TestSnapshotIA).
"""
import math
import re
from datetime import datetime

import pytz

from ai_provider import get_provider
import portfolio
import position_view
import prices
from config import (TRADING_CONTEXT_PATH, MACRO_ANALYSIS_PATH,
                    EARNINGS_VETO_DAYS)

PARIS = pytz.timezone("Europe/Paris")

def _earnings_note(next_earnings: str) -> str:
    """'2026-08-04' → '2026-08-04 (dans 19 j)'. Donne à l'IA le nombre exact de
    jours pour appliquer le veto numérique EARNINGS_VETO_DAYS sans improviser.
    Chaîne brute si parsing impossible ; date passée (donnée obsolète) → brute."""
    if not next_earnings:
        return ""
    try:
        d = datetime.strptime(str(next_earnings)[:10], "%Y-%m-%d").date()
        days = (d - datetime.now(PARIS).date()).days
        return f"{next_earnings} (dans {days} j)" if days >= 0 else str(next_earnings)
    except Exception:
        return str(next_earnings)


def _lessons_block() -> str:
    """Bloc de leçons (brique 3) à injecter dans les prompts de validation.
    Vide tant qu'il n'y a pas assez de trades tagués — jamais bloquant."""
    try:
        import lessons
        b = lessons.build_lessons_block()
        return f"\n{b}\n" if b else ""
    except Exception:
        return ""


def _entry_ctx(tech: dict, pctx: dict, thesis: str, source: str,
               regime: str = "") -> dict:
    """Assemble le contexte d'entrée mémorisé pour la boucle d'apprentissage
    (brique 1) : indicateurs au moment de la décision + thèse + régime."""
    tech = tech or {}
    pctx = pctx or {}
    return {
        "source":      source,
        "regime":      regime,
        "rsi":         tech.get("rsi"),
        "momentum_1m": tech.get("momentum_1m"),
        "mom_12_1":    tech.get("mom_12_1"),
        "above_ma200": tech.get("above_ma200"),
        "atr_pct":     tech.get("atr_pct"),
        "vol_ratio":   tech.get("vol_ratio"),
        "perf_1y":     pctx.get("perf_1y"),
        "from_52w_low": pctx.get("from_52w_low"),
        "thesis":      (thesis or "").strip()[:200],
    }


def _strip_markdown(text: str) -> str:
    """Supprime les symboles Markdown résiduels pour un affichage propre sur Telegram."""
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)   # titres #
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)         # gras/italique
    text = re.sub(r'`{1,3}([^`]*)`{1,3}', r'\1', text)           # code inline/block
    text = re.sub(r'^-{3,}\s*$', '---', text, flags=re.MULTILINE) # hr
    text = re.sub(r'^\s*>\s*', '', text, flags=re.MULTILINE)       # blockquotes
    text = re.sub(r'\n{3,}', '\n\n', text)                         # espaces excessifs
    return text.strip()


def _trading_context() -> str:
    """Charge le contexte personnel de trading si le fichier existe."""
    try:
        if TRADING_CONTEXT_PATH.exists():
            return TRADING_CONTEXT_PATH.read_text(encoding="utf-8")
    except Exception:
        pass
    return ""


def _macro_summary(content: str, mtime: float) -> str:
    """Résumé (~2500 chars) de l'analyse macro, mis en cache par mtime.
    En cas d'échec IA : texte intégral (comportement d'avant, jamais dégradé)."""
    import json as _json
    try:
        cached = _json.loads(_MACRO_CACHE_PATH.read_text(encoding="utf-8"))
        if cached.get("mtime") == mtime and cached.get("summary"):
            return cached["summary"]
    except Exception:
        pass
    try:
        summary = get_provider().complete_cheap(
            "Condense cette analyse macro sectorielle en 2500 caractères MAXIMUM, "
            "texte brut sans markdown. GARDE impérativement : les convictions "
            "sectorielles avec leur direction (surpondérer/éviter), les niveaux et "
            "dates clés, les risques majeurs datés, les recommandations concrètes. "
            "SUPPRIME : narratif, répétitions, contexte historique générique.\n\n"
            + content,
            max_tokens=1200,
        ).strip()
        if len(summary) < 200:   # réponse anormalement courte → ne pas dégrader
            return content
        _MACRO_CACHE_PATH.write_text(
            _json.dumps({"mtime": mtime, "summary": summary}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[macro] résumé regénéré ({len(content)} → {len(summary)} chars)")
        return summary
    except Exception as e:
        print(f"[macro] résumé impossible ({e}) — texte intégral utilisé")
        return content


def _macro_context() -> str:
    """Charge l'analyse macro sectorielle si macro_analysis.md existe (document
    daté, mis à jour par l'utilisateur). Les documents longs sont condensés par
    le modèle cheap (cache sur mtime) avant injection dans les prompts."""
    try:
        if MACRO_ANALYSIS_PATH.exists():
            content = MACRO_ANALYSIS_PATH.read_text(encoding="utf-8")
            mtime_ts = MACRO_ANALYSIS_PATH.stat().st_mtime
            mtime = datetime.fromtimestamp(mtime_ts).strftime("%d/%m/%Y")
            if len(content) > _MACRO_SUMMARY_THRESHOLD:
                content = _macro_summary(content, mtime_ts)
            return (
                f"\n--- ANALYSE MACRO SECTORIELLE (rédigée/mise à jour le {mtime}) ---\n"
                f"{content}\n"
                f"--- FIN ANALYSE MACRO (point dans le temps — peut être obsolète) ---\n"
            )
    except Exception:
        pass
    return ""


def _portfolio_snapshot() -> str:
    data = portfolio.load()
    cash = data.get("cash_available", 0)
    positions = data.get("positions", {})
    today = datetime.now(PARIS).strftime("%d/%m/%Y")
    lines = [
        f"SNAPSHOT PORTEFEUILLE — SOURCE DE VÉRITÉ — {today}",
        f"💰 Cash: {cash}€",
        "📁 Positions (UNIQUEMENT ces positions sont actives — ignorer tout autre mention) :",
    ]
    # Les HOLD long terme sortent du périmètre de gestion : listés à part avec
    # interdiction explicite pour l'IA de proposer vente, swap ou protection.
    holds = {k: v for k, v in positions.items() if v.get("hold")}
    positions = {k: v for k, v in positions.items() if not v.get("hold")}
    # Cours retenu, P&L, provenance : calculés par position_view — comme
    # /status, le STATUS planifié, le dashboard et /stats. Un briefing bâti sur
    # des cours périmés raisonne juste sur des chiffres faux, et rien dans le
    # prompt ne permet à l'IA de s'en apercevoir.
    for v in position_view.views(positions):
        if v["price"] and not math.isnan(v["price"]):
            sym = v["sym"]
            cur_tag = (" | ⚠️ perf aberrante, PRU probablement dans la mauvaise devise — ignorer ce P&L"
                       if v["aberrant"] else "")
            # L'IA, elle, DOIT savoir qu'un cours est vieux : elle raisonne
            # dessus. Mais seulement quand il l'est vraiment — le critère est
            # l'âge du cours, pas la bibliothèque qui l'a fourni.
            if v["stale"]:
                cur_tag += f" | ⚠️ {v['note']}"
            # ── Ce que l'IA ne voyait PAS avant (11/08/2026) ────────────────
            # Le snapshot annoncé comme « SOURCE DE VÉRITÉ » présentait les
            # SL/TP comme des faits, sans jamais dire qu'aucun ordre ne les
            # portait sur BD. Du 31/07 au 05/08, l'IA a raisonné chaque matin
            # comme si BAC était protégé alors qu'il était à nu.
            if v["protected"] is False:
                cur_tag += (" | 🚨 SL/TP NON PROTECTEURS : aucun ordre actif sur "
                            "BD ne les porte — la position n'a AUCUN stop réel")
            if v["pending_sl"]:
                cur_tag += (f" | ⏳ SL {v['pending_sl']} calculé mais PAS posé "
                            f"sur BD, le stop actif reste {v['sl']}")
            lines.append(
                f"  {v['name']} ({v['ticker']}): {sym}{v['price']} ({v['chg_pct']:+.2f}%) | "
                f"PRU {sym}{v['entry']} | {v['qty']}t | P&L {sym}{v['pnl']:+.0f} | "
                f"SL {sym}{v['sl']} | TP {sym}{v['tp']}{cur_tag}"
            )
        else:
            # Le relevé BD tranche : si le courtier valorise le titre, il n'est
            # pas suspendu — c'est le ticker stocké qui est faux.
            code, msg = v["problem"]
            icon = {"ticker": "🚨", "suspended": "⛔"}.get(code, "⚠️")
            suffix = " (liquidation judiciaire ?)" if code == "suspended" else ""
            lines.append(
                f"  {v['name']} ({v['ticker']}): {icon} {msg}{suffix} | "
                f"PRU {v['entry']} | {v['qty']}t"
            )

    if holds:
        lines.append("🔒 HOLD LONG TERME — HORS GESTION (ne JAMAIS proposer de vente, "
                     "swap, SL/TP ou analyse pour ces titres) :")
        for name, cfg in holds.items():
            lines.append(f"  {name} ({cfg['ticker']}): {cfg['qty']}t | "
                         f"{cfg.get('hold_note', 'hold long terme')}")

    pending = data.get("pending_orders", {})
    if pending:
        lines.append("⏳ Ordres en attente (cash réservé) :")
        for name, cfg in pending.items():
            quote = prices.get_quote(cfg["ticker"])
            price = quote.get("price") or "?"
            drift = ""
            if isinstance(price, float):
                d = ((price - cfg["entry_price"]) / cfg["entry_price"]) * 100
                drift = f" | cours actuel {price}€ ({d:+.1f}% vs entrée)"
            lines.append(
                f"  {name} ({cfg['ticker']}): achat limite {cfg['entry_price']}€ "
                f"x {cfg['qty']}t — {cfg['reserved_cash']:.0f}€ réservés{drift}"
            )

    return "\n".join(lines)


def _breach_warning(ticker: str, pru: float, sl: float) -> str | None:
    """Retourne un message d'alerte si le cours actuel a déjà franchi le SL ou dépasse +25%."""
    quote = prices.get_quote(ticker)
    price = quote.get("price")
    if not price or math.isnan(price):
        return None
    if price < sl:
        return f"⚠️ SL déjà dépassé : cours {price}€ < SL {sl}€ → /research {ticker}"
    if price > pru * 1.25:
        gain = ((price / pru) - 1) * 100
        return f"⚠️ TP dépassé (+{gain:.0f}%) : cours {price}€ → vendre ou /research {ticker}"
    return None


def _parse_verdict(val: str) -> tuple[str, str]:
    """
    Source UNIQUE de lecture d'un verdict IA (ACHAT / EXCLUS).
    L'IA peut écrire un en-tête société avant de dire EXCLU : on cherche sur les
    premières lignes, pas seulement en début de texte. Retourne (verdict, raison).
    Utilisé par TOUS les chemins de décision — garantit un jugement cohérent.
    """
    lines = val.strip().splitlines()
    head = "\n".join(lines[:5]).upper()
    if "EXCLU" in head or "ÉVITER" in head or "EVITER" in head:
        reason = "écarté"
        for line in lines:
            u = line.upper()
            if "EXCLU" in u or "ÉVITER" in u or "EVITER" in u:
                reason = line.split("—", 1)[1].strip()[:70] if "—" in line else line.strip()[:70]
                break
        return "EXCLUS", reason
    return "ACHAT", ""


def _regime_instructions(regime: str, regime_summary: str,
                         rel: float, index_mom: float) -> str:
    """Bloc d'instructions spécifique au régime — inchangé, factorisé ici pour
    être partagé par le scan et le briefing."""
    if regime == "CORRECTION":
        return f"""
RÉGIME : CORRECTION ({regime_summary})
Ce titre est sélectionné pour sa force relative ({rel:+.1f}% vs indice à {index_mom:+.1f}%).

MISSION CORRECTION — critères ACHAT valides dans ce contexte :
1. FORCE RELATIVE : l'action résiste ou monte pendant que l'indice baisse → thèse valide.
2. BÉNÉFICIAIRE MACRO : la cause probable de la correction (BCE hawkish → banques ;
   tensions géo → défense/énergie ; récession → pharma/utilities/consommation de base ;
   correction tech → value/industrielles) bénéficie directement à ce secteur.
3. REBOND TECHNIQUE QUALITÉ : RSI < 35, titre de qualité, tendance LT intacte,
   catalyseur de rebond identifiable.

Signal EXCLUS si : momentum positif MAIS corrélé à l'indice (force relative nulle),
ou si secteur cyclique sans thèse macro claire en contexte de correction."""
    if regime == "NEUTRAL":
        return f"""
RÉGIME : NEUTRE ({regime_summary})
Marché sans tendance d'indice claire. Les titres en momentum 12 mois propre
au-dessus de leur MM200 restent tradeables, mais EXIGE une force relative
positive vs l'indice (le filtre quantitatif l'a déjà vérifiée — confirme
qu'aucune news ne l'explique par un facteur non répétable). Gestion du SL serrée."""
    return f"""
RÉGIME : HAUSSIER ({regime_summary})
Conditions favorables. Scan momentum standard (12 mois hors dernier mois,
entrée sur repli sain)."""