"""
Lecture du portefeuille Bourse Direct via Playwright.
URL : /fr/page/portefeuille-tr  (iframe → /priv/new/portefeuille-TR.php)
Compte CTO : select value=2  |  PEA : value=1
"""
import time
import re

BD_PORTFOLIO_URL = "https://www.boursedirect.fr/fr/page/portefeuille-tr"
CTO_SELECT_VALUE = "2"  # Compte Titre ordinaire


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
        time.sleep(2)

        if "login" in page.url.lower():
            log("Session expirée — reconnecte avec /connect.")
            return None

        # Le contenu est dans un iframe interne (/priv/new/portefeuille-TR.php)
        # qui charge en AJAX. On accède via l'API frames de Playwright.
        pf_frame = None
        for _ in range(20):  # polling jusqu'à 10s
            time.sleep(0.5)
            for fr in page.frames:
                if "portefeuille-TR" in fr.url or "portefeuille" in fr.url.lower():
                    pf_frame = fr
                    break
            if pf_frame:
                # Vérifie que le contenu est chargé
                try:
                    if pf_frame.locator("select").count() > 0:
                        break
                except Exception:
                    pass
            pf_frame = None

        if not pf_frame:
            log("Iframe portefeuille introuvable. La page a peut-être changé de structure.")
            return None

        # Bascule sur le CTO
        try:
            pf_frame.locator("select").first.select_option(CTO_SELECT_VALUE)
            time.sleep(2)  # rechargement AJAX après changement de compte
        except Exception as e:
            log(f"Bascule CTO échouée : {e}")

        # Lit le texte du frame
        raw_text = ""
        for _ in range(10):
            try:
                raw_text = pf_frame.locator("body").inner_text(timeout=3000)
                if "Solde espèces" in raw_text:
                    break
            except Exception:
                pass
            time.sleep(0.5)

        if "Solde espèces" not in raw_text:
            log("Données portefeuille non chargées (pas de 'Solde espèces').")
            return None

        return _parse(raw_text)

    except Exception as e:
        log(f"Erreur : {e}")
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
