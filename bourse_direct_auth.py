"""
Authentification Bourse Direct via Playwright.
Flow : login/password → détection 2FA → relay code via Telegram → session active.
"""
import os
import threading
import time

BD_URL      = "https://www.boursedirect.fr/fr/identification"
BD_LOGIN    = os.getenv("BD_LOGIN", "")
BD_PASSWORD = os.getenv("BD_PASSWORD", "")

# Partagé avec telegram_bot pour le relay 2FA
_otp_event        = threading.Event()
_otp_code: str | None = None
_waiting_for_otp  = False
OTP_TIMEOUT = 90  # secondes


def is_waiting_for_otp() -> bool:
    return _waiting_for_otp


def set_otp(code: str):
    """Appelé par le handler Telegram quand l'user répond avec le code 2FA."""
    global _otp_code
    _otp_code = code.strip()
    _otp_event.set()


def login(send_fn) -> bool:
    """
    Lance le flow de connexion complet.
    send_fn : fonction d'envoi Telegram pour le relay 2FA.
    Retourne True si connecté, False sinon.
    """
    import playwright_session as session

    page = session.get_page()
    if page is None:
        return False

    if not BD_LOGIN or not BD_PASSWORD:
        send_fn("BD_LOGIN ou BD_PASSWORD manquant dans .env")
        return False

    try:
        page.goto(BD_URL, wait_until="domcontentloaded", timeout=15000)

        # Remplissage login
        page.fill('input[name="login"]', BD_LOGIN)
        page.fill('input[name="password"]', BD_PASSWORD)
        page.click('button[type="submit"]')
        time.sleep(2)

        # Détection 2FA
        if _needs_otp(page):
            global _otp_code, _waiting_for_otp
            _otp_code = None
            _otp_event.clear()
            _waiting_for_otp = True

            send_fn(
                "Code 2FA Bourse Direct reçu par SMS ?\n"
                "Envoie-le ici (tu as 90 secondes) :"
            )
            got_code = _otp_event.wait(timeout=OTP_TIMEOUT)
            _waiting_for_otp = False

            if not got_code or not _otp_code:
                send_fn("Timeout 2FA — connexion annulée.")
                return False

            _fill_otp(page, _otp_code)
            time.sleep(2)

        if _is_logged_in(page):
            session.mark_connected()
            return True

        send_fn("Connexion échouée (credentials incorrects ou page inattendue).")
        return False

    except Exception as e:
        send_fn(f"Erreur lors de la connexion BD : {e}")
        return False


def _needs_otp(page) -> bool:
    return (
        page.locator('input[name="otp"]').count() > 0
        or page.locator('input[name="code"]').count() > 0
        or "code" in page.url.lower()
        or page.locator("text=code").count() > 0
    )


def _fill_otp(page, code: str):
    for sel in ('input[name="otp"]', 'input[name="code"]', 'input[type="tel"]'):
        if page.locator(sel).count() > 0:
            page.fill(sel, code)
            page.locator(sel).press("Enter")
            return
    # Fallback : premier input numérique visible
    page.locator('input[type="number"]:visible').first.fill(code)
    page.locator('input[type="number"]:visible').first.press("Enter")


def _is_logged_in(page) -> bool:
    return (
        "identification" not in page.url
        and "login" not in page.url.lower()
    )
