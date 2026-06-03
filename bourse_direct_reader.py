"""
Lecture du portefeuille et des données de compte Bourse Direct via Playwright.
Les URLs privées sont découvertes dynamiquement après connexion (nav inspection).
"""
import re
import time
import playwright_session as session

# URLs candidates pour le portefeuille — découvertes à la première utilisation
_portfolio_url: str | None = None

# Patterns d'URLs connus pour les pages de portefeuille BD
_PORTFOLIO_PATTERNS = [
    "/fr/compte/portefeuille",
    "/fr/compte",
    "/priv/portefeuille",
    "/priv/compte",
]


# ─── Découverte d'URL ────────────────────────────────────────────────────────

def discover_portfolio_url() -> str | None:
    """
    Après login, inspecte la navigation pour trouver l'URL du portefeuille.
    Cherche des liens contenant "portefeuille", "compte", "titres", "positions".
    """
    global _portfolio_url
    if _portfolio_url:
        return _portfolio_url

    page = session.get_page()
    if not page:
        return None

    try:
        # 1 — Cherche dans les liens de la page courante
        links = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.href)
                .filter(href =>
                    /portefeuille|compte|titres|positions|wallet/i.test(href)
                    && !href.includes('ouvrir')
                    && !href.includes('tarif')
                )
        """)
        if links:
            _portfolio_url = links[0]
            print(f"[BD Reader] URL portefeuille découverte : {_portfolio_url}")
            return _portfolio_url

        # 2 — Essaye les patterns connus en vérifiant le statut HTTP
        for pattern in _PORTFOLIO_PATTERNS:
            url = f"https://www.boursedirect.fr{pattern}"
            page.goto(url, wait_until="domcontentloaded", timeout=8000)
            if "login" not in page.url.lower() and "404" not in page.title().lower():
                _portfolio_url = page.url
                print(f"[BD Reader] URL portefeuille (fallback) : {_portfolio_url}")
                return _portfolio_url

    except Exception as e:
        print(f"[BD Reader] Erreur découverte URL : {e}")

    return None


def reset_urls():
    """Réinitialise les URLs découvertes (après reconnexion)."""
    global _portfolio_url
    _portfolio_url = None


# ─── Lecture portefeuille ────────────────────────────────────────────────────

def get_portfolio() -> dict | None:
    """
    Lit le portefeuille depuis Bourse Direct.
    Retourne {"cash": float, "positions": [{"name", "qty", "pru", "current"}]}
    ou None si la lecture échoue.
    """
    page = session.get_page()
    if not page:
        return None

    url = discover_portfolio_url()
    if not url:
        print("[BD Reader] Impossible de trouver la page portefeuille.")
        return None

    try:
        if page.url != url:
            page.goto(url, wait_until="domcontentloaded", timeout=10000)
            time.sleep(1)

        if "login" in page.url.lower():
            print("[BD Reader] Session expirée — reconnexion requise.")
            return None

        return _parse_portfolio(page)

    except Exception as e:
        print(f"[BD Reader] Erreur lecture portefeuille : {e}")
        return None


def _parse_portfolio(page) -> dict:
    """
    Parse le DOM de la page portefeuille.
    Stratégie : cherche les tableaux de positions et le solde cash.
    NOTE : les sélecteurs exacts seront affinés lors du premier test connecté.
    """
    result = {"cash": None, "positions": [], "raw_html": None}

    try:
        # Cash — cherche patterns communs : "Liquidités", "Espèces", "Solde"
        cash_text = page.evaluate("""
            () => {
                const patterns = ['liquidit', 'espèce', 'solde disponible', 'cash'];
                for (const el of document.querySelectorAll('*')) {
                    const text = el.innerText || '';
                    if (patterns.some(p => text.toLowerCase().includes(p))) {
                        const match = text.match(/([\\d\\s,.]+)\\s*€/);
                        if (match) return match[1].replace(/\\s/g, '').replace(',', '.');
                    }
                }
                return null;
            }
        """)
        if cash_text:
            result["cash"] = float(cash_text)

        # Positions — cherche les lignes de tableau avec des tickers/quantités
        rows = page.evaluate("""
            () => {
                const rows = [];
                // Cherche les lignes de tableau avec au moins 3 colonnes numériques
                document.querySelectorAll('table tr, [class*="ligne"], [class*="row"]').forEach(row => {
                    const cells = Array.from(row.querySelectorAll('td, [class*="cell"]'));
                    if (cells.length >= 3) {
                        rows.push(cells.map(c => c.innerText.trim()));
                    }
                });
                return rows;
            }
        """)
        result["positions"] = _parse_position_rows(rows)

        # Capture HTML brut pour debug si rien trouvé
        if not result["positions"] and not result["cash"]:
            result["raw_html"] = page.content()[:3000]

    except Exception as e:
        print(f"[BD Reader] Erreur parsing : {e}")

    return result


def _parse_position_rows(rows: list) -> list:
    """Tente d'extraire des positions depuis les lignes de tableau."""
    positions = []
    for row in rows:
        if len(row) < 3:
            continue
        # On cherche une ligne avec : un nom, une quantité entière, un prix décimal
        nums = []
        name_candidate = ""
        for cell in row:
            clean = cell.replace("\xa0", "").replace(" ", "").replace(",", ".")
            if re.match(r"^\d+$", clean):
                nums.append(int(clean))
            elif re.match(r"^\d+\.\d+$", clean):
                nums.append(float(clean))
            elif len(cell) > 2 and not re.match(r"^[\d.,\s%€+-]+$", cell):
                name_candidate = cell.split("\n")[0].strip()

        if name_candidate and len(nums) >= 2:
            positions.append({
                "name": name_candidate,
                "qty": nums[0] if isinstance(nums[0], int) else None,
                "pru": nums[1] if len(nums) > 1 else None,
                "current": nums[2] if len(nums) > 2 else None,
            })

    return positions
