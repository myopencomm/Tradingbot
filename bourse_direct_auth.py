"""
Authentification Bourse Direct via Playwright.
Flow : login/password → détection TOTP → relay code via Telegram → session active.
"""
import os
import threading
import time

BD_URL      = "https://www.boursedirect.fr/fr/login"
BD_LOGIN    = os.getenv("BD_LOGIN", "")
BD_PASSWORD = os.getenv("BD_PASSWORD", "")

_otp_event        = threading.Event()
_otp_code: str | None = None
_waiting_for_otp  = False
OTP_TIMEOUT = 90


def is_waiting_for_otp() -> bool:
    return _waiting_for_otp


def set_otp(code: str):
    global _otp_code
    _otp_code = code.strip()
    _otp_event.set()


def login(send_fn) -> bool:
    import playwright_session as session

    page = session.get_page()
    if page is None:
        return False

    if not BD_LOGIN or not BD_PASSWORD:
        send_fn("BD_LOGIN ou BD_PASSWORD manquant dans .env")
        return False

    try:
        page.goto(BD_URL, wait_until="domcontentloaded", timeout=20000)
        time.sleep(1)

        # ── Remplissage credentials ──────────────────────────────────────────
        # Utilise click + type pour déclencher les événements clavier (Vue.js)
        login_field = page.locator('input[placeholder="Identifiant"]')
        login_field.click()
        login_field.type(BD_LOGIN, delay=50)

        pwd_field = page.locator('input[placeholder="Mot de passe"]')
        pwd_field.click()
        pwd_field.type(BD_PASSWORD, delay=50)

        page.click('button:has-text("Se connecter")')

        # Attend que la page change (soit TOTP, soit dashboard)
        try:
            page.wait_for_url(lambda url: "login" not in url or "TOTP" in page.content(),
                              timeout=8000)
        except Exception:
            pass  # Timeout ok — on vérifie ensuite
        time.sleep(1)

        # ── Détection TOTP ──────────────────────────────────────────────────
        if _needs_otp(page):
            global _otp_code, _waiting_for_otp
            _otp_code = None
            _otp_event.clear()
            _waiting_for_otp = True

            send_fn(
                "Code TOTP Bourse Direct ?\n"
                "(Application d'authentification — Google Authenticator, Authy...)\n"
                "Envoie le code a 6 chiffres (90 secondes) :"
            )
            got_code = _otp_event.wait(timeout=OTP_TIMEOUT)
            _waiting_for_otp = False

            if not got_code or not _otp_code:
                send_fn("Timeout TOTP — connexion annulée.")
                return False

            success = _fill_totp(page, _otp_code, send_fn)
            if not success:
                return False

            # Attend la redirection post-TOTP
            try:
                page.wait_for_url(lambda url: "login" not in url, timeout=10000)
            except Exception:
                pass
            time.sleep(2)

        if _is_logged_in(page):
            session.mark_connected()
            return True

        # Debug : montre l'URL courante pour diagnostiquer
        send_fn(f"Connexion échouée. URL actuelle : {page.url[:80]}")
        return False

    except Exception as e:
        send_fn(f"Erreur connexion BD : {e}")
        return False


def _needs_otp(page) -> bool:
    content = page.content()
    return (
        page.locator('[role="spinbutton"]').count() >= 4
        or page.locator('input[type="number"]').count() >= 4
        or "TOTP" in content
        or "authentification" in content.lower() and "code" in content.lower()
    )


def _fill_totp(page, code: str, send_fn=None) -> bool:
    """
    Remplit le formulaire TOTP BD (6 spinbuttons individuels).
    Utilise click + press_sequentially pour déclencher les événements Vue.js.
    """
    digits = [c for c in code.strip() if c.isdigit()]
    if len(digits) != 6:
        if send_fn:
            send_fn(f"Code invalide : {len(digits)} chiffres reçus, 6 attendus.")
        return False

    try:
        # Attendre que les spinbuttons soient présents
        page.wait_for_selector('[role="spinbutton"]', timeout=5000)
        spinbuttons = page.locator('[role="spinbutton"]').all()

        if len(spinbuttons) < 6:
            if send_fn:
                send_fn(f"Formulaire TOTP inattendu ({len(spinbuttons)} champs). Réessaie.")
            return False

        # Remplit chaque champ digit par digit avec événements clavier
        for i, digit in enumerate(digits):
            sb = spinbuttons[i]
            sb.click()
            time.sleep(0.05)
            # Triple clear + type pour garantir la réactivité Vue
            sb.press("Control+a")
            sb.press("Backspace")
            sb.press_sequentially(digit, delay=30)
        time.sleep(0.3)

        # Coche "Faire confiance à cet appareil — Oui"
        try:
            radio_oui = page.locator('input[type="radio"]').first
            if not radio_oui.is_checked():
                radio_oui.click()
            time.sleep(0.2)
        except Exception:
            pass

        # Attend que le bouton Continuer soit actif puis clique
        try:
            btn = page.locator('button:has-text("Continuer")')
            btn.wait_for(state="enabled", timeout=4000)
            btn.click()
        except Exception:
            # Fallback : Enter depuis le dernier champ
            spinbuttons[-1].press("Enter")

        return True

    except Exception as e:
        if send_fn:
            send_fn(f"Erreur remplissage TOTP : {e}")
        return False


def _is_logged_in(page) -> bool:
    return "login" not in page.url.lower()
