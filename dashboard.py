"""
Dashboard local : http://localhost:8642
Page HTML autonome régénérée à chaque visite (données toujours fraîches) :
  - cartes de synthèse (P&L réalisé/latent, win rate, profit factor, cash)
  - courbe du P&L cumulé + barres par trade (Chart.js via CDN)
  - tableau des trades filtrable (texte, WIN/LOSS)
  - positions ouvertes avec P&L latent live
La commande Telegram /dashboard envoie le graphique en image (matplotlib)
avec le résumé — même contenu, format mobile.
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

def build_data() -> dict:
    """Assemble trades clôturés, P&L cumulé, stats et positions ouvertes."""
    trades = stats.load_history().get("closed_trades", [])
    cum, total = [], 0.0
    for t in trades:
        total += t.get("pnl", 0)
        cum.append({
            "date":   t.get("date", ""),
            "name":   t.get("name", "?"),
            "ticker": t.get("ticker", ""),
            "qty":    t.get("qty", 0),
            "entry":  t.get("entry_price"),
            "exit":   t.get("exit_price"),
            "pnl":    round(t.get("pnl", 0), 2),
            "cum":    round(total, 2),
            "result": t.get("result", ""),
        })

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
        "generated": __import__("datetime").datetime.now().strftime("%d/%m/%Y %H:%M"),
    }


# ─── Page HTML (autonome, Chart.js CDN) ──────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TradingBot — Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: -apple-system, sans-serif; background:#12151c; color:#e8e8e8;
         margin:0; padding:20px; }}
  h1 {{ font-size:1.3em; }} h2 {{ font-size:1.05em; color:#9ab; margin-top:28px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; }}
  .card {{ background:#1b2029; border-radius:10px; padding:14px 18px; min-width:130px; }}
  .card .v {{ font-size:1.4em; font-weight:700; }} .card .l {{ color:#8899aa; font-size:.8em; }}
  .green {{ color:#4cd97b; }} .red {{ color:#ff6b6b; }}
  canvas {{ background:#1b2029; border-radius:10px; padding:10px; margin-top:10px; }}
  table {{ border-collapse:collapse; width:100%; margin-top:10px; font-size:.9em; }}
  th, td {{ padding:7px 10px; text-align:right; border-bottom:1px solid #2a3140; }}
  th {{ color:#8899aa; }} td:first-child, th:first-child {{ text-align:left; }}
  input, button {{ background:#1b2029; color:#e8e8e8; border:1px solid #2a3140;
                   border-radius:6px; padding:6px 12px; margin-right:6px; }}
  button.on {{ border-color:#4cd97b; color:#4cd97b; }}
  .muted {{ color:#667; font-size:.8em; }}
  .badge {{ font-size:.75em; padding:1px 6px; border-radius:4px; background:#28405a; }}
</style></head><body>
<h1>🤖 TradingBot — Dashboard <span class="muted">généré {generated}</span></h1>

<div class="cards">
  <div class="card"><div class="v {pnl_cls}">{realized:+.2f}€</div><div class="l">P&L réalisé ({nb} trades)</div></div>
  <div class="card"><div class="v {upnl_cls}">{unrealized:+.2f}€</div><div class="l">P&L latent (positions)</div></div>
  <div class="card"><div class="v {tpnl_cls}">{total:+.2f}€</div><div class="l">P&L total</div></div>
  <div class="card"><div class="v">{win_rate}%</div><div class="l">Win rate ({wins}W / {losses}L)</div></div>
  <div class="card"><div class="v">{pf}</div><div class="l">Profit factor</div></div>
  <div class="card"><div class="v">{cash:.2f}€</div><div class="l">Cash disponible</div></div>
</div>

<h2>P&L cumulé</h2><canvas id="cum" height="90"></canvas>
<h2>P&L par trade</h2><canvas id="bars" height="90"></canvas>

<h2>Trades clôturés</h2>
<div>
  <input id="q" placeholder="Filtrer (nom, ticker, date)…" oninput="renderT()">
  <button id="fAll" class="on" onclick="setF('all')">Tous</button>
  <button id="fWin" onclick="setF('win')">WIN</button>
  <button id="fLoss" onclick="setF('loss')">LOSS</button>
</div>
<table id="tt"><thead><tr><th>Nom</th><th>Ticker</th><th>Date</th><th>Qté</th>
<th>Entrée</th><th>Sortie</th><th>P&L</th><th>Cumul</th></tr></thead><tbody></tbody></table>

<h2>Positions ouvertes</h2>
<table><thead><tr><th>Nom</th><th>Qté</th><th>PRU</th><th>Cours</th><th>Var</th>
<th>P&L latent</th><th>SL</th><th>TP</th></tr></thead><tbody>{open_rows}</tbody></table>

<script>
const D = {data_json};
let filt = 'all';
function setF(f) {{
  filt = f;
  for (const [id, v] of [['fAll','all'],['fWin','win'],['fLoss','loss']])
    document.getElementById(id).classList.toggle('on', v === f);
  renderT();
}}
function renderT() {{
  const q = document.getElementById('q').value.toLowerCase();
  const tb = document.querySelector('#tt tbody');
  tb.innerHTML = '';
  for (const t of [...D.trades].reverse()) {{
    if (filt !== 'all' && t.result !== filt) continue;
    if (q && !(t.name + t.ticker + t.date).toLowerCase().includes(q)) continue;
    const c = t.pnl >= 0 ? 'green' : 'red';
    tb.innerHTML += `<tr><td>${{t.name}}</td><td>${{t.ticker}}</td><td>${{t.date}}</td>
      <td>${{t.qty}}</td><td>${{t.entry}}</td><td>${{t.exit}}</td>
      <td class="${{c}}">${{t.pnl >= 0 ? '+' : ''}}${{t.pnl}}€</td><td>${{t.cum}}€</td></tr>`;
  }}
}}
renderT();
const labels = D.trades.map(t => t.date + ' ' + t.name);
new Chart(document.getElementById('cum'), {{
  type: 'line',
  data: {{ labels, datasets: [{{ label: 'P&L cumulé (€)', data: D.trades.map(t => t.cum),
    borderColor: '#4cd97b', backgroundColor: 'rgba(76,217,123,.12)', fill: true, tension: .25 }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }} }}
}});
new Chart(document.getElementById('bars'), {{
  type: 'bar',
  data: {{ labels, datasets: [{{ data: D.trades.map(t => t.pnl),
    backgroundColor: D.trades.map(t => t.pnl >= 0 ? '#4cd97b' : '#ff6b6b') }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }} }}
}});
</script></body></html>"""


def render_html() -> str:
    d = build_data()
    s = d["stats"]

    def cls(v):
        return "green" if v >= 0 else "red"

    rows = []
    for p in d["open"]:
        sym = p["sym"]
        if p["price"] is not None:
            var = f'<td class="{cls(p["chg"])}">{p["chg"]:+.2f}%</td>'
            pnl = f'<td class="{cls(p["pnl"])}">{p["pnl"]:+.0f}€</td>'
            price = f"{p['price']}{sym}"
        else:
            var, pnl, price = "<td>—</td>", "<td>⛔ suspendu</td>", "—"
        tag = ' <span class="badge">auto</span>' if p["auto"] else ""
        rows.append(
            f"<tr><td>{p['name']}{tag}</td><td>{p['qty']}</td>"
            f"<td>{p['entry']}{sym}</td><td>{price}</td>{var}{pnl}"
            f"<td>{p['sl']}{sym}</td><td>{p['tp']}{sym}</td></tr>"
        )

    return _HTML.format(
        generated=d["generated"],
        realized=s["realized_pnl"], pnl_cls=cls(s["realized_pnl"]),
        unrealized=s["unrealized_pnl"], upnl_cls=cls(s["unrealized_pnl"]),
        total=s["total_pnl"], tpnl_cls=cls(s["total_pnl"]),
        nb=s["nb_closed"], win_rate=s["win_rate"],
        wins=s["nb_wins"], losses=s["nb_losses"],
        pf=s["profit_factor"] if s["profit_factor"] is not None else "—",
        cash=d["cash"],
        open_rows="".join(rows),
        data_json=json.dumps(d, ensure_ascii=False),
    )


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

    labels = [f"{t['name']}\n{t['date'][5:]}" for t in trades]
    cum    = [t["cum"] for t in trades]
    pnl    = [t["pnl"] for t in trades]

    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})
    ax1.plot(range(len(cum)), cum, color="#4cd97b", linewidth=2, marker="o", markersize=4)
    ax1.fill_between(range(len(cum)), cum, alpha=0.12, color="#4cd97b")
    ax1.axhline(0, color="#556", linewidth=0.8)
    ax1.set_title(f"P&L cumulé : {cum[-1]:+.2f}€  ({len(trades)} trades, "
                  f"win rate {d['stats']['win_rate']}%)", fontsize=12)
    ax1.set_ylabel("€")
    ax2.bar(range(len(pnl)), pnl,
            color=["#4cd97b" if v >= 0 else "#ff6b6b" for v in pnl])
    ax2.axhline(0, color="#556", linewidth=0.8)
    ax2.set_ylabel("€ / trade")
    ax2.set_xticks(range(len(labels)))
    ax2.set_xticklabels(labels, fontsize=7)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


def summary_text() -> str:
    """Résumé texte compact pour la légende Telegram."""
    s = stats.get_stats()
    cash = portfolio.get_cash()
    pf   = s["profit_factor"] if s["profit_factor"] is not None else "—"
    return (
        f"📊 DASHBOARD\n"
        f"P&L réalisé : {s['realized_pnl']:+.2f}€ ({s['nb_closed']} trades)\n"
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
            body = render_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
    """Démarre le serveur local (127.0.0.1 uniquement) dans un thread daemon."""
    try:
        srv = HTTPServer(("127.0.0.1", PORT), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True,
                         name="dashboard").start()
        print(f"✅ Dashboard local : http://localhost:{PORT}")
    except OSError as e:
        print(f"⚠️ Dashboard non démarré (port {PORT}) : {e}")
