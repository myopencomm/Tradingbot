"""
Dashboard local : http://localhost:8642  (PWA installable sur iOS)
Page HTML autonome régénérée à chaque visite (données toujours fraîches) :
  - cartes de synthèse (P&L réalisé/latent, win rate, profit factor, cash,
    €/jour, ROI sur cash engagé)
  - courbe du P&L cumulé sur axe temporel (taille des points ∝ cash engagé)
  - P&L par trade (un emplacement par trade : nom / date / cash engagé)
  - tableau des trades filtrable (texte, WIN/LOSS)
  - positions ouvertes avec P&L latent live
PWA : manifest + icônes servis par le même serveur — "Ajouter à l'écran
d'accueil" sur iOS donne une app plein écran.
La commande Telegram /dashboard envoie le graphique en image (matplotlib).
Accès distant : DASHBOARD_BIND=0.0.0.0 dans .env (tailnet/LAN) ou
`tailscale serve --bg 8642`.
"""
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import portfolio
import prices
import stats

PORT = 8642


# ─── Données ─────────────────────────────────────────────────────────────────

def _date_iso(d: str) -> str:
    """Normalise les dates de trades : '2026-05' (mois seul) → '2026-05-01'."""
    d = (d or "").strip()
    if len(d) == 7:   # YYYY-MM
        return d + "-01"
    return d or "1970-01-01"


def build_data() -> dict:
    """Assemble trades clôturés, P&L cumulé, stats et positions ouvertes."""
    from datetime import datetime as _dt
    trades = stats.load_history().get("closed_trades", [])
    cum, total = [], 0.0
    for t in trades:
        total += t.get("pnl", 0)
        # Cash engagé sur CE deal (qté × entrée), converti en EUR au taux
        # actuel pour les titres en devise (approximation : taux historique
        # non stocké).
        ticker = t.get("ticker", "")
        fx = prices.fx_to_eur("USD") if "." not in ticker else 1.0
        invested = round((t.get("entry_price") or 0) * t.get("qty", 0) * fx, 2)
        roi = round(t.get("pnl", 0) / invested * 100, 2) if invested else 0
        cum.append({
            "date":     t.get("date", ""),
            "date_iso": _date_iso(t.get("date", "")),
            "name":   t.get("name", "?"),
            "ticker": ticker,
            "qty":    t.get("qty", 0),
            "entry":  t.get("entry_price"),
            "exit":   t.get("exit_price"),
            "pnl":    round(t.get("pnl", 0), 2),
            "cum":    round(total, 2),
            "invested": invested,
            "roi":      roi,
            "result": t.get("result", ""),
        })

    # Durée de la performance : du premier trade à aujourd'hui
    span_days, since, per_day = 0, "", 0.0
    avg_invested, avg_roi = 0.0, 0.0
    if cum:
        first = _dt.fromisoformat(cum[0]["date_iso"])
        span_days = max(1, (_dt.now() - first).days)
        since = first.strftime("%d/%m/%Y")
        per_day = round(total / span_days, 2)
        total_invested = sum(t["invested"] for t in cum)
        avg_invested = round(total_invested / len(cum), 0)
        avg_roi = round(total / total_invested * 100, 2) if total_invested else 0

    s = stats.get_stats()

    open_pos = []
    data = portfolio.load()
    for name, cfg in data.get("positions", {}).items():
        q     = prices.get_quote(cfg["ticker"])
        price = q.get("price")
        cur   = q.get("currency") or "EUR"
        fx    = prices.fx_to_eur(cur)
        if price:
            pnl_eur = round((price - cfg["entry_price"]) * cfg["qty"] * fx, 2)
            chg     = round((price / cfg["entry_price"] - 1) * 100, 2)
        else:
            pnl_eur, chg = None, None
        open_pos.append({
            "name":   name,
            "ticker": cfg["ticker"],
            "qty":    cfg["qty"],
            "entry":  cfg["entry_price"],
            "price":  price,
            "chg":    chg,
            "pnl":    pnl_eur,
            "sl":     cfg.get("target_low"),
            "tp":     cfg.get("target_high"),
            "auto":   bool(cfg.get("autonomous")),
            "sym":    prices.currency_symbol(cur),
        })

    return {
        "trades":    cum,
        "stats":     s,
        "open":      open_pos,
        "cash":      data.get("cash_available", 0),
        "span_days": span_days,
        "since":     since,
        "per_day":   per_day,
        "avg_invested": avg_invested,
        "avg_roi":      avg_roi,
        "generated": __import__("datetime").datetime.now().strftime("%d/%m %H:%M"),
    }


# ─── Page HTML — PWA mobile-first (template à jetons @X@) ───────────────────

_HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>TradingBot</title>
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0e1117">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="TradingBot">
<link rel="apple-touch-icon" href="/icon-180.png">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #0e1117; color: #e6e9ef; margin: 0;
  padding: calc(14px + env(safe-area-inset-top)) 14px
           calc(24px + env(safe-area-inset-bottom)); max-width: 1100px;
  margin-inline: auto; }
header { display: flex; align-items: baseline; justify-content: space-between;
  margin-bottom: 14px; }
h1 { font-size: 1.15em; margin: 0; }
.muted { color: #5c6675; font-size: .75em; }
h2 { font-size: .8em; color: #8b96a5; text-transform: uppercase;
  letter-spacing: .08em; margin: 26px 0 10px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px; }
.card { background: linear-gradient(160deg, #1a1f2a, #151923);
  border: 1px solid #232a37; border-radius: 14px; padding: 13px 15px; }
.card .v { font-size: 1.3em; font-weight: 700; letter-spacing: -.02em; }
.card .l { color: #7c8695; font-size: .72em; margin-top: 3px; line-height: 1.35; }
.green { color: #3fd583; } .red { color: #ff7070; }
.chartbox { background: #151923; border: 1px solid #232a37; border-radius: 14px;
  padding: 12px; height: 300px; }
.tablewrap { overflow-x: auto; -webkit-overflow-scrolling: touch;
  background: #151923; border: 1px solid #232a37; border-radius: 14px; }
table { border-collapse: collapse; width: 100%; font-size: .84em; }
th, td { padding: 9px 12px; text-align: right; border-bottom: 1px solid #212836;
  white-space: nowrap; }
tr:last-child td { border-bottom: none; }
th { color: #7c8695; font-weight: 600; font-size: .78em; text-transform: uppercase;
  letter-spacing: .04em; }
td:first-child, th:first-child { text-align: left; }
.filters { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
input, button { background: #151923; color: #e6e9ef; border: 1px solid #232a37;
  border-radius: 10px; padding: 9px 14px; font-size: .9em; }
input { flex: 1; min-width: 130px; }
button.on { border-color: #3fd583; color: #3fd583; }
.badge { font-size: .68em; padding: 2px 7px; border-radius: 6px;
  background: #1f3a5c; color: #8db8ec; vertical-align: middle; }
@media (max-width: 540px) {
  .chartbox { height: 235px; padding: 8px; }
  .card .v { font-size: 1.15em; }
  .m-hide { display: none; }
  th, td { padding: 8px 9px; }
}
</style></head><body>
<header><h1>🤖 TradingBot</h1><span class="muted">@GENERATED@</span></header>

<div class="cards">
  <div class="card"><div class="v @PNL_CLS@">@REALIZED@€</div><div class="l">P&L réalisé (@NB@ trades)</div></div>
  <div class="card"><div class="v @UPNL_CLS@">@UNREALIZED@€</div><div class="l">P&L latent (positions)</div></div>
  <div class="card"><div class="v @TPNL_CLS@">@TOTAL@€</div><div class="l">P&L total</div></div>
  <div class="card"><div class="v">@WIN_RATE@%</div><div class="l">Win rate (@WINS@W / @LOSSES@L)</div></div>
  <div class="card"><div class="v">@PF@</div><div class="l">Profit factor</div></div>
  <div class="card"><div class="v">@CASH@€</div><div class="l">Cash disponible</div></div>
  <div class="card"><div class="v @PNL_CLS@">@PER_DAY@€/j</div><div class="l">depuis le @SINCE@ (@SPAN@ jours)</div></div>
  <div class="card"><div class="v @ROI_CLS@">@AVG_ROI@%</div><div class="l">ROI / cash engagé (moy. @AVG_INV@€/deal)</div></div>
</div>

<h2>P&L cumulé <span class="muted">— taille du point ◉ = cash engagé</span></h2>
<div class="chartbox"><canvas id="cum"></canvas></div>

<h2>P&L par trade <span class="muted">— nom / date / cash engagé</span></h2>
<div class="chartbox"><canvas id="bars"></canvas></div>

<h2>Trades clôturés</h2>
<div class="filters">
  <input id="q" placeholder="Filtrer…" oninput="renderT()">
  <button id="fAll" class="on" onclick="setF('all')">Tous</button>
  <button id="fWin" onclick="setF('win')">WIN</button>
  <button id="fLoss" onclick="setF('loss')">LOSS</button>
</div>
<div class="tablewrap">
<table id="tt"><thead><tr><th>Nom</th><th class="m-hide">Ticker</th><th>Date</th>
<th class="m-hide">Qté</th><th class="m-hide">Entrée</th><th class="m-hide">Sortie</th>
<th>Investi</th><th>P&L</th><th>ROI</th><th>Cumul</th></tr></thead><tbody></tbody></table>
</div>

<h2>Positions ouvertes</h2>
<div class="tablewrap">
<table><thead><tr><th>Nom</th><th class="m-hide">Qté</th><th class="m-hide">PRU</th>
<th>Cours</th><th>Var</th><th>P&L</th><th>SL</th><th>TP</th></tr></thead>
<tbody>@OPEN_ROWS@</tbody></table>
</div>

<script>
const D = @DATA_JSON@;
let filt = 'all';
function setF(f) {
  filt = f;
  for (const [id, v] of [['fAll','all'],['fWin','win'],['fLoss','loss']])
    document.getElementById(id).classList.toggle('on', v === f);
  renderT();
}
function renderT() {
  const q = document.getElementById('q').value.toLowerCase();
  const tb = document.querySelector('#tt tbody');
  tb.innerHTML = '';
  for (const t of [...D.trades].reverse()) {
    if (filt !== 'all' && t.result !== filt) continue;
    if (q && !(t.name + t.ticker + t.date).toLowerCase().includes(q)) continue;
    const c = t.pnl >= 0 ? 'green' : 'red';
    tb.innerHTML += `<tr><td>${t.name}</td><td class="m-hide">${t.ticker}</td>
      <td>${t.date}</td><td class="m-hide">${t.qty}</td>
      <td class="m-hide">${t.entry}</td><td class="m-hide">${t.exit}</td>
      <td>${t.invested}€</td>
      <td class="${c}">${t.pnl >= 0 ? '+' : ''}${t.pnl}€</td>
      <td class="${c}">${t.roi >= 0 ? '+' : ''}${t.roi}%</td><td>${t.cum}€</td></tr>`;
  }
}
renderT();

const timeScale = {
  type: 'time',
  time: { unit: 'day', tooltipFormat: 'dd/MM/yyyy',
          displayFormats: { day: 'dd/MM', week: 'dd/MM', month: 'MM/yyyy' } },
  ticks: { maxRotation: 0 }
};
const tt = { callbacks: { label: c => {
  const t = D.trades[c.dataIndex];
  return `${t.name} : ${t.pnl >= 0 ? '+' : ''}${t.pnl}€ sur ${t.invested}€ engagés `
       + `(ROI ${t.roi >= 0 ? '+' : ''}${t.roi}%) — cumul ${t.cum}€`;
} } };
// Taille du point ∝ cash engagé sur le deal (min 4px, max 14px)
const invs = D.trades.map(t => t.invested);
const iMin = Math.min(...invs), iMax = Math.max(...invs);
const radius = i => 4 + (iMax > iMin ? 10 * (invs[i] - iMin) / (iMax - iMin) : 4);
new Chart(document.getElementById('cum'), {
  type: 'line',
  data: { datasets: [{ label: 'P&L cumulé (€)',
    data: D.trades.map(t => ({ x: t.date_iso, y: t.cum })),
    borderColor: '#3fd583', backgroundColor: 'rgba(63,213,131,.12)',
    fill: true, tension: .25,
    pointRadius: c => radius(c.dataIndex),
    pointHoverRadius: c => radius(c.dataIndex) + 3,
    pointBackgroundColor: '#3fd583',
    pointBorderColor: '#7a9bd4', pointBorderWidth: 2 }] },
  options: { maintainAspectRatio: false, scales: { x: timeScale },
             plugins: { legend: { display: false }, tooltip: tt } }
});
// Un emplacement par trade (espacement régulier) : lisible même quand
// plusieurs trades tombent le même jour.
new Chart(document.getElementById('bars'), {
  type: 'bar',
  data: {
    labels: D.trades.map(t => [t.name,
      t.date_iso.slice(8,10) + '/' + t.date_iso.slice(5,7), t.invested + '€']),
    datasets: [{ data: D.trades.map(t => t.pnl),
      backgroundColor: D.trades.map(t => t.pnl >= 0 ? '#3fd583' : '#ff7070') }]
  },
  options: { maintainAspectRatio: false,
             plugins: { legend: { display: false }, tooltip: tt } }
});
</script></body></html>"""

_MANIFEST = {
    "name": "TradingBot",
    "short_name": "TradingBot",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0e1117",
    "theme_color": "#0e1117",
    "icons": [
        {"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}


def render_html() -> str:
    d = build_data()
    s = d["stats"]

    def cls(v):
        return "green" if v >= 0 else "red"

    def signed(v):
        return f"{v:+.2f}"

    rows = []
    for p in d["open"]:
        sym = p["sym"]
        if p["price"] is not None:
            var = f'<td class="{cls(p["chg"])}">{p["chg"]:+.2f}%</td>'
            pnl = f'<td class="{cls(p["pnl"])}">{p["pnl"]:+.0f}€</td>'
            price = f"{p['price']}{sym}"
        else:
            var, pnl, price = "<td>—</td>", "<td>⛔</td>", "—"
        tag = ' <span class="badge">auto</span>' if p["auto"] else ""
        rows.append(
            f"<tr><td>{p['name']}{tag}</td><td class='m-hide'>{p['qty']}</td>"
            f"<td class='m-hide'>{p['entry']}{sym}</td><td>{price}</td>{var}{pnl}"
            f"<td>{p['sl']}{sym}</td><td>{p['tp']}{sym}</td></tr>"
        )

    repl = {
        "@GENERATED@":  d["generated"],
        "@REALIZED@":   signed(s["realized_pnl"]),
        "@PNL_CLS@":    cls(s["realized_pnl"]),
        "@UNREALIZED@": signed(s["unrealized_pnl"]),
        "@UPNL_CLS@":   cls(s["unrealized_pnl"]),
        "@TOTAL@":      signed(s["total_pnl"]),
        "@TPNL_CLS@":   cls(s["total_pnl"]),
        "@NB@":         str(s["nb_closed"]),
        "@WIN_RATE@":   str(s["win_rate"]),
        "@WINS@":       str(s["nb_wins"]),
        "@LOSSES@":     str(s["nb_losses"]),
        "@PF@":         str(s["profit_factor"]) if s["profit_factor"] is not None else "—",
        "@CASH@":       f"{d['cash']:.2f}",
        "@PER_DAY@":    signed(d["per_day"]),
        "@SINCE@":      d["since"] or "—",
        "@SPAN@":       str(d["span_days"]),
        "@AVG_ROI@":    signed(d["avg_roi"]),
        "@ROI_CLS@":    cls(d["avg_roi"]),
        "@AVG_INV@":    f"{d['avg_invested']:.0f}",
        "@OPEN_ROWS@":  "".join(rows),
        "@DATA_JSON@":  json.dumps(d, ensure_ascii=False),
    }
    html = _HTML
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


# ─── Icône PWA (générée une fois, en mémoire) ────────────────────────────────

_icon_cache: dict[int, bytes] = {}


def _icon_png(size: int = 180) -> bytes:
    """Icône app : courbe verte ascendante sur fond sombre (style dashboard)."""
    if size in _icon_cache:
        return _icon_cache[size]
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(size / 100, size / 100), dpi=100)
    fig.patch.set_facecolor("#0e1117")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("#0e1117")
    ax.axis("off")
    xs = [0.12, 0.34, 0.52, 0.70, 0.88]
    ys = [0.22, 0.46, 0.38, 0.64, 0.82]
    ax.plot(xs, ys, color="#3fd583", linewidth=size / 13, solid_capstyle="round",
            solid_joinstyle="round")
    ax.scatter([xs[-1]], [ys[-1]], s=(size / 5.5) ** 2, color="#3fd583",
               edgecolor="#7a9bd4", linewidth=size / 40, zorder=3)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    _icon_cache[size] = buf.getvalue()
    return _icon_cache[size]


# ─── Image matplotlib pour Telegram ──────────────────────────────────────────

def render_png() -> bytes | None:
    """Courbe P&L cumulé + barres par trade, en une image sombre pour mobile."""
    d = build_data()
    trades = d["trades"]
    if not trades:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from datetime import datetime as _dt

    dates = [_dt.fromisoformat(t["date_iso"]) for t in trades]
    cum   = [t["cum"] for t in trades]
    pnl   = [t["pnl"] for t in trades]
    invested = [t["invested"] for t in trades]

    plt.style.use("dark_background")
    # PAS de sharex : le haut est en TEMPS réel (trajectoire), le bas en
    # UN EMPLACEMENT PAR TRADE (détails lisibles même si les dates se touchent).
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7),
                                   gridspec_kw={"height_ratios": [2, 1]})
    ax1.plot(dates, cum, color="#4cd97b", linewidth=2, zorder=2)
    ax1.fill_between(dates, cum, alpha=0.12, color="#4cd97b", zorder=1)
    # Taille du point ∝ cash engagé — les détails chiffrés sont dans le
    # graphique du bas (aucun texte ici : les trades rapprochés restent nets)
    i_min, i_max = min(invested), max(invested)
    sizes = [40 + (200 * (v - i_min) / (i_max - i_min) if i_max > i_min else 40)
             for v in invested]
    ax1.scatter(dates, cum, s=sizes, color="#4cd97b",
                edgecolor="#7a9bd4", linewidth=1.5, zorder=3)
    ax1.axhline(0, color="#556", linewidth=0.8)
    ax1.set_title(f"P&L cumulé : {cum[-1]:+.2f}€ en {d['span_days']} jours "
                  f"(≈{d['per_day']:+.2f}€/j) — ROI {d['avg_roi']:+.2f}% "
                  f"sur ~{d['avg_invested']:.0f}€/deal, win rate {d['stats']['win_rate']}%",
                  fontsize=10)
    ax1.set_ylabel("€")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax1.xaxis.set_major_locator(mdates.AutoDateLocator())

    # Bas : un emplacement par trade (espacement régulier), toutes les infos
    # en étiquette — jamais de chevauchement même à dates identiques.
    xs = range(len(trades))
    ax2.bar(xs, pnl, width=0.6,
            color=["#4cd97b" if v >= 0 else "#ff6b6b" for v in pnl])
    for i, (v, inv) in enumerate(zip(pnl, invested)):
        ax2.annotate(f"{v:+.0f}€", (i, v), textcoords="offset points",
                     xytext=(0, 4 if v >= 0 else -11), fontsize=7,
                     ha="center", color="#dde")
    ax2.axhline(0, color="#556", linewidth=0.8)
    ax2.set_ylabel("€ / trade")
    ax2.set_xticks(list(xs))
    ax2.set_xticklabels(
        [f"{t['name']}\n{t['date_iso'][8:10]}/{t['date_iso'][5:7]}\n{t['invested']:.0f}€"
         for t in trades], fontsize=6.5)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


def summary_text() -> str:
    """Résumé texte compact pour la légende Telegram."""
    s = stats.get_stats()
    d = build_data()
    cash = portfolio.get_cash()
    pf   = s["profit_factor"] if s["profit_factor"] is not None else "—"
    return (
        f"📊 DASHBOARD\n"
        f"P&L réalisé : {s['realized_pnl']:+.2f}€ ({s['nb_closed']} trades "
        f"en {d['span_days']}j ≈ {d['per_day']:+.2f}€/j)\n"
        f"P&L latent : {s['unrealized_pnl']:+.2f}€ | Total : {s['total_pnl']:+.2f}€\n"
        f"Win rate : {s['win_rate']}% ({s['nb_wins']}W/{s['nb_losses']}L) | "
        f"Profit factor : {pf}\n"
        f"Cash : {cash:.2f}€\n\n"
        f"Version complète (filtres, tableau) : http://localhost:{PORT} sur le Mac"
    )


# ─── Serveur local ───────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            if path == "/manifest.webmanifest":
                body  = json.dumps(_MANIFEST).encode("utf-8")
                ctype = "application/manifest+json"
            elif path in ("/icon-180.png", "/icon-512.png"):
                body  = _icon_png(512 if "512" in path else 180)
                ctype = "image/png"
            else:
                body  = render_html().encode("utf-8")
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Erreur dashboard : {e}".encode())

    def log_message(self, *args):
        pass  # pas de bruit dans tradingbot.log


def start_server():
    """
    Démarre le serveur dans un thread daemon.
    Bind par défaut : 127.0.0.1 (local uniquement). Pour un accès distant
    (Tailscale/LAN), DASHBOARD_BIND=0.0.0.0 dans .env — ATTENTION : aucune
    authentification, à réserver à un réseau de confiance (tailnet).
    L'alternative recommandée sans changer le bind : `tailscale serve --bg 8642`.
    """
    import os
    bind = os.getenv("DASHBOARD_BIND", "127.0.0.1").strip() or "127.0.0.1"
    try:
        srv = HTTPServer((bind, PORT), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True,
                         name="dashboard").start()
        scope = "local uniquement" if bind == "127.0.0.1" else f"bind {bind} — accessible réseau"
        print(f"✅ Dashboard : http://localhost:{PORT} ({scope})")
    except OSError as e:
        print(f"⚠️ Dashboard non démarré (port {PORT}) : {e}")
