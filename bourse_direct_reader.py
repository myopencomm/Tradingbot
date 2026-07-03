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
        ".didomi-continue-without-agreeing",
        "button.WelcomeModal-module_backBtn_QnNhW",
        "#modal-ui-settings button[data-dismiss='modal']",
        "#tosModalClosable button[data-dismiss='modal']",
        "button.close-modal-tos-closable",
        "button:has-text(\"Non merci\")",
        "button:has-text(\"Plus tard\")",
        "[class*='modal'] button[aria-label='Fermer']",
        "[class*='modal'] button[aria-label='Close']",
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
        # Sélecteur robuste : le hash CSS change à chaque déploiement BD.
        # On tente d'abord le sélecteur exact, puis un fallback large.
        orders = []
        if _click_tab(page, "Mes ordres"):
            _dismiss_popups(page)
            try:
                # Sélecteur principal (hash peut changer) puis fallback
                sel_main = "[class*='ConsolidatedOrders'][class*='content']"
                sel_back = "[class*='orderContainer'], [class*='order-line'], [class*='OrderRow']"
                blocks = page.locator(sel_main).all()
                if not blocks:
                    blocks = page.locator(sel_back).all()
                    if blocks:
                        log("Sélecteur ordres : fallback activé")

                for block in blocks:
                    raw_text = block.inner_text(timeout=2000)
                    # Log brut pour debugger les problèmes de parsing
                    print(f"[BD Reader order raw] {raw_text[:300].replace(chr(10), ' | ')}")
                    parsed = _parse_order(raw_text)
                    if parsed:
                        try:
                            oid_el = block.locator('[id^="order-"]').first
                            if oid_el.count() > 0:
                                oid = oid_el.get_attribute("id", timeout=1500)
                                if oid:
                                    parsed["order_id"] = oid.replace("order-", "")
                        except Exception:
                            pass
                        orders.append(parsed)
                    else:
                        # Ordre filtré (Annulé/Exécuté) ou format non reconnu
                        flat = raw_text.replace("\n", " ")[:120]
                        print(f"[BD Reader order skipped] {flat}")
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
    """
    Parse une .position-row.
    bd_ticker peut être absent pour les valeurs suspendues (pas de lien marché) :
    dans ce cas on garde quand même la position via son nom complet.
    """
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if len(parts) < 4:
        return None

    name = bd_ticker = qty = pru = mic = None
    for p in parts:
        if "›" in p:
            seg = p.split("›")
            if len(seg) == 2:
                bd_ticker = seg[1].strip()
            m_mic = re.search(r'\b(X[A-Z]{3})\b', seg[0])
            if m_mic:
                mic = m_mic.group(1)
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

    # bd_ticker optionnel (valeurs suspendues) : nom + qty suffisent
    if not name or qty is None:
        return None
    return {"name": name, "bd_ticker": bd_ticker or "", "qty": qty, "pru": pru,
            "mic": mic or ""}


def _parse_order(text: str) -> dict | None:
    """
    Parse un bloc .ConsolidatedOrders (titre + ordre ensemble).
    Ex : 'Exosens | XPAR › EXENS | 61.45 EUR | ... Vente(CPT) 0/17 ...
          Take Profit Seuil57.20 € En cours Profit72.90 € En cours'
    """
    flat = text.replace("\n", " ").replace("\t", " ")
    flat = re.sub(r'\s+', ' ', flat).strip()
    if not flat or len(flat) < 5:
        return None

    order = {"raw": flat}

    # Nom + ticker du titre concerné
    if "›" in flat:
        m_tick = re.search(r'›\s*([A-Z0-9]+)', flat)
        if m_tick:
            order["bd_ticker"] = m_tick.group(1).strip()
    # Place / MIC (XPAR, XAMS, XBRU…) — sert à reconstruire le suffixe yfinance
    m_mic = re.search(r'\b(X[A-Z]{3})\b', flat)
    if m_mic:
        order["mic"] = m_mic.group(1)
    # Nom = premier mot avant le premier séparateur/marché
    m_name = re.match(r'^([A-Za-zÀ-ÿ0-9.\- ]+?)\s*[|›]', flat)
    if m_name:
        nm = m_name.group(1).strip()
        # Retire un MIC accolé en fin de nom ("Unilever PLC XAMS" → "Unilever PLC")
        nm = re.sub(r'\s+X[A-Z]{3}$', '', nm).strip()
        order["name"] = nm

    # Sens — dans un bloc consolidé plusieurs ordres coexistent (ex: Achat exécuté
    # + Vente en cours). On prend le DERNIER match = l'ordre actif le plus récent.
    sens_matches = re.findall(r'(Achat|Vente)\s*\([A-Z]+\)', flat)
    if sens_matches:
        order["sens"] = sens_matches[-1]
    else:
        if re.search(r'\bVente\b', flat):
            order["sens"] = "Vente"
        elif re.search(r'\bAchat\b', flat):
            order["sens"] = "Achat"

    # Type d'ordre
    for t in ("Take Profit", "Stop Loss", "Stop Suiveur", "A cours limité", "Au marché", "Expert"):
        if t in flat:
            order["type"] = t
            break

    # Seuil (SL) — on cherche d'abord "Seuil X € En cours" (partie active),
    # pas "Seuil X € Annulé" (partie annulée d'un ancien ordre dans le même bloc).
    m_seuil = (re.search(r'Seuil\s*([\d.,]+)\s*€\s*En cours', flat)
               or re.search(r'Stop\s+([\d.,]+)\s*€\s*En cours', flat)
               or re.search(r'Seuil\s*([\d.,]+)\s*€', flat)
               or re.search(r'Stop\s+([\d.,]+)\s*€', flat))
    if m_seuil:
        order["seuil"] = _parse_float(m_seuil.group(1))

    # Profit (TP) — idem : priorité à "Profit X € En cours"
    m_profit = (re.search(r'Profit\s*([\d.,]+)\s*€\s*En cours', flat)
                or re.search(r'Limite\s+([\d.,]+)\s*€\s*En cours', flat)
                or re.search(r'Profit\s*([\d.,]+)\s*€', flat)
                or re.search(r'Limite\s+([\d.,]+)\s*€', flat))
    if m_profit:
        order["profit"] = _parse_float(m_profit.group(1))

    # Quantité exécutée / totale (ex: 0/17)
    m_qty = re.search(r'(\d+)\s*/\s*(\d+)', flat)
    if m_qty:
        order["qty_exec"] = int(m_qty.group(1))
        order["qty_total"] = int(m_qty.group(2))

    # Statut — "En cours" prime. Un Take Profit a un statut par partie ;
    # si au moins une est "En cours", l'ordre est actif.
    # "Exécuté" AVANT "Annulé" : sur un ordre à 2 volets exécuté, le volet
    # non déclenché est "Annulé" et le volet déclenché "Exécuté" — c'est bien
    # une exécution, pas une annulation.
    if "En cours" in flat:
        order["statut"] = "En cours"
    elif "Exécuté" in flat or "Execute" in flat:
        # Conservé pour la détection automatique des ventes par le sync.
        # Le volet exécuté porte le prix réel de sortie.
        order["statut"] = "Exécuté"
        m_ex = (re.search(r'Profit\s*([\d.,]+)\s*€\s*Exécuté', flat)
                or re.search(r'Seuil\s*([\d.,]+)\s*€\s*Exécuté', flat)
                or re.search(r'Limite\s+([\d.,]+)\s*€\s*Exécuté', flat)
                or re.search(r'Stop\s+([\d.,]+)\s*€\s*Exécuté', flat))
        if m_ex:
            order["exec_price"] = _parse_float(m_ex.group(1))
    elif "Annulé" in flat or "Annule" in flat:
        return None  # annulé sans exécution → ignoré

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
