from datetime import datetime
import pytz
import history
import portfolio
import prices

PARIS = pytz.timezone("Europe/Paris")


# La persistance de l'historique vit dans history.py (module feuille) :
# `stats` et `lessons` s'importaient mutuellement pour y accéder.
load_history = history.load
save_history = history.save


def held_days(opened_at: str | None, closed_at: str) -> float | None:
    """Durée de détention en jours (décimale), ou None si l'entrée est inconnue.

    Décimale et non entière : un aller-retour dans la journée est le trade le
    plus rapide qui soit, et l'arrondir à 0 jour le rendrait indistinguable
    d'une donnée manquante.
    """
    if not opened_at:
        return None
    try:
        d0 = datetime.fromisoformat(opened_at)
        d1 = datetime.fromisoformat(closed_at)
    except (TypeError, ValueError):
        return None
    if d0.tzinfo is None:
        d0 = PARIS.localize(d0)
    if d1.tzinfo is None:
        d1 = PARIS.localize(d1)
    return round(max(0.0, (d1 - d0).total_seconds() / 86400), 2)


def record_close(name: str, ticker: str, qty: int, entry_price: float,
                 exit_price: float, fees: float = 0.0,
                 opened_at: str | None = None) -> float:
    """Enregistre un trade clôturé (+ contexte d'entrée et post-mortem) et
    retourne le P&L net.

    `opened_at` vient de la position (`portfolio.new_position`) et permet de
    chiffrer COMBIEN DE TEMPS le trade a pris. Il est passé explicitement plutôt
    que relu ici : au moment de l'appel la position est encore en base mais sur
    le point d'être retirée, et faire dépendre un KPI de cet ordre-là serait le
    genre de fragilité qui se paie six mois plus tard.
    """
    import portfolio
    data = load_history()
    pnl = round((exit_price - entry_price) * qty - fees, 2)
    result = "win" if pnl > 0 else "loss"
    closed_at = portfolio.now_iso()

    # Brique 2 : récupère le POURQUOI de l'entrée et en tire des leçons.
    ctx = portfolio.get_entry_context(ticker) or portfolio.get_entry_context(name)
    try:
        import lessons
        tags = lessons.post_mortem(ctx, entry_price, exit_price, result)
    except Exception:
        tags = []

    # P&L en EUR — le bilan est tenu en euros alors que le trade se dénoue dans
    # la devise de cotation. Sans ça la perte JNJ du 30/07/2026 (-48.82 $) a été
    # additionnée telle quelle à des gains en euros : le total était faux de
    # ~6 € sur ce seul trade, et l'erreur grandit avec chaque trade US.
    cur = prices._ticker_currency(ticker)
    pnl_eur = round(pnl * prices.fx_to_eur(cur), 2)

    record = {
        "name":         name,
        "ticker":       ticker,
        "qty":          qty,
        "entry_price":  entry_price,
        "exit_price":   exit_price,
        "fees":         fees,
        "pnl":          pnl,
        "currency":     cur,
        "pnl_eur":      pnl_eur,
        "result":       result,
        "date":         datetime.now(PARIS).strftime("%Y-%m-%d"),
        "source":       ctx.get("source", "inconnu"),
        "entry_context": ctx,
        "lessons":      tags,
        # ── Combien de temps ce trade a-t-il pris ? ──────────────────────
        # `held_source` sépare le mesuré du reconstitué : un KPI de vitesse
        # bâti sur un mélange des deux sans le dire ne vaut rien.
        "opened_at":    opened_at,
        "closed_at":    closed_at,
        "held_days":    held_days(opened_at, closed_at),
        "held_source":  "exact" if opened_at else None,
    }
    data["closed_trades"].append(record)
    save_history(data)
    portfolio.clear_entry_context(ticker)
    portfolio.clear_entry_context(name)
    return pnl


def get_stats() -> dict:
    # `closed` et non `history = load_history()` : ce nom masquait désormais le
    # module `history` importé en tête, et toute utilisation ultérieure du
    # module dans cette fonction aurait levé un AttributeError.
    closed = history.closed_trades()

    wins   = [t for t in closed if t["result"] == "win"]
    losses = [t for t in closed if t["result"] == "loss"]

    # TOUJOURS raisonner en euros : `pnl` est dans la devise du trade, `pnl_eur`
    # est la conversion faite à la clôture (trades antérieurs au 30/07/2026 :
    # pas de champ → ils étaient tous en euros, `pnl` fait foi).
    def _eur(t):
        return t.get("pnl_eur", t["pnl"])

    realized_pnl  = sum(_eur(t) for t in closed)
    win_rate      = (len(wins) / len(closed) * 100) if closed else 0
    avg_win       = sum(_eur(t) for t in wins)   / len(wins)   if wins   else 0
    avg_loss      = sum(_eur(t) for t in losses) / len(losses) if losses else 0
    gross_wins    = sum(_eur(t) for t in wins)
    gross_losses  = abs(sum(_eur(t) for t in losses))
    profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else None
    best  = max(closed, key=_eur) if closed else None
    worst = min(closed, key=_eur) if closed else None

    # P&L latent des positions ouvertes GÉRÉES par le bot. Les positions HOLD
    # long terme (hold: true, ex ILMN) sont hors périmètre trading : leur
    # latent n'entre pas dans le bilan du bot.
    # Deux pièges corrigés le 29/07/2026 :
    #  1. un cours indisponible (rate-limit yfinance, suspension) faisait
    #     DISPARAÎTRE la position du total, sans aucun signal : le latent
    #     affichait +7.91€ au lieu de +74.91€ car AIR était muet à cet instant.
    #     On remonte donc la liste des positions non valorisées à l'appelant.
    #  2. aucune conversion de devise : le P&L d'une position en USD était
    #     additionné tel quel à un total en EUR.
    unrealized_pnl = 0.0
    unpriced: list[str] = []
    positions = portfolio.get_managed_positions()
    # Cours retenu et conversion en euros : position_view (source unique). Un
    # cours périmé fausse le P&L latent aussi sûrement qu'un cours manquant.
    import position_view
    for v in position_view.views(positions):
        if v["pnl_eur"] is None:
            unpriced.append(v["name"])
            continue
        unrealized_pnl += v["pnl_eur"]

    # Coûts API IA — 2e charge réelle après les frais de courtage (déjà déduits
    # par trade). Sans eux, le bilan surestime l'efficacité du bot.
    try:
        import api_costs
        costs = api_costs.get_costs()
        api_cost_eur = costs["total_eur"]
        api_month_eur = costs["month_eur"]
        # Quel modèle répond VRAIMENT. Le bot est passé sur le fallback Gemini
        # le 20/07/2026 (crédit Anthropic épuisé) et n'a plus servi un seul
        # appel Anthropic depuis — sans que rien ne l'indique nulle part.
        api_model = costs.get("top_model")
        api_fournisseurs = costs.get("par_fournisseur", {})
    except Exception:
        api_cost_eur, api_month_eur, api_model = 0.0, 0.0, None
        api_fournisseurs = {}

    # ── Combien de temps prend un trade ? ────────────────────────────────
    # KPI demandé le 13/08/2026 : trouver les trades RAPIDES. Un gain de 100 €
    # en trois jours et le même en trois mois n'ont pas la même valeur — le
    # capital immobilisé n'a pas travaillé pendant ce temps.
    def _duree(ts):
        vals = sorted(t["held_days"] for t in ts if t.get("held_days") is not None)
        if not vals:
            return None
        milieu = len(vals) // 2
        return {
            "n":      len(vals),
            "median": vals[milieu] if len(vals) % 2 else round((vals[milieu - 1] + vals[milieu]) / 2, 2),
            "avg":    round(sum(vals) / len(vals), 2),
            "min":    vals[0],
            "max":    vals[-1],
        }

    chronometres = [t for t in closed if t.get("held_days") is not None]
    # Gain par jour de détention : le classement qui répond vraiment à « quels
    # trades vont vite ». Un +8 % en 2 jours passe devant un +25 % en 40 jours.
    par_jour = sorted(
        ({**t, "eur_per_day": round(_eur(t) / max(t["held_days"], 0.25), 2)}
         for t in chronometres if _eur(t) > 0),
        key=lambda t: -t["eur_per_day"],
    )

    total_pnl = round(realized_pnl + unrealized_pnl, 2)
    return {
        "hold":          _duree(chronometres),
        "hold_wins":     _duree([t for t in chronometres if t["result"] == "win"]),
        "hold_losses":   _duree([t for t in chronometres if t["result"] == "loss"]),
        # Trades dont la durée est inconnue : à dire, pas à masquer — sinon la
        # médiane porte sur un échantillon dont on ignore la taille réelle.
        "hold_unknown":  len(closed) - len(chronometres),
        "fastest_wins":  par_jour[:3],
        "nb_closed":      len(closed),
        "nb_wins":        len(wins),
        "nb_losses":      len(losses),
        "win_rate":       round(win_rate, 1),
        "realized_pnl":   round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "unpriced":       unpriced,   # positions non valorisables : le latent est PARTIEL
        "total_pnl":      total_pnl,
        "api_cost_eur":   api_cost_eur,
        "api_month_eur":  api_month_eur,
        "api_model":      api_model,
        "api_fournisseurs": api_fournisseurs,
        "net_pnl":        round(total_pnl - api_cost_eur, 2),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "profit_factor":  profit_factor,
        "best_trade":     best,
        "worst_trade":    worst,
    }
