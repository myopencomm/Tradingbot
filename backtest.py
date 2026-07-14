"""
Backtest de la stratégie de sélection (Phase 2 de l'audit 07/2026).

Compare sur l'univers de scan réel :
  A. OLD    — proxy de l'ancienne logique : classement momentum 1 mois,
              RSI < 75 accepté, SL -7% / TP +10%, ~50% du budget par trade.
  B. PHASE1 — stratégie livrée : momentum 12-1 + MM200 + RSI 35-65,
              SL 2×ATR borné 3-10%, TP ≥ 1.5R (min +10%), risque 1%/trade,
              coût ≤ 30% du budget, max 2 positions.
  C. RECOVERY — mêmes signaux que PHASE1, risque 2%/trade, coût ≤ 40%,
              max 3 positions (compte petit orienté rattrapage).

Hypothèses PESSIMISTES : entrée au lendemain du signal à l'open +0.3%
(limite marchande), si SL et TP touchés la même bougie → SL d'abord,
gap sous le SL → exécution à l'open du gap, frais 1.98€ par ordre.

Limites (à garder en tête) :
  - Univers = constituants ACTUELS (biais du survivant — favorise TOUTES
    les stratégies testées de la même façon, la comparaison reste valide).
  - Pas de conversion FX (P&L en devise locale agrégé 1:1).
  - L'étage de validation IA (news, OPA…) n'est pas simulable — on compare
    les moteurs QUANTITATIFS seuls.

Usage : venv/bin/python3 backtest.py [--start 2023-01-01] [--fast]
"""
import argparse
import sys
import numpy as np
import pandas as pd
import yfinance as yf

from analysis import SCAN_UNIVERSE

FEE = 1.98            # frais BD par ordre
BUDGET = 2000.0       # budget autonome de référence
BREAKEVEN_PCT = 3.0   # trailing : SL au PRU à +3%
MAX_HOLD_DAYS = 60    # time-stop (jours de bourse)


# ── Indicateurs vectorisés ────────────────────────────────────────────────────

def rsi14(closes: pd.Series) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def atr14_pct(df: pd.DataFrame) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - prev).abs(),
                    (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(14).mean() / df["Close"] * 100


def build_indicators(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = {}
    for t, df in data.items():
        if len(df) < 260:
            continue
        c = df["Close"]
        ind = pd.DataFrame(index=df.index)
        ind["close"] = c
        ind["open"] = df["Open"]
        ind["high"] = df["High"]
        ind["low"] = df["Low"]
        ind["rsi"] = rsi14(c)
        ind["mom_1m"] = (c / c.shift(21) - 1) * 100
        ind["mom_12_1"] = (c.shift(21) / c.shift(252) - 1) * 100
        ind["ma200"] = c.rolling(200).mean()
        ind["atr_pct"] = atr14_pct(df)
        out[t] = ind
    return out


# ── Moteur de simulation ──────────────────────────────────────────────────────

def simulate(ind: dict[str, pd.DataFrame], dates: pd.DatetimeIndex,
             regime_ok: pd.Series, *, mode: str,
             risk_pct: float = 1.0, max_pos: int = 2,
             max_cost_pct: float = 30.0, be_trail: bool = True,
             tp_mult_r: float | None = None, fee: float = FEE,
             cadence: str = "weekly") -> dict:
    """mode: 'old' (mom 1m, SL7/TP10, 50% budget) ou 'new' (12-1 + MM200 + ATR)."""
    positions = []   # {ticker, entry, sl, tp, qty, entry_date, be_done}
    closed = []
    equity = BUDGET

    # Signaux évalués chaque lundi ; cadence "monthly" = 1er lundi du mois
    weekly = dates[dates.weekday == 0]
    if cadence == "monthly":
        weekly = pd.DatetimeIndex([d for i, d in enumerate(weekly)
                                   if i == 0 or d.month != weekly[i - 1].month])

    for d in dates:
        # ── Gestion des positions ouvertes (bougie du jour) ────────────────
        still = []
        for p in positions:
            df = ind[p["ticker"]]
            if d not in df.index:
                still.append(p); continue
            row = df.loc[d]
            days_held = np.busday_count(p["entry_date"].date(), d.date())
            exit_price, why = None, None
            # Gap sous le SL → open ; sinon SL touché en séance → SL exact.
            if row["open"] <= p["sl"]:
                exit_price, why = row["open"], "SL(gap)"
            elif row["low"] <= p["sl"]:
                exit_price, why = p["sl"], "SL"
            elif row["high"] >= p["tp"]:
                exit_price, why = p["tp"], "TP"
            elif days_held >= MAX_HOLD_DAYS:
                exit_price, why = row["close"], "TIME"
            if exit_price is not None:
                pnl = (exit_price - p["entry"]) * p["qty"] - 2 * fee
                closed.append({"ticker": p["ticker"], "pnl": pnl, "why": why,
                               "entry_date": p["entry_date"], "exit_date": d,
                               "ret_pct": (exit_price / p["entry"] - 1) * 100})
                equity += pnl
                continue
            # Trailing breakeven (optionnel — testé avec/sans)
            if be_trail and not p["be_done"] and row["close"] >= p["entry"] * (1 + BREAKEVEN_PCT / 100):
                p["sl"] = max(p["sl"], p["entry"])
                p["be_done"] = True
            still.append(p)
        positions = still

        # ── Nouvelles entrées (signal du lundi, entrée à l'open du lendemain) ─
        if d not in weekly or len(positions) >= max_pos:
            continue
        if not bool(regime_ok.get(d, False)):
            continue
        held = {p["ticker"] for p in positions}
        cands = []
        for t, df in ind.items():
            if t in held or d not in df.index:
                continue
            r = df.loc[d]
            if r[["rsi", "mom_1m", "close"]].isna().any():
                continue
            if mode == "old":
                if r["rsi"] > 75 or r["rsi"] < 28 or r["mom_1m"] < -8:
                    continue
                cands.append((t, r["mom_1m"]))
            else:
                if (np.isnan(r["mom_12_1"]) or r["mom_12_1"] <= 0
                        or np.isnan(r["ma200"]) or r["close"] <= r["ma200"]
                        or not (35 <= r["rsi"] <= 65) or r["mom_1m"] < -12
                        or np.isnan(r["atr_pct"]) or 2 * r["atr_pct"] > 10):
                    continue
                cands.append((t, min(r["mom_12_1"], 80)))
        cands.sort(key=lambda x: -x[1])

        for t, _score in cands[: max_pos - len(positions)]:
            df = ind[t]
            nxt = df.index[df.index > d]
            if len(nxt) == 0:
                continue
            e_day = nxt[0]
            entry = float(df.loc[e_day, "open"]) * 1.003
            if np.isnan(entry) or entry <= 0:
                continue
            if mode == "old":
                sl, tp = entry * 0.93, entry * 1.10
                cost_cap = BUDGET * 0.50
                qty = int(cost_cap / entry)
            else:
                atr = float(df.loc[d, "atr_pct"])
                sl_pct = min(max(2 * atr, 3.0), 10.0)
                sl = entry * (1 - sl_pct / 100)
                if tp_mult_r:
                    tp = entry * (1 + tp_mult_r * sl_pct / 100)
                else:
                    tp = entry * (1 + max(10.0, 1.5 * sl_pct) / 100)
                risk_eur = BUDGET * risk_pct / 100
                qty = int(risk_eur / (entry - sl))
                cost_cap = BUDGET * max_cost_pct / 100
                if qty * entry > cost_cap:
                    qty = int(cost_cap / entry)
            if qty < 1:
                continue
            positions.append({"ticker": t, "entry": entry, "sl": sl, "tp": tp,
                              "qty": qty, "entry_date": e_day, "be_done": False})

    # Clôture des positions restantes au dernier cours
    for p in positions:
        df = ind[p["ticker"]]
        last = float(df["close"].iloc[-1])
        pnl = (last - p["entry"]) * p["qty"] - 2 * fee
        closed.append({"ticker": p["ticker"], "pnl": pnl, "why": "OPEN",
                       "entry_date": p["entry_date"], "exit_date": df.index[-1],
                       "ret_pct": (last / p["entry"] - 1) * 100})
        equity += pnl

    if not closed:
        return {"trades": 0}
    pnls = [c["pnl"] for c in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    # Max drawdown sur la courbe d'équité trade par trade
    curve = np.cumsum([0] + sorted_by_exit(closed))
    peak = np.maximum.accumulate(curve + BUDGET)
    dd = float(((curve + BUDGET) - peak).min())
    return {
        "trades": len(closed),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "total_pnl": round(sum(pnls), 2),
        "avg_win": round(np.mean(wins), 2) if wins else 0,
        "avg_loss": round(np.mean(losses), 2) if losses else 0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "max_dd_eur": round(dd, 2),
        "final_equity": round(equity, 2),
        "exits": pd.Series([c["why"] for c in closed]).value_counts().to_dict(),
        "closed": closed,
    }


def sorted_by_exit(closed):
    return [c["pnl"] for c in sorted(closed, key=lambda c: c["exit_date"])]


# ── Validation : bootstrap + walk-forward ─────────────────────────────────────

def bootstrap_metrics(closed: list[dict], n_boot: int = 2000, seed: int = 42) -> dict | None:
    """Intervalle de confiance par rééchantillonnage (bootstrap) des trades.

    Rééchantillonne AVEC REMISE la séquence de trades n_boot fois. Comme le
    max drawdown dépend de l'ordre, on recalcule la courbe d'équité sur chaque
    tirage → l'IC90% capture la dispersion réelle de la stratégie, pas un
    point unique qui peut être un coup de chance. Si l'IC du P&L total inclut
    0 (ou P(gagnante) proche de 50%), l'edge n'est pas distinguable du bruit.
    """
    pnls = np.array([c["pnl"] for c in closed], dtype=float)
    n = len(pnls)
    if n == 0:
        return None
    rng = np.random.default_rng(seed)
    tot = np.empty(n_boot)
    pf = np.empty(n_boot)
    dd = np.empty(n_boot)
    for b in range(n_boot):
        sample = pnls[rng.integers(0, n, n)]
        tot[b] = sample.sum()
        wins = sample[sample > 0].sum()
        loss = sample[sample <= 0].sum()
        pf[b] = wins / abs(loss) if loss < 0 else np.inf
        equity = np.concatenate([[BUDGET], BUDGET + np.cumsum(sample)])
        dd[b] = (equity - np.maximum.accumulate(equity)).min()

    def ci(a: np.ndarray) -> tuple[float, float, float]:
        return (float(np.percentile(a, 5)),
                float(np.percentile(a, 50)),
                float(np.percentile(a, 95)))

    pf_finite = pf[np.isfinite(pf)]
    return {
        "n_trades": n,
        "total_pnl_ci": ci(tot),
        "profit_factor_ci": ci(pf_finite) if len(pf_finite) else None,
        "max_dd_ci": ci(dd),
        "p_profitable": round(float((tot > 0).mean()) * 100, 1),
    }


def walk_forward(ind: dict, dates: pd.DatetimeIndex, regime_ok: pd.Series,
                 cfg: dict, n_folds: int = 4) -> list[tuple[str, dict]]:
    """Découpe la période en n_folds fenêtres consécutives et simule chacune
    indépendamment (positions clôturées en fin de fenêtre). Un edge robuste
    reste positif hors échantillon dans la majorité des fenêtres ; s'il ne
    tient que grâce à un seul régime, le walk-forward le révèle."""
    out = []
    for fold in np.array_split(dates, n_folds):
        fd = pd.DatetimeIndex(fold)
        if len(fd) == 0:
            continue
        r = simulate(ind, fd, regime_ok, **cfg)
        out.append((f"{fd[0].date()} → {fd[-1].date()}", r))
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01", help="début de la simulation")
    ap.add_argument("--fast", action="store_true", help="univers réduit (30 tickers)")
    ap.add_argument("--no-validate", action="store_true",
                    help="saute le bloc bootstrap + walk-forward")
    ap.add_argument("--boot", type=int, default=2000,
                    help="nombre de rééchantillonnages bootstrap")
    ap.add_argument("--folds", type=int, default=4,
                    help="nombre de fenêtres walk-forward")
    args = ap.parse_args()

    universe = SCAN_UNIVERSE[:30] if args.fast else SCAN_UNIVERSE
    dl_start = (pd.Timestamp(args.start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")

    print(f"Téléchargement {len(universe)} tickers depuis {dl_start}…", flush=True)
    raw = yf.download(universe, start=dl_start, group_by="ticker",
                      auto_adjust=True, threads=True, progress=False)
    data = {}
    for t in universe:
        try:
            df = raw[t].dropna(subset=["Close"])
            if len(df) > 0:
                data[t] = df
        except Exception:
            continue
    print(f"{len(data)} tickers avec données.", flush=True)

    ind = build_indicators(data)
    print(f"{len(ind)} tickers avec historique suffisant (≥ 260 jours).", flush=True)

    # Régime : entrées permises si CAC OU SPY au-dessus de leur MM200 (les deux
    # sous la MM200 = CORRECTION → pas de nouvelles entrées).
    idx = yf.download(["^FCHI", "^GSPC"], start=dl_start, group_by="ticker",
                      auto_adjust=True, progress=False)
    above = []
    for it in ["^FCHI", "^GSPC"]:
        c = idx[it]["Close"].dropna()
        above.append((c > c.rolling(200).mean()).astype(int))
    regime_ok = (pd.concat(above, axis=1).ffill().sum(axis=1) >= 1)

    all_dates = sorted(set().union(*[set(df.index) for df in ind.values()]))
    dates = pd.DatetimeIndex([d for d in all_dates if d >= pd.Timestamp(args.start, tz=d.tz)])
    regime_ok = regime_ok.reindex(pd.DatetimeIndex(all_dates).tz_localize(None)).ffill()
    regime_ok.index = pd.DatetimeIndex(all_dates)

    configs = [
        ("A. OLD (mom 1M, SL7/TP10, 50% budget)", dict(mode="old", max_pos=2)),
        ("B. PHASE1 (12-1, risque 1%, max 2 pos, cout<=30%)",
         dict(mode="new", risk_pct=1.0, max_pos=2, max_cost_pct=30)),
        ("C. RECOVERY (12-1, risque 2%, max 3 pos, cout<=40%)",
         dict(mode="new", risk_pct=2.0, max_pos=3, max_cost_pct=40)),
        ("B2. PHASE1 sans trailing breakeven",
         dict(mode="new", risk_pct=1.0, max_pos=2, max_cost_pct=30, be_trail=False)),
        ("C2. RECOVERY sans trailing breakeven",
         dict(mode="new", risk_pct=2.0, max_pos=3, max_cost_pct=40, be_trail=False)),
        ("C3. RECOVERY sans trail, TP 2.5R",
         dict(mode="new", risk_pct=2.0, max_pos=3, max_cost_pct=40,
              be_trail=False, tp_mult_r=2.5)),
    ]
    print(f"\nSimulation {dates[0].date()} → {dates[-1].date()} "
          f"(budget {BUDGET:.0f}€, frais {FEE}€/ordre)\n" + "=" * 74)
    results = {}
    for name, cfg in configs:
        r = simulate(ind, dates, regime_ok, **cfg)
        results[name] = (cfg, r)
        print(f"\n{name}")
        if r["trades"] == 0:
            print("  aucun trade")
            continue
        print(f"  trades: {r['trades']} | win rate: {r['win_rate']}% | "
              f"profit factor: {r['profit_factor']}")
        print(f"  P&L total: {r['total_pnl']:+.0f}€ | équité finale: {r['final_equity']:.0f}€ "
              f"({(r['final_equity'] / BUDGET - 1) * 100:+.1f}%)")
        print(f"  gain moyen: {r['avg_win']:+.0f}€ | perte moyenne: {r['avg_loss']:+.0f}€ | "
              f"max drawdown: {r['max_dd_eur']:.0f}€")
        print(f"  sorties: {r['exits']}")

    if args.no_validate:
        return

    # ── Validation des stratégies livrées (PHASE1 + RECOVERY) ─────────────────
    print("\n" + "=" * 74)
    print(f"VALIDATION — bootstrap {args.boot}× + walk-forward {args.folds} fenêtres")
    print("(un point unique peut être un coup de chance ; ici on mesure la dispersion)")
    print("=" * 74)
    for name, (cfg, r) in results.items():
        if not (name.startswith("B. PHASE1") or name.startswith("C. RECOVERY")):
            continue
        print(f"\n{name}")
        if r["trades"] == 0:
            print("  aucun trade — rien à valider")
            continue
        bs = bootstrap_metrics(r["closed"], n_boot=args.boot)
        lo, mid, hi = bs["total_pnl_ci"]
        print(f"  bootstrap ({bs['n_trades']} trades rééchantillonnés) :")
        print(f"    P&L total      IC90% [{lo:+7.0f}€ … {hi:+7.0f}€]  médiane {mid:+.0f}€")
        if bs["profit_factor_ci"]:
            lo, mid, hi = bs["profit_factor_ci"]
            print(f"    profit factor  IC90% [{lo:7.2f}  … {hi:7.2f} ]  médiane {mid:.2f}")
        lo, mid, hi = bs["max_dd_ci"]
        print(f"    max drawdown   IC90% [{lo:7.0f}€ … {hi:7.0f}€]  médiane {mid:.0f}€")
        verdict = "edge réel" if bs["p_profitable"] >= 90 else \
                  "fragile" if bs["p_profitable"] >= 75 else "indistinct du bruit"
        print(f"    P(stratégie gagnante) = {bs['p_profitable']}%  → {verdict}")
        print(f"  walk-forward (hors échantillon, {args.folds} fenêtres) :")
        pos_folds = 0
        n_folds_traded = 0
        for label, fr in walk_forward(ind, dates, regime_ok, cfg, args.folds):
            if fr["trades"] == 0:
                print(f"    {label} : aucun trade")
                continue
            n_folds_traded += 1
            if fr["total_pnl"] > 0:
                pos_folds += 1
            print(f"    {label} : {fr['trades']:2d} trades | PF {str(fr['profit_factor']):>4} | "
                  f"P&L {fr['total_pnl']:+6.0f}€ | DD {fr['max_dd_eur']:6.0f}€")
        if n_folds_traded:
            print(f"    → {pos_folds}/{n_folds_traded} fenêtres actives gagnantes")


if __name__ == "__main__":
    sys.exit(main())
