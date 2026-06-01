"""
Sync Gmail IMAP → Telegram.

Flow :
1. Cherche les emails non lus de noreply@boursedirect.fr
2. "Déclenchement de votre stratégie" → notifie l'utilisateur et demande le prix
3. "Finalisation de votre stratégie" → marque lu, ignoré (doublon de confirmation)
4. Doublons : flag gmail_triggered dans positions.json, pas de double notification

Le bot ne clôture JAMAIS automatiquement — il demande toujours le prix réel.
"""
import imaplib
import email
import re
from email.header import decode_header
from html.parser import HTMLParser

import portfolio

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
BD_SENDER = "boursedirect.fr"


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    p = _HTMLTextExtractor()
    p.feed(html)
    return p.get_text()


def _decode_header_str(raw) -> str:
    parts = decode_header(raw or "")
    return "".join(
        chunk.decode(enc or "utf-8") if isinstance(chunk, bytes) else (chunk or "")
        for chunk, enc in parts
    )


def _get_text_body(msg) -> str:
    for part in msg.walk():
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain":
            return text
        if ctype == "text/html":
            return _strip_html(text)
    return ""


def _parse_strategy(body: str) -> dict | None:
    """Extrait strategy_type et company_name depuis le corps de l'email."""
    m = re.search(
        r"strat[eé]gie\s+(take_profit|stop_loss)\s+sur\s+la\s+valeur\s+(.+?)\s+a\s+[eé]t[eé]\s+(activ[eé]e|finalis[eé]e)",
        body,
        re.IGNORECASE,
    )
    if m:
        return {
            "strategy_type": m.group(1),
            "company_name":  m.group(2).strip(),
            "event":         "triggered" if "activ" in m.group(3).lower() else "finalized",
        }
    return None


def _match_position(company_name: str) -> str | None:
    """Trouve le nom de position le plus proche du nom de société reçu par email."""
    import yfinance as yf
    cn = company_name.lower().replace(" ", "").replace("-", "")
    positions = portfolio.get_positions()

    for name in positions:
        if name.lower() in cn or cn in name.lower():
            return name

    for name, cfg in positions.items():
        try:
            info = yf.Ticker(cfg["ticker"]).fast_info
            long_name = (getattr(info, "company_name", "") or "").lower().replace(" ", "").replace("-", "")
            if long_name and (long_name in cn or cn in long_name or cn[:6] in long_name):
                return name
        except Exception:
            pass

    return None


def check_and_notify(gmail_user: str, gmail_password: str) -> list[dict]:
    """
    Vérifie Gmail et retourne la liste des notifications à envoyer.
    Chaque item : {"position": str, "company": str, "strategy": str}
    Retourne [] si rien à notifier.
    """
    notifications = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(gmail_user, gmail_password)
        mail.select("INBOX")

        _, msg_ids = mail.search(None, f'(UNSEEN FROM "{BD_SENDER}")')
        ids = msg_ids[0].split() if msg_ids[0] else []

        for msg_id in ids:
            try:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                subject = _decode_header_str(msg.get("Subject", ""))

                # Marque toujours comme lu pour ne pas retraiter
                mail.store(msg_id, "+FLAGS", "\\Seen")

                # "Finalisation" = doublon de confirmation → ignorer
                if "finalisation" in subject.lower():
                    continue

                # "Déclenchement" = ordre activé → notifier
                if "d" not in subject.lower() or "clenchement" not in subject.lower():
                    continue

                body   = _get_text_body(msg)
                parsed = _parse_strategy(body)
                if not parsed:
                    continue

                company  = parsed["company_name"]
                strategy = parsed["strategy_type"]
                pos_name = _match_position(company)

                if not pos_name:
                    notifications.append({
                        "status":   "no_match",
                        "company":  company,
                        "strategy": strategy,
                    })
                    continue

                # Déjà notifié pour cette position → doublon, ignorer
                if portfolio.is_gmail_triggered(pos_name):
                    continue

                # Marque la position comme "en attente de prix"
                portfolio.mark_gmail_triggered(pos_name, strategy)
                notifications.append({
                    "status":   "notify",
                    "position": pos_name,
                    "company":  company,
                    "strategy": strategy,
                })

            except Exception as e:
                notifications.append({"status": "error", "error": str(e)})

        mail.logout()

    except Exception as e:
        notifications.append({"status": "connect_error", "error": str(e)})

    return notifications


def format_notifications(notifications: list[dict]) -> list[str]:
    """Retourne la liste des messages Telegram à envoyer."""
    messages = []
    for n in notifications:
        if n["status"] == "notify":
            label = "Take Profit" if n["strategy"] == "take_profit" else "Stop Loss"
            messages.append(
                f"📧 Ordre BD declenche — {n['position']} ({label})\n\n"
                f"Bourse Direct indique que votre stratégie {n['strategy']} "
                f"sur {n['company']} a ete activee.\n\n"
                f"A quel prix avez-vous vendu ?\n"
                f"→ /vendu {n['position']} PRIX"
            )
        elif n["status"] == "no_match":
            messages.append(
                f"📧 Email BD recu : {n['company']} ({n['strategy']}) "
                f"— position non trouvee dans le portefeuille.\n"
                f"Si vendu : /vendu NOM PRIX"
            )
        elif n["status"] in ("error", "connect_error"):
            messages.append(f"Erreur Gmail : {n.get('error', '')}")
    return messages
