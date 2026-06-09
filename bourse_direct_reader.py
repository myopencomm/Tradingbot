"""
Lecture du portefeuille Bourse Direct via Playwright.

Page React : https://www.boursedirect.fr/fr/mon-compte/portefeuilles
- Sélecteur de compte : boutons #up / #down (carte PEA ↔ CTO)
- 3 onglets : "Mes positions", "Mes ordres", "Mes investissements programmés"

Sélecteurs (explorés en live) :
- Carte active   : .AccountCardSelector-module_card_Zt5-8
- Cash dispo     : [data-testid="portfolio-header_available-cash-value"]
- Position       : .position-row
    NOM | PLACE › TICKER | cours devise | var% | qté | 'PRU : X €' | ...
- Ordre          : .order-line (Order-module_orderContainer)
    NOM | PLACE › TICKER | ... | Sens(CPT) | exec/qty | validité | Type | Seuil X | Profit X | statut
"""
import time
import re

BD_PORTFOLIO_URL = "https://www.boursedirect.fr/fr/mon-compte/portefeuilles"


def _dismiss_popups(page):
    """Ferme les popups éventuelles (WelcomeModal, modals). Ignore si absentes."""
    selectors = [
        "button.WelcomeModal-module_backBtn_QnNhW",
        "#modal-ui-settings button[data-dismiss='modal']",
        "#tosModalClosable button[data-dismiss='modal']",
        "button.close-modal-tos-closable",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=1500)
                time.sleep(0.3)
        except Exception:
            pass


def _ensure_cto(page) -> bool:
    """Clique #down jusqu'à ce que la carte active affiche le CTO."""
    for _ in range(4):
        try:
            active = page.locator(
                ".AccountCardSelector-module_card_Zt5-8"
            ).first.inner_text(timeout=2000)
        except Exception:
            active = ""
        if "CTO" in active:
            return True
        try:
            page.locator("button#down").click(timeout=2000)
            time.sleep(1)
        except Exception:
            break
    try:
        return "CTO" in page.locator(
            ".AccountCardSelector-module_card_Zt5-8"
        ).first.inner_text(timeout=2000)
    except Exception:
        return False


def _click_tab(page, label: str):
    """Clique un onglet de la sidebar par son texte."""
    try:
        page.locator(f'a[role="button"]:has-text("{label}")').first.click(timeout=3000)
        time.sleep(1.5)
        return True
    except Exception:
        return False


def get_portfolio(page, send_fn=None) -> dict | None:
    """
    Lit le portefeuille CTO complet : cash + positions + ordres + invest. programmés.
    `page` fourni par playwright_session.run() (thread worker).
    Retourne dict ou None si échec.
    """
    def log(msg):
        print(f"[BD Reader] {msg}")
        if send_fn:
            send_fn(msg)

    try:
        page.goto(BD_PORTFOLIO_URL, wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)

        if "login" in page.url.lower():
            log("Session expirée — reconnecte avec /connect.")
            return None

        _dismiss_popups(page)

        if not _ensure_cto(page):
            log("Impossible de sélectionner le compte CTO.")
            return None
        time.sleep(1.5)

        # ── Cash ──────────────────────────────────────────────────────────────
        cash = None
        try:
            cash_txt = page.locator(
                '[data-testid="portfolio-header_available-cash-value"]'
            ).first.inner_text(timeout=4000)
            cash = _parse_float(cash_txt)
        except Exception as e:
            log(f"Lecture cash échouée : {e}")

        # ── Onglet Mes positions ────────────────────────────────────────────
        _click_tab(page, "Mes positions")
        _dismiss_popups(page)
        positions = []
        try:
            for row in page.locator(".position-row").all():
                parsed = _parse_position(row.inner_text(timeout=2000))
                if parsed:
                    positions.append(parsed)
        except Exception as e:
            log(f"Lecture positions échouée : {e}")

        # ── Onglet Mes ordres ────────────────────────────────────────────────
        orders = []
        if _click_tab(page, "Mes ordres"):
            _dismiss_popups(page)
            try:
                for row in page.locator(".order-line").all():
                    parsed = _parse_order(row.inner_text(timeout=2000))
                    if parsed:
                        orders.append(parsed)
            except Exception as e:
                log(f"Lecture ordres échouée : {e}")

        # ── Onglet Mes investissements programmés ────────────────────────────
        programmed = []
        if _click_tab(page, "Mes investissements programmés"):
            _dismiss_popups(page)
            try:
                body = page.locator("body").inner_text(timeout=3000)
                if "pas d’investissement programmé" not in body and \
                   "pas d'investissement programmé" not in body:
                    # Structure inconnue tant qu'il n'y en a pas — on capture le brut
                    for row in page.locator('[class*="ProgrammedInvestment"], [class*="programmed-row"]').all():
                        t = row.inner_text(timeout=2000).strip()
                        if t:
                            programmed.append(t.replace("\n", " "))
            except Exception as e:
                log(f"Lecture invest. programmés échouée : {e}")

        if cash is None and not positions:
            log("Aucune donnée lue (ni cash ni positions).")
            return None

        return {
            "cash":       cash,
            "positions":  positions,
            "orders":     orders,
            "programmed": programmed,
        }

    except Exception as e:
        log(f"Erreur : {e}")
        return None


def _parse_position(text: str) -> dict | None:
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if len(parts) < 5:
        return None

    name = bd_ticker = qty = pru = None
    for p in parts:
        if "›" in p:
            seg = p.split("›")
            if len(seg) == 2:
                bd_ticker = seg[1].strip()
        elif p.startswith("PRU"):
            m = re.search(r'PRU\s*:\s*([\d\s.,]+)', p)
            if m:
                pru = _parse_float(m.group(1))

    for p in parts:
        if "›" in p or p.startswith("PRU") or "€" in p or "%" in p:
            continue
        if re.search(r'[A-Za-zÀ-ÿ]{3,}', p) and not p.replace(".", "").isdigit():
            name = p
            break

    for i, p in enumerate(parts):
        clean = p.replace(" ", "")
        if clean.isdigit() and 0 < int(clean) < 100000:
            if i + 1 < len(parts) and parts[i + 1].startswith("PRU"):
                qty = int(clean)
                break

    if not name or not bd_ticker or qty is None:
        return None
    return {"name": name, "bd_ticker": bd_ticker, "qty": qty, "pru": pru}


def _parse_order(text: str) -> dict | None:
    """
    Parse une ligne .order-line.
    Ex : 'Vente(CPT) 0/17 - 31/12/2026 à 17:35:00 Take Profit Seuil57.20 € En cours Profit72.90 € En cours'
    """
    flat = text.replace("\n", " ").replace("\t", " ")
    flat = re.sub(r'\s+', ' ', flat).strip()
    if not flat or len(flat) < 5:
        return None

    order = {"raw": flat}

    # Sens
    m = re.search(r'(Achat|Vente)\s*\(([A-Z]+)\)', flat)
    if m:
        order["sens"] = m.group(1)
    # Type d'ordre
    for t in ("Take Profit", "Stop Loss", "Stop Suiveur", "A cours limité", "Au marché"):
        if t in flat:
            order["type"] = t
            break
    # Seuil (SL) et Profit (TP)
    m_seuil = re.search(r'Seuil\s*([\d\s.,]+)\s*€', flat)
    if m_seuil:
        order["seuil"] = _parse_float(m_seuil.group(1))
    m_profit = re.search(r'Profit\s*([\d\s.,]+)\s*€', flat)
    if m_profit:
        order["profit"] = _parse_float(m_profit.group(1))
    # Quantité exécutée / totale (ex: 0/17)
    m_qty = re.search(r'(\d+)\s*/\s*(\d+)', flat)
    if m_qty:
        order["qty_exec"] = int(m_qty.group(1))
        order["qty_total"] = int(m_qty.group(2))
    # Statut
    if "En cours" in flat:
        order["statut"] = "En cours"
    elif "Exécuté" in flat or "Execute" in flat:
        order["statut"] = "Exécuté"

    return order


def _parse_float(s: str) -> float | None:
    if not s:
        return None
    try:
        clean = re.sub(r'[€$£\s\xa0]', '', str(s))
        if ',' in clean and '.' in clean:
            clean = clean.replace(',', '')      # virgule = séparateur milliers
        elif ',' in clean:
            clean = clean.replace(',', '.')     # virgule = décimale (FR)
        return round(float(clean), 5)
    except Exception:
        return None
