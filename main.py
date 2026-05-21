import schedule
import time
from config import CHECK_TIMES, ANALYSIS_TIME, TELEGRAM_TOKEN, AI_PROVIDER
import monitor
import analysis
import telegram_bot


def run_scheduler():
    for t in CHECK_TIMES:
        schedule.every().day.at(t).do(
            lambda: monitor.check_positions(telegram_bot.send)
        )
    schedule.every().day.at(ANALYSIS_TIME).do(
        lambda: analysis.morning_briefing(telegram_bot.send)
    )
    print(f"   Checks: {', '.join(CHECK_TIMES)} | Briefing: {ANALYSIS_TIME} (heure Paris)")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    print("=" * 40)
    print("  TradingBot")
    print("=" * 40)
    print(f"  Telegram : {'OK' if TELEGRAM_TOKEN else 'MANQUANT (.env)'}")
    print(f"  AI       : {AI_PROVIDER}")

    import portfolio
    data = portfolio.load()
    positions = data.get("positions", {})
    print(f"  Cash     : {data.get('cash_available', 0)}€")
    print(f"  Positions: {list(positions.keys()) or 'aucune'}")
    print("=" * 40)

    telegram_bot.start_polling()
    run_scheduler()
