import schedule
import time
from datetime import datetime
from config import CHECK_TIMES, ANALYSIS_TIME, TELEGRAM_TOKEN, AI_PROVIDER, GMAIL_USER, GMAIL_APP_PASSWORD
import monitor
import analysis
import telegram_bot
import gmail_sync


def _market_day() -> bool:
    """Vrai si aujourd'hui est un jour de semaine (lundi–vendredi)."""
    return datetime.now().weekday() < 5  # 0=lundi … 4=vendredi


def _weekly_version_check():
    """Vérifie silencieusement si une mise à jour est dispo sur GitHub. Envoie une notif si en retard."""
    import subprocess
    from config import BASE_DIR
    try:
        subprocess.run(
            ["git", "fetch", "origin", "--quiet"],
            cwd=str(BASE_DIR), capture_output=True, timeout=20
        )
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5
        ).stdout.strip()
        remote = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5
        ).stdout.strip()
        if not local or not remote or local == remote:
            return
        behind = subprocess.run(
            ["git", "rev-list", "--count", f"{local}..{remote}"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=5
        ).stdout.strip()
        nb = behind if behind.isdigit() else "?"
        telegram_bot.send(
            f"🔄 MISE À JOUR DISPONIBLE\n\n"
            f"{nb} commit(s) de retard sur la version officielle.\n\n"
            f"Pour mettre à jour :\n"
            f"./bot.sh update\n\n"
            f"(Vérification automatique chaque lundi)"
        )
    except Exception as e:
        print(f"[version check] erreur silencieuse: {e}")


def _hourly_bd_sync():
    """
    Vérification horaire SILENCIEUSE du portefeuille BD (9h-22h, jours de marché).
    Détecte les ventes (TP/SL exécutés) et achats passés hors du bot, met à jour
    positions.json automatiquement. Message Telegram uniquement si exécution détectée.
    """
    if not _market_day():
        return
    if not (9 <= datetime.now().hour <= 22):
        return
    import bot_mode
    import playwright_session
    if not (bot_mode.is_playwright() and playwright_session.is_connected()):
        return
    import sync_engine
    try:
        playwright_session.run(
            lambda page: sync_engine.sync(page, telegram_bot.send, silent=True),
            timeout=90,
        )
    except Exception as e:
        print(f"[hourly sync] {e}")
    # Trailing stop : remonte les SL au PRU sur BD (positions auto +3%,
    # manuelles +5%) — uniquement celles protégées par un Expert actif.
    try:
        import autonomous_engine
        autonomous_engine.trailing_stop_cycle(telegram_bot.send)
    except Exception as e:
        print(f"[hourly trailing] {e}")


def _auto_gmail_check():
    if not _market_day() or not GMAIL_USER or not GMAIL_APP_PASSWORD:
        return
    notifications = gmail_sync.check_and_notify(GMAIL_USER, GMAIL_APP_PASSWORD)
    for msg in gmail_sync.format_notifications(notifications):
        telegram_bot.send(msg)


def run_scheduler():
    for t in CHECK_TIMES:
        schedule.every().day.at(t).do(
            lambda: (
                _auto_gmail_check(),
                monitor.check_pending_orders(telegram_bot.send),
                monitor.check_positions(telegram_bot.send),
            ) if _market_day() else None
        )
    schedule.every().day.at(ANALYSIS_TIME).do(
        lambda: analysis.morning_briefing(telegram_bot.send) if _market_day() else None
    )
    schedule.every().monday.at("09:10").do(
        lambda: analysis.weekly_swap_analysis(telegram_bot.send)
    )
    schedule.every().day.at("09:15").do(
        lambda: analysis.monthly_breach_review(telegram_bot.send)
        if _market_day() and datetime.now().day == 1 else None
    )
    schedule.every().monday.at("09:20").do(_weekly_version_check)
    schedule.every().hour.at(":35").do(_hourly_bd_sync)
    print(f"   Checks: {', '.join(CHECK_TIMES)} | Briefing: {ANALYSIS_TIME} | Swap: lundi 09:10 | Revue SL: 1er du mois 09:15 | Version: lundi 09:20 | Sync BD silencieux: toutes les heures à :35 (heure Paris)")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    print("=" * 40)
    print("  TradingBot")
    print("=" * 40)
    print(f"  Telegram : {'OK' if TELEGRAM_TOKEN else 'MANQUANT (.env)'}")
    print(f"  Gmail    : {'OK' if GMAIL_USER else 'non configure (optionnel)'}")
    print(f"  AI       : {AI_PROVIDER}")

    import portfolio
    data = portfolio.load()
    positions = data.get("positions", {})
    print(f"  Cash     : {data.get('cash_available', 0)}€")
    print(f"  Positions: {list(positions.keys()) or 'aucune'}")
    print("=" * 40)

    telegram_bot.start_polling()

    nb = len(positions)
    cash = data.get("cash_available", 0)
    telegram_bot.send(
        f"TradingBot en ligne 🤖\n"
        f"{nb} position{'s' if nb > 1 else ''} | {cash}€ cash"
    )

    run_scheduler()
