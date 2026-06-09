"""
Lecture du portefeuille Bourse Direct via Playwright.

Page React : https://www.boursedirect.fr/fr/mon-compte/portefeuilles
- Sélecteur de compte : boutons #up / #down (carte PEA ↔ CTO)
- Header cash : [data-testid="portfolio-header_available-cash-value"]
- Lignes positions : .position-row  (texte tabulé multi-lignes)

Structure d'une ligne position (innerText, séparé par \n) :
  <icône> | NOM | PLACE › TICKER | COURS DEVISE | VAR% | QTÉ | PRU : X € | var% | VALO | +/-VAL | POIDS%
Ex : Exosens | XPAR › EXENS | 61.45 EUR | +0.41 % | 17 | PRU : 63.4223 € | -3.11 % | 1 044.65 € | -33.53 € | 13%
"""
import time
import re

BD_PORTFOLIO_URL = "https://www.boursedirect.fr/fr/mon-compte/portefeuilles"


def get_portfolio(page, send_fn=None) -> dict | None:
    """
    Lit le portefeuille CTO depuis Bourse Direct.
    `page` fourni par playwright_session.run() (thread worker).
    Retourne {"cash": float, "positions": [...]} ou None si échec.
    """
    def log(msg):
        print(f"[BD Reader] {msg}")
        if send_fn:
            send_fn(msg)

    try:
        page.goto(BD_PORTFOLIO_URL, wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)  # React charge les données en AJAX

        if "login" in page.url.lower():
            log("Session expirée — reconnecte avec /connect.")
            return None

        # Ferme la popup de bienvenue si présente
        try:
            modal = page.locator("button.WelcomeModal-module_backBtn_QnNhW")
            if modal.count() > 0:
                modal.click()
                time.sleep(0.5)
        except Exception:
            pass

        # ── Bascule sur le CTO ────────────────────────────────────────────────
        # La carte affichée en premier change avec les flèches #up/#down.
        # On clique #down jusqu'à voir "CTO" dans la carte active (max 3 essais).
        cto_selected = _ensure_cto(page, log)
        if not cto_selected:
            log("Impossible de sélectionner le compte CTO.")
            return None
        time.sleep(1.5)  # rechargement des données après switch

        # ── Cash ──────────────────────────────────────────────────────────────
        cash = None
        try:
            cash_txt = page.locator(
                '[data-testid="portfolio-header_available-cash-value"]'
            ).first.inner_text(timeout=4000)
            cash = _parse_float(cash_txt)
        except Exception as e:
            log(f"Lecture cash échouée : {e}")

        # ── Positions ──────────────────────────────────────────────────────────
        positions = []
        try:
            rows = page.locator(".position-row").all()
            for row in rows:
                txt = row.inner_text(timeout=2000)
                parsed = _parse_position(txt)
                if parsed:
                    positions.append(parsed)
        except Exception as e:
            log(f"Lecture positions échouée : {e}")

        if cash is None and not positions:
            log("Aucune donnée lue (ni cash ni positions).")
            return None

        return {"cash": cash, "positions": positions}

    except Exception as e:
        log(f"Erreur : {e}")
        return None


def _ensure_cto(page, log) -> bool:
    """Clique #down jusqu'à ce que la carte active affiche le CTO."""
    for _ in range(4):
        # La carte active est la première .card visible
        try:
            active_card = page.locator(
                ".AccountCardSelector-module_card_Zt5-8"
            ).first.inner_text(timeout=2000)
        except Exception:
            active_card = ""

        if "CTO" in active_card:
            return True

        # Pas le CTO → flèche suivant
        try:
            page.locator("button#down").click(timeout=2000)
            time.sleep(1)
        except Exception:
            break
    # Dernier check
    try:
        return "CTO" in page.locator(
            ".AccountCardSelector-module_card_Zt5-8"
        ).first.inner_text(timeout=2000)
    except Exception:
        return False


def _parse_position(text: str) -> dict | None:
    """
    Parse une ligne .position-row.
    Format innerText (lignes séparées par \\n) :
      NOM, PLACE › TICKER, COURS DEVISE, VAR%, QTÉ, 'PRU : X €', var%, VALO, +/-VAL, POIDS
    """
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if len(parts) < 5:
        return None

    name = None
    bd_ticker = None
    qty = None
    pru = None

    for p in parts:
        # Ticker : "XPAR › EXENS" ou "XNGS › ILMN"
        if "›" in p:
            seg = p.split("›")
            if len(seg) == 2:
                bd_ticker = seg[1].strip()
        # PRU : "PRU : 63.4223 €"
        elif p.startswith("PRU"):
            m = re.search(r'PRU\s*:\s*([\d\s.,]+)', p)
            if m:
                pru = _parse_float(m.group(1))

    # Nom : première partie alphabétique longue (pas une icône 1-2 lettres)
    for p in parts:
        if "›" in p or p.startswith("PRU") or "€" in p or "%" in p:
            continue
        if re.search(r'[A-Za-zÀ-ÿ]{3,}', p) and not p.replace(".", "").isdigit():
            name = p
            break

    # Quantité : un entier seul parmi les parts (entre var% et PRU)
    for i, p in enumerate(parts):
        clean = p.replace(" ", "")
        if clean.isdigit() and 0 < int(clean) < 100000:
            # Heuristique : la qté précède "PRU"
            if i + 1 < len(parts) and parts[i + 1].startswith("PRU"):
                qty = int(clean)
                break

    if not name or not bd_ticker or qty is None:
        return None

    return {
        "name":      name,
        "bd_ticker": bd_ticker,
        "qty":       qty,
        "pru":       pru,
    }


def _parse_float(s: str) -> float | None:
    """Convertit '1 050,60 €' ou '63.4223' ou '2 176.57 €' en float."""
    if not s:
        return None
    try:
        clean = re.sub(r'[€$£\s\xa0]', '', str(s))
        # Gère le format FR (virgule décimale) et US (point décimal)
        # Si virgule ET point : la virgule est séparateur de milliers (format US)
        if ',' in clean and '.' in clean:
            clean = clean.replace(',', '')
        elif ',' in clean:
            clean = clean.replace(',', '.')
        return round(float(clean), 5)
    except Exception:
        return None
