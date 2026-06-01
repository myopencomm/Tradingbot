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


def _auto_gmail_check():
    if not _market_day() or not GMAIL_USER or not GMAIL_APP_PASSWORD:
        return
    notifications = gmail_sync.check_and_notify(GMAIL_USER, GMAIL_APP_PASSWORD)
    for msg in gmail_sync.format_notifications(notifications):
        telegram_bot.send(msg)


def run_scheduler():
    for t in CHECK_TIMES:
        schedule.every().day.at(t).do(
            lambda: (_auto_gmail_check(), monitor.check_positions(telegram_bot.send)) if _market_day() else None
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
    print(f"   Checks: {', '.join(CHECK_TIMES)} | Briefing: {ANALYSIS_TIME} | Swap: lundi 09:10 | Revue SL: 1er du mois 09:15 (heure Paris)")
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
    run_scheduler()
