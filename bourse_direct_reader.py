"""
Lecture du portefeuille Bourse Direct via Playwright.
URL : /fr/page/portefeuille-tr  (iframe → /priv/new/portefeuille-TR.php)
Compte CTO : select value=2  |  PEA : value=1
"""
import time
import re
import playwright_session as session

BD_PORTFOLIO_URL = "https://www.boursedirect.fr/fr/page/portefeuille-tr"
CTO_SELECT_VALUE = "2"  # Compte Titre ordinaire


def get_portfolio() -> dict | None:
    """
    Lit le portefeuille CTO depuis Bourse Direct.
    Retourne {"cash": float, "positions": [...]} ou None si échec.
    """
    page = session.get_page()
    if not page:
        return None

    try:
        if BD_PORTFOLIO_URL not in page.url:
            page.goto(BD_PORTFOLIO_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)

        if "login" in page.url.lower():
            print("[BD Reader] Session expirée.")
            return None

        # Bascule sur le CTO dans l'iframe
        page.evaluate(f"""() => {{
            const iframes = document.querySelectorAll('iframe');
            for (let f of iframes) {{
                try {{
                    const doc = f.contentDocument || f.contentWindow.document;
                    const sel = doc.querySelector('select');
                    if (sel) {{
                        sel.value = '{CTO_SELECT_VALUE}';
                        sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return true;
                    }}
                }} catch(e) {{}}
            }}
            return false;
        }}""")
        time.sleep(1.5)  # Attend le rechargement AJAX

        raw_text = page.evaluate("""() => {
            const iframes = document.querySelectorAll('iframe');
            for (let f of iframes) {
                try {
                    const doc = f.contentDocument || f.contentWindow.document;
                    const text = doc.body ? doc.body.innerText : '';
                    if (text.includes('Solde espèces')) return text;
                } catch(e) {}
            }
            return '';
        }""")

        if not raw_text:
            print("[BD Reader] Aucune donnée dans l'iframe portefeuille.")
            return None

        return _parse(raw_text)

    except Exception as e:
        print(f"[BD Reader] Erreur : {e}")
        return None


def _parse(text: str) -> dict:
    """Parse le texte brut de la page portefeuille BD."""
    result = {"cash": None, "positions": []}

    for line in text.splitlines():
        # Cash : "Solde espèces\t65,37 €\t..."
        if "Solde espèces" in line:
            m = re.search(r'Solde espèces\s*\t\s*([\d\s,]+)\s*€', line)
            if m:
                result["cash"] = _parse_float(m.group(1))
            continue

        # Ignore les lignes d'ordres ("Vente transmise", "Achat transmis")
        if "transmise" in line or "transmis" in line or "Seuil" in line or "Lim." in line:
            continue

        # Lignes de positions : commencent par " NOM\tQTE\tPRU\tCOURS\t..."
        parts = [p.strip() for p in line.split("\t")]
        parts = [p for p in parts if p]  # retire les cellules vides
        if len(parts) < 4:
            continue

        name = parts[0]
        # Filtre : nom non vide, pas un header, pas un total
        if not name or name in ("Libellé", "TOTAL", "Evaluation") or name.startswith("Sélectionnez"):
            continue
        # La quantité doit être un entier
        try:
            qty = int(_parse_float(parts[1]))
        except Exception:
            continue
        if qty <= 0:
            continue

        pru = _parse_float(parts[2]) if len(parts) > 2 else None
        cours = _parse_float(parts[3]) if len(parts) > 3 else None

        result["positions"].append({
            "name":  name,
            "qty":   qty,
            "pru":   pru,
            "cours": cours,
        })

    return result


def _parse_float(s: str) -> float | None:
    """Convertit '1 050,60 €' ou '63,42234' en float."""
    if not s:
        return None
    try:
        clean = re.sub(r'[€$£\s]', '', str(s)).replace(',', '.').replace('\xa0', '')
        return round(float(clean), 5)
    except Exception:
        return None
