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
import os
import time
import re

def _bd_account() -> str:
    """
    Numéro de compte CTO (.env) — sert à sélectionner le BON compte dans le
    carnet legacy : la page s'ouvre par défaut sur le PEA. Annuler un ordre du
    mauvais compte serait une faute grave, donc sans correspondance certaine
    on s'abstient.

    Lu à l'APPEL et non à l'import : seul config.py charge dotenv, un import
    de ce module avant config donnerait une valeur vide (et donc une
    abstention silencieuse du trailing).
    """
    return os.getenv("BD_ACCOUNT", "").strip()

BD_PORTFOLIO_URL = "https://www.boursedirect.fr/fr/mon-compte/portefeuilles"
# Page legacy servie dans l'iframe du wrapper : on l'attaque DIRECTEMENT
# (page.request), ce qui évite l'iframe et permet de passer ?nc=<compte>.
BD_ORDER_BOOK_LEGACY_URL = "https://www.boursedirect.fr/priv/new/ordres-en-carnet.php"
# Carnet d'ordres : chaque protection (Stop Loss / Take Profit) y est une LIGNE
# SÉPARÉE avec son propre order_id — alors que la page portefeuille les fusionne
# dans un bloc unique qui ne porte que l'id de l'ordre d'achat parent (exécuté,
# donc non annulable). C'est depuis cette page que l'annulation manuelle du
# 27/07/2026 a réussi (HTTP 200).
BD_ORDER_BOOK_URL = "https://www.boursedirect.fr/fr/page/ordres-en-carnet"


def find_account_nc(html: str, account: str = "") -> str | None:
    """
    Valeur `nc` du compte CTO dans le sélecteur du carnet legacy.

    La page s'ouvre sur le PEA par défaut (`<option value="1" selected>`), donc
    sans ce paramètre on lit le carnet du MAUVAIS compte — c'est la cause du
    « carnet vide » du 28/07/2026.

    On exige une correspondance avec le numéro de compte (.env BD_ACCOUNT) :
    se fier au libellé « Compte Titre » serait plus fragile, et annuler un
    ordre sur le mauvais compte est une faute irrattrapable. Sans
    correspondance certaine → None → l'appelant s'abstient.
    """
    account = (account or _bd_account()).strip()
    if not account:
        return None
    m = re.search(r'<select[^>]*name="nc".*?</select>', html, re.S | re.I)
    if not m:
        return None
    for val, label in re.findall(r'<option\s+value="([^"]+)"[^>]*>(.*?)</option>',
                                 m.group(0), re.S | re.I):
        if account in re.sub(r"<[^>]+>", "", label):
            return val
    return None


def parse_order_book_html(html: str) -> list[dict]:
    """
    Parse le tableau du carnet d'ordres LEGACY (ordres-en-carnet.php).

    Structure réelle (relevée le 28/07/2026) — rien à voir avec l'app moderne :
    page PHP server-rendered, jQuery 1.9, servie DANS UNE IFRAME. Aucun UUID,
    aucun id="order-*" : chaque ordre est une ligne <tr class="row1|row2"> et
    son identité tient dans le lien « Annuler » :
        detailOrdre.php?cn=<compte>&ref=<ref>&refbo=<refbo>&num=1
    (num=1 = annuler, num=0 = détail).

    CHAQUE protection est une ligne SÉPARÉE : un Expert SL+TP produit deux
    lignes (ex. UNA : 49,35 puis 58,40). C'est ici — et nulle part ailleurs —
    qu'on peut cibler le Stop Loss seul.

    Fonction pure (testable sans navigateur).
    """
    rows = []
    for chunk in re.split(r'<tr\s+class="row\d"', html)[1:]:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", chunk, re.DOTALL)
        if len(tds) < 8:
            continue

        def _txt(s):
            return " ".join(re.sub(r"<[^>]+>", " ", s).split())

        cancel_m = re.search(
            r"detailOrdre\.php\?cn=([^&'\"]+)&(?:amp;)?ref=([^&'\"]+)&(?:amp;)?refbo=([^&'\"]+)&(?:amp;)?num=1",
            chunk,
        )
        tick_m = re.search(r"val=E:([A-Z0-9]+)", chunk)
        if not cancel_m or not tick_m:
            continue
        try:
            limit = float(_txt(tds[3]).replace(",", ".").replace(" ", ""))
        except ValueError:
            limit = None
        try:
            qty = int(_txt(tds[2]))
        except ValueError:
            qty = None
        rows.append({
            "sens":     _txt(tds[0]),
            "name":     _txt(tds[1]),
            "ticker":   tick_m.group(1),
            "qty":      qty,
            "limit":    limit,
            "etat":     _txt(tds[4]),
            "validite": _txt(tds[6]),
            "cn":       cancel_m.group(1),
            "ref":      cancel_m.group(2),
            "refbo":    cancel_m.group(3),
        })
    return rows


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
    Lit le CARNET D'ORDRES du compte CTO — une ligne par ordre actif.

    STRUCTURE (inspection manuelle 28/07/2026) : page legacy PHP
    server-rendered (ordres-en-carnet.php, jQuery 1.9) servie dans une IFRAME.
    On l'attaque donc DIRECTEMENT via page.request (mêmes cookies de session,
    sans toucher à la page courante) : pas d'iframe à traverser, et surtout on
    peut passer ?nc=<compte>.

    ⚠️ La page s'ouvre par défaut sur le PEA. Sans le bon `nc`, on lit le
    carnet du MAUVAIS compte (« carnet vide » du 28/07). Sans correspondance
    certaine avec BD_ACCOUNT on s'abstient : annuler un ordre sur le mauvais
    compte serait irrattrapable.
    """
    def log(msg):
        print(f"[BD Carnet] {msg}")
        if send_fn:
            send_fn(msg)

    def _html(resp) -> str:
        """
        Décode la réponse. La page legacy est servie en LATIN-1 (« marché » =
        octet 0xe9) alors que resp.text() suppose de l'UTF-8 → UnicodeDecodeError
        et carnet illisible (28/07/2026). On tente l'UTF-8 puis on retombe sur
        cp1252 (surensemble de latin-1).
        """
        raw = resp.body()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace")

    try:
        r = page.request.get(BD_ORDER_BOOK_LEGACY_URL, timeout=25000)
        if not r.ok:
            log(f"page carnet inaccessible (HTTP {r.status})")
            return []
        html = _html(r)

        nc = find_account_nc(html)
        if not nc:
            log("compte CTO introuvable dans le sélecteur du carnet "
                "(BD_ACCOUNT absent du .env ou libellé changé) — abstention")
            return []
        print(f"[BD Carnet] compte CTO = nc={nc}")

        r2 = page.request.get(f"{BD_ORDER_BOOK_LEGACY_URL}?nc={nc}", timeout=25000)
        if not r2.ok:
            log(f"carnet du compte {nc} inaccessible (HTTP {r2.status})")
            return []
        html = _html(r2)

        # Garde-fou : le HTML renvoyé doit bien être celui du compte visé.
        acct = _bd_account()
        if acct and acct not in html:
            log("le carnet renvoyé ne mentionne pas le compte attendu — abstention")
            return []

        rows = parse_order_book_html(html)
        if not rows:
            log("aucun ordre actif au carnet du CTO")
            return []
        print(f"[BD Carnet] {len(rows)} ordre(s)")
        for o in rows:
            print(f"[BD Carnet] {o['ticker']:6} {o['sens']:6} qty={o['qty']} "
                  f"limit={o['limit']} etat={o['etat']!r} "
                  f"ref={o['ref']} refbo={o['refbo']}")
        return rows
    except Exception as e:
        log(f"lecture carnet échouée : {e}")
    return []


def find_stop_loss_order(rows: list[dict], ticker: str, entry: float) -> dict | None:
    """
    Isole le Stop Loss d'une position parmi les lignes du carnet : c'est la
    vente dont la limite est SOUS le PRU (le Take Profit est au-dessus).
    Retourne None si ce n'est pas STRICTEMENT univoque — sur compte réel,
    annuler la mauvaise ligne laisserait la position sans protection.
    """
    base = (ticker or "").upper().split(".")[0]
    mine = [o for o in rows
            if (o.get("ticker") or "").upper() == base
            and o.get("limit") is not None
            and (o.get("sens") or "").lower().startswith("vente")]
    below = [o for o in mine if o["limit"] < entry]
    return below[0] if len(below) == 1 else None


def find_take_profit_order(rows: list[dict], ticker: str, entry: float) -> dict | None:
    """
    Take Profit d'une position : la vente dont la limite est AU-DESSUS du PRU.
    None si non univoque. Nécessaire au trailing : reposer un ordre Expert
    (SL+TP) sans avoir annulé l'ancien TP créerait un DOUBLON de vente — soit
    deux fois la quantité détenue.
    """
    base = (ticker or "").upper().split(".")[0]
    mine = [o for o in rows
            if (o.get("ticker") or "").upper() == base
            and o.get("limit") is not None
            and (o.get("sens") or "").lower().startswith("vente")]
    above = [o for o in mine if o["limit"] > entry]
    return above[0] if len(above) == 1 else None


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

    def trace(msg):
        """Trace de diagnostic : fichier de log UNIQUEMENT.
        Le contenu brut des lignes BD n'a aucun intérêt dans Telegram — il y
        noyait le résultat du sync (constaté le 29/07/2026)."""
        print(f"[BD Reader] {msg}")

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
                raw = row.inner_text(timeout=2000)
                parsed = _parse_position(raw)
                if parsed:
                    positions.append(parsed)
                # Trace brute (comme pour les ordres) : indispensable pour
                # diagnostiquer un format inattendu (valeurs US, devises).
                trace("[position raw] " + " | ".join(raw.split("\n")))
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
    pru_currency = "EUR"
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
            # La devise du PRU décide s'il est comparable au cours yfinance :
            # BD affiche le PRU des valeurs US converti en EUR sur cet onglet,
            # alors que les ordres restent en $US.
            pru_currency = _detect_currency(p)

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
    return {"name": _clean_name(name), "bd_ticker": bd_ticker or "", "qty": qty,
            "pru": pru, "mic": mic or "", "pru_currency": pru_currency}


# ── Montants multi-devises ────────────────────────────────────────────────
# BD n'écrit PAS les valeurs US en euros : "Seuil255.60 $US", "Lim. 268.65 $US",
# cours "267.430 USD". Les regex ne cherchaient que "€" → sur JNJ (29/07/2026)
# le sync affichait « ? : Achat Take Profit ⚠️ SL/TP non lus » alors que
# l'ordre était bien présent sur BD.
_CUR    = r'(?:€|\$\s?US\b|\$|USD|EUR|£|GBP|CHF)'
_AMT    = r'([\d.,]+)\s*' + _CUR      # capturant
_AMT_NC = r'[\d.,]+\s*' + _CUR        # non capturant


def _clean_name(raw: str) -> str:
    """
    Nom de valeur débarrassé du code marché accolé par BD :
    "Unilever PLC XAMS" → "Unilever PLC", "ILLUMINA INC(XNGS)" → "ILLUMINA INC".
    Boucle car le nom peut porter les DEUX formes à la fois
    ("Johnson & Johnson(XNYS) XNYS" quand le séparateur suivant est '›').
    """
    nm = (raw or "").strip()
    for _ in range(3):
        new = re.sub(r'\s*\(X[A-Z]{3}\)$', '', nm).strip()
        new = re.sub(r'\s+X[A-Z]{3}$', '', new).strip()
        if new == nm:
            break
        nm = new
    return nm


def _detect_currency(flat: str) -> str:
    """Devise d'un bloc BD, déduite des libellés ('$US', 'USD', '£'…)."""
    if re.search(r'\$\s?US\b|\bUSD\b|\$', flat):
        return "USD"
    if re.search(r'£|\bGBP\b', flat):
        return "GBP"
    if re.search(r'\bCHF\b', flat):
        return "CHF"
    return "EUR"


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
    # La classe de caractères DOIT accepter & ( ) ' / : sans ça
    # "Johnson & Johnson(XNYS) | XNYS › JNJ" ne matchait pas et l'ordre
    # s'affichait « ? » (constaté le 29/07/2026 sur JNJ).
    m_name = re.match(r"^([A-Za-zÀ-ÿ0-9.\-&()'’/ ]+?)\s*[|›]", flat)
    if m_name:
        order["name"] = _clean_name(m_name.group(1))

    # Devise de l'ordre : BD libelle les valeurs US en "$US" ("Seuil255.60 $US")
    # et affiche le cours en "267.430 USD". Sans ça tous les montants étaient
    # lus comme des euros — ou pas lus du tout.
    order["currency"] = _detect_currency(flat)

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
    m_seuil = (re.search(rf'Seuil\s*{_AMT}\s*En cours', flat)
               or re.search(rf'Stop\s+{_AMT}\s*En cours', flat)
               or re.search(rf'Seuil\s*{_AMT}', flat)
               or re.search(rf'Stop\s+{_AMT}', flat))
    if m_seuil:
        order["seuil"] = _parse_float(m_seuil.group(1))

    # Profit (TP) — idem : priorité à "Profit X € En cours"
    m_profit = (re.search(rf'Profit\s*{_AMT}\s*En cours', flat)
                or re.search(rf'Limite\s+{_AMT}\s*En cours', flat)
                or re.search(rf'Profit\s*{_AMT}', flat)
                or re.search(rf'Limite\s+{_AMT}', flat))
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
    m_ex = (re.search(rf'Profit\s*Exé\.?\s*{_AMT}', flat)
            or re.search(rf'Seuil\s*Exé\.?\s*{_AMT}', flat)
            or re.search(rf'Lim(?:ite)?\.?\s*Exé\.?\s*{_AMT}', flat)
            or re.search(rf'Ordre exécuté\s*\d+\s*/\s*\d+\s*Lim\.\s*{_AMT_NC}\s*{_AMT}', flat))
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
        # On retire TOUT sauf chiffres/séparateurs : symboles (€ $ £), codes
        # devise ("$US", "USD", "EUR"), espaces fines et insécables. Sans ça
        # "255.60 $US" (format BD des valeurs US) renvoyait None.
        clean = re.sub(r'[^\d.,\-]', '', str(s))
        if ',' in clean and '.' in clean:
            clean = clean.replace(',', '')      # virgule = séparateur milliers
        elif ',' in clean:
            clean = clean.replace(',', '.')     # virgule = décimale (FR)
        return round(float(clean), 5)
    except Exception:
        return None
