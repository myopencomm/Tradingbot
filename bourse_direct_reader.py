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
# Carnet d'ordres : chaque protection (Stop Loss / Take Profit) y est une LIGNE
# SÉPARÉE avec son propre order_id — alors que la page portefeuille les fusionne
# dans un bloc unique qui ne porte que l'id de l'ordre d'achat parent (exécuté,
# donc non annulable). C'est depuis cette page que l'annulation manuelle du
# 27/07/2026 a réussi (HTTP 200).
BD_ORDER_BOOK_URL = "https://www.boursedirect.fr/fr/page/ordres-en-carnet"


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


def read_order_book(page, send_fn=None) -> list[dict]:
    """
    Lit le CARNET D'ORDRES (page dédiée) : chaque Stop Loss / Take Profit y est
    une ligne autonome avec son propre order_id, contrairement à la page
    portefeuille qui les fusionne sous l'id du parent exécuté.

    DIAGNOSTIC : plutôt que de deviner la structure DOM (le sélecteur
    [id^="order-"] ne renvoie RIEN sur cette page — constaté le 27/07/2026), on
    écoute les appels API que la page émet en se chargeant. La liste des ordres
    arrive forcément en JSON : c'est la source fiable des order_id enfants,
    sans scraping. Même méthode que pour les ordres US ([CAPTURE]).

    Journalise sous [BD Carnet API]. Aucune annulation automatique n'est
    branchée dessus tant que le format n'est pas confirmé — sur un compte réel,
    annuler le mauvais id laisserait une position à nu.

    `get_portfolio` renavigue vers la page portefeuille à chaque appel : visiter
    cette page ne perturbe donc pas les lectures suivantes.
    """
    def log(msg):
        print(f"[BD Carnet] {msg}")
        if send_fn:
            send_fn(msg)

    seen: list[dict] = []

    def _on_response(resp):
        try:
            if "/hub/" not in resp.url:
                return
            body = resp.text()
            # On ne garde que ce qui ressemble à une liste d'ordres
            if not body or len(body) < 20:
                return
            if not any(k in body for k in ('"order', '"status"', '"stop', '"limit')):
                return
            print(f"[BD Carnet API] {resp.status} {resp.url}")
            print(f"[BD Carnet BODY] {body[:2000]}")
            seen.append({"url": resp.url, "body": body[:2000]})
        except Exception:
            pass

    try:
        page.on("response", _on_response)
        try:
            page.goto(BD_ORDER_BOOK_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(4)  # laisse les appels XHR de la page se terminer
            _dismiss_popups(page)
            time.sleep(1)
        finally:
            try:
                page.remove_listener("response", _on_response)
            except Exception:
                pass
        if not seen:
            log("aucune réponse API exploitable captée sur la page carnet")
    except Exception as e:
        log(f"lecture carnet échouée : {e}")
    return seen


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
                            # Un bloc consolidé peut contenir PLUSIEURS ordres
                            # (achat parent exécuté + enfants TP/SL "En cours").
                            # order_id (1er id) peut donc désigner le parent
                            # EXÉCUTÉ — inutilisable pour /order/cancel (403).
                            # On garde TOUS les ids (order_ids) pour cibler le
                            # bon sous-ordre (cas trailing AIR 27/07/2026).
                            # On garde l'id ET le texte propre à chaque
                            # sous-ordre : c'est ce texte ("Ordre exécuté" vs
                            # "En cours") qui permettra de distinguer l'achat
                            # parent non annulable de la protection active.
                            entries = []
                            for oid_el in block.locator('[id^="order-"]').all():
                                oid = oid_el.get_attribute("id", timeout=1500)
                                if not oid:
                                    continue
                                try:
                                    otxt = " ".join(oid_el.inner_text(timeout=2000).split())
                                except Exception:
                                    otxt = ""
                                entries.append({"id": oid.replace("order-", ""), "text": otxt})
                            if entries:
                                parsed["order_id"] = entries[0]["id"]  # rétrocompat
                                parsed["order_ids"] = [e["id"] for e in entries]
                                parsed["order_entries"] = entries
                                if len(entries) > 1:
                                    tick = parsed.get("bd_ticker", "?")
                                    for e in entries:
                                        print(f"[BD Reader id] {tick} {e['id']} | {e['text'][:160]}")
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

    # ── Prix réel d'exécution — formats RÉELS observés sur BD (logs bruts) :
    #   volet TP déclenché : "Profit206.00 € Profit Exé. 208.00 €"
    #   volet SL déclenché : "Seuil57.20 € Seuil Exé. 57.20 €"
    #   ordre simple rempli : "Ordre exécuté 18/18 Lim. 53.06 € 53.05 €"
    # Le prix après "Exé." est le prix RÉEL (peut différer du seuil posé, ex:
    # gap d'ouverture au-dessus du TP).
    m_ex = (re.search(r'Profit\s*Exé\.?\s*([\d.,]+)\s*€', flat)
            or re.search(r'Seuil\s*Exé\.?\s*([\d.,]+)\s*€', flat)
            or re.search(r'Lim(?:ite)?\.?\s*Exé\.?\s*([\d.,]+)\s*€', flat)
            or re.search(r'Ordre exécuté\s*\d+\s*/\s*\d+\s*Lim\.\s*[\d.,]+\s*€\s*([\d.,]+)\s*€', flat))
    if m_ex:
        order["exec_price"] = _parse_float(m_ex.group(1))

    # Statut — "en cours" prime : un bloc peut contenir un volet exécuté
    # (entrée remplie) ET des protections encore actives.
    # INSENSIBLE À LA CASSE : BD écrit "En cours" sur les volets TP/SL mais
    # "Ordre en cours" (minuscule) sur les achats limite en attente — sans ça
    # les ordres d'achat en attente étaient invisibles pour le sync.
    # Exécution AVANT "Annulé" : sur un ordre TP/SL à 2 volets exécuté, le
    # volet non déclenché est "Annulé" et le volet déclenché porte "Exé." —
    # c'est une exécution, pas une annulation.
    if re.search(r'en cours', flat, re.I):
        order["statut"] = "En cours"
    elif m_ex or re.search(r'exécuté|execute', flat, re.I):
        # Conservé pour la détection automatique des ventes par le sync.
        order["statut"] = "Exécuté"
    elif re.search(r'annulé|annule', flat, re.I):
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
