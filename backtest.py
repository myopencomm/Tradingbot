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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01", help="début de la simulation")
    ap.add_argument("--fast", action="store_true", help="univers réduit (30 tickers)")
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
    for name, cfg in configs:
        r = simulate(ind, dates, regime_ok, **cfg)
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


if __name__ == "__main__":
    sys.exit(main())
