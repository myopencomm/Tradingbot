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
        login_field = page.locator('input[placeholder="Identifiant"]')
        login_field.click()
        login_field.type(BD_LOGIN, delay=50)

        pwd_field = page.locator('input[placeholder="Mot de passe"]')
        pwd_field.click()
        pwd_field.type(BD_PASSWORD, delay=50)

        page.click('button:has-text("Se connecter")')

        # Polling : attend jusqu'à 10s que la page évolue
        # On cherche SOIT les spinbuttons TOTP, SOIT une URL hors /login
        totp_detected = False
        for _ in range(20):
            time.sleep(0.5)
            if "login" not in page.url.lower():
                break  # Connecté directement (pas de TOTP)
            if page.locator('[role="spinbutton"]').count() >= 4:
                totp_detected = True
                break

        # ── TOTP détecté ─────────────────────────────────────────────────────
        if totp_detected:
            global _otp_code, _waiting_for_otp
            _otp_code = None
            _otp_event.clear()
            _waiting_for_otp = True

            send_fn(
                "Code TOTP Bourse Direct ?\n"
                "(Google Authenticator, Authy...)\n"
                "Envoie le code a 6 chiffres (90 secondes) :"
            )
            got_code = _otp_event.wait(timeout=OTP_TIMEOUT)
            _waiting_for_otp = False

            if not got_code or not _otp_code:
                send_fn("Timeout TOTP — connexion annulée.")
                return False

            if not _fill_totp(page, _otp_code, send_fn):
                return False

            # Polling post-TOTP : attend la redirection
            for _ in range(12):
                time.sleep(0.5)
                if "login" not in page.url.lower():
                    break

        if _is_logged_in(page):
            session.mark_connected()
            return True

        send_fn(f"Connexion échouée. URL : {page.url[:80]}")
        return False

    except Exception as e:
        send_fn(f"Erreur connexion BD : {e}")
        return False


def _needs_otp(page) -> bool:
    # Détection stricte : uniquement sur la présence réelle des spinbuttons
    # (pas de match texte — "authentification" est présent sur la page de login)
    return page.locator('[role="spinbutton"]').count() >= 4


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
        time.sleep(1)
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

        # Clique sur Continuer (délai pour que Vue.js valide le formulaire)
        time.sleep(0.5)
        try:
            page.locator('button:has-text("Continuer")').click(timeout=3000)
        except Exception:
            spinbuttons[-1].press("Enter")

        return True

    except Exception as e:
        if send_fn:
            send_fn(f"Erreur remplissage TOTP : {e}")
        return False


def _is_logged_in(page) -> bool:
    return "login" not in page.url.lower()
