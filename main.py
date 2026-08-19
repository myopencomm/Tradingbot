import schedule
import threading
import time
from datetime import datetime
from config import (CHECK_TIMES, ANALYSIS_TIME, TELEGRAM_TOKEN, AI_PROVIDER,
                    GMAIL_USER, GMAIL_APP_PASSWORD,
                    US_EXTENDED_HOURS, US_CHECK_TIMES, US_SCAN_TIME)
import monitor
import analysis
import telegram_bot
import gmail_sync


def _market_day() -> bool:
    """Vrai si aujourd'hui est un jour de semaine (lundi–vendredi)."""
    return datetime.now().weekday() < 5  # 0=lundi … 4=vendredi


# ─── Délais maximum par job ─────────────────────────────────────────────────
# Un seuil unique de 240 s était calibré sur le chemin COURT : quand toutes les
# places sont prises, le briefing saute la recherche de candidats et tient en un
# appel IA. Dès qu'une place se libère il fait le chemin complet — screen quant
# sur tout l'univers, puis pour chacun des 6 finalistes : recherche web,
# graphique chandeliers, analyse vision, validation, soit ~24 appels IA. Ce
# matin-là (13/08/2026, après la sortie de NVDA au TP) il a dépassé les 240 s
# alors que RIEN n'était bloqué — il a terminé deux minutes plus tard.
#
# Historique des dépassements : briefing ×2, us_scan ×2, check ×1 — tous sur des
# jobs qui appellent l'IA en boucle. Le budget colle donc à ce que le job fait,
# pas à une moyenne.
JOB_TIMEOUTS = {
    "briefing":         900,    # chemin complet : screen + 6 validations IA
    "us_scan":          900,    # même travail sur l'univers US
    "weekly_swap":      900,    # compare chaque position à des candidats
    "monthly_breach":   600,
    "universe_refresh": 2400,   # ~2500 valeurs, 2 ans d'historique
}
JOB_TIMEOUT_DEFAUT = 240        # checks SL/TP, sync BD : pas d'IA, quelques secondes

# Délai de grâce accordé APRÈS l'alerte avant de conclure au vrai blocage. Un
# job qui dépasse son budget est suspect ; un job qui dépasse budget + grâce ne
# répond plus.
JOB_GRACE_S = 600


def _bounded(fn, name, timeout=None):
    """
    Enrobe fn pour l'exécuter dans un thread dédié avec délai maximum : un job
    planifié qui bloque (ex: appel réseau sans timeout côté yfinance/Gmail) ne
    doit jamais geler le thread scheduler, sous peine d'arrêter TOUTE la
    surveillance (positions, ordres, briefing) sans aucun crash ni redémarrage
    visible. Incident du 21-23/07/2026 : un scan bloqué à 21h40 a arrêté tout
    le scheduler pendant ~36h, sans aucun message d'erreur (keepalive et
    polling Telegram, sur d'autres threads, ont continué à tourner
    normalement — rien ne laissait deviner que le bot était figé).

    Le délai dépassé n'est PAS une preuve de blocage : le scheduler est libéré,
    mais le thread continue. On le surveille donc jusqu'au bout et on annonce
    l'issue — « finalement terminé en Xs » ou « toujours en cours ». Sans ce
    retour, l'alerte restait un mensonge dans l'historique : le 13/08 elle
    affirmait « celui-ci n'a pas terminé » à propos d'un briefing qui a terminé.
    """
    budget = timeout if timeout is not None else JOB_TIMEOUTS.get(name, JOB_TIMEOUT_DEFAUT)

    def wrapped(*args, **kwargs):
        debut = time.time()

        def worker():
            try:
                fn(*args, **kwargs)
            except Exception as e:
                print(f"[job:{name}] erreur : {e}")

        t = threading.Thread(target=worker, daemon=True, name=f"job-{name}")
        t.start()
        t.join(timeout=budget)
        if not t.is_alive():
            return

        print(f"[job:{name}] ⚠️ dépasse {budget}s — scheduler libéré, "
              f"le thread continue et sera suivi")
        telegram_bot.send(
            f"⚠️ Job planifié « {name} » dépasse {budget}s.\n"
            f"Le scheduler est libéré (les prochains jobs tournent normalement) "
            f"et celui-ci continue en arrière-plan — je te dis ce qu'il devient."
        )

        # Suivi jusqu'à l'issue, sur un thread à part : le scheduler, lui, est
        # déjà reparti.
        def surveiller():
            t.join(timeout=max(budget, JOB_GRACE_S))
            écoulé = int(time.time() - debut)
            if t.is_alive():
                print(f"[job:{name}] toujours en cours après {écoulé}s")
                telegram_bot.send(
                    f"🔴 « {name} » toujours en cours après {écoulé}s — cette "
                    f"fois c'est probablement un appel réseau sans réponse. "
                    f"Le reste du bot n'est pas affecté."
                )
            else:
                print(f"[job:{name}] finalement terminé en {écoulé}s")
                telegram_bot.send(
                    f"✅ « {name} » a finalement terminé en {écoulé}s — "
                    f"lent, pas bloqué. Rien n'a été perdu."
                )

        threading.Thread(target=surveiller, daemon=True,
                         name=f"watch-{name}").start()

    return wrapped


def _releve_nav():
    """Relevé quotidien de la valeur liquidative du fonds « bot »."""
    try:
        import nav
        p = nav.relever(send_fn=telegram_bot.send)
        print(f"[nav] part {p['part']} — fonds {p['valeur']}€")
    except Exception as e:
        print(f"[nav] relevé impossible : {e}")


def _refresh_market_universe():
    """Reconstruit l'univers US investissable (liste officielle Nasdaq Trader
    → filtre de liquidité → indicateurs), mis en cache pour le scan.

    Silencieux sauf échec : c'est de la maintenance, pas une décision de
    trading. Le scan retombe seul sur la liste manuelle si le cache manque.
    """
    try:
        import market_universe
        r = market_universe.refresh_us()
        print(f"[universe] rafraîchi : {r}")
    except Exception as e:
        print(f"[universe] échec du rafraîchissement : {e}")
        telegram_bot.send(
            f"⚠️ Rafraîchissement de l'univers de marché échoué : {e}\n"
            f"Le scan continue sur la liste manuelle (149 valeurs)."
        )


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
    # Ordres d'entrée autonomes périmés (non exécutés après la clôture de leur
    # marché) : annulation sur BD — un limite qui traîne ne se remplit que
    # quand le momentum s'est retourné contre nous.
    try:
        import autonomous_engine
        autonomous_engine.cancel_stale_entry_orders(telegram_bot.send)
    except Exception as e:
        print(f"[hourly cancel stale] {e}")
    # Trailing stop : remonte les SL au PRU sur BD (positions auto +3%,
    # manuelles +5%) — uniquement celles protégées par un Expert actif.
    try:
        import autonomous_engine
        autonomous_engine.trailing_stop_cycle(telegram_bot.send)
    except Exception as e:
        print(f"[hourly trailing] {e}")
    # Cycle d'entrée : tente les opportunités en attente dont le marché vient
    # d'ouvrir (ex: US validées le matin, achetables dès 15h35 Paris). Chaque
    # opportunité n'est évaluée qu'une fois (achetée ou retirée après veto).
    try:
        import autonomous_engine
        autonomous_engine.run_entry_cycle(telegram_bot.send)
    except Exception as e:
        print(f"[hourly entry] {e}")


def _auto_gmail_check():
    if not _market_day() or not GMAIL_USER or not GMAIL_APP_PASSWORD:
        return
    notifications = gmail_sync.check_and_notify(GMAIL_USER, GMAIL_APP_PASSWORD)
    if not notifications:
        return
    # Un mail BD "stratégie finalisée" = un ordre exécuté (SL/TP touché).
    # Si Playwright est connecté, on SYNCHRONISE directement : le sync lit
    # l'ordre exécuté sur BD, clôture la position au prix réel et met tout à
    # jour tout seul (aucun /vendu manuel). Sinon, on retombe sur le message
    # d'invite manuel.
    import bot_mode
    import playwright_session
    if bot_mode.is_playwright() and playwright_session.is_connected():
        try:
            import sync_engine
            playwright_session.run(
                lambda page: sync_engine.sync(page, telegram_bot.send, silent=True),
                timeout=90,
            )
            return
        except Exception as e:
            print(f"[gmail→sync] {e}")
    for msg in gmail_sync.format_notifications(notifications):
        telegram_bot.send(msg)


def run_scheduler():
    for t in CHECK_TIMES:
        schedule.every().day.at(t).do(
            _bounded(lambda: (
                _auto_gmail_check(),
                monitor.check_pending_orders(telegram_bot.send),
                monitor.check_positions(telegram_bot.send),
            ) if _market_day() else None, f"check_{t}")
        )
    schedule.every().day.at(ANALYSIS_TIME).do(
        _bounded(lambda: analysis.morning_briefing(telegram_bot.send) if _market_day() else None,
                 "briefing")
    )
    schedule.every().monday.at("09:10").do(
        _bounded(lambda: analysis.weekly_swap_analysis(telegram_bot.send), "weekly_swap")
    )
    schedule.every().day.at("09:15").do(
        _bounded(lambda: analysis.monthly_breach_review(telegram_bot.send)
                 if _market_day() and datetime.now().day == 1 else None, "monthly_breach")
    )
    schedule.every().monday.at("09:20").do(_bounded(_weekly_version_check, "version_check"))
    # Univers de marché : rafraîchi le week-end, marchés fermés. JAMAIS à la
    # demande — un passage complet (~5000 symboles) fait rate-limiter yfinance,
    # ce qui dégraderait les cours du scan et du suivi de positions.
    # Son budget (large : ~4 min mesurés, mais 2 ans d'historique sur ~2500
    # valeurs) est déclaré avec les autres dans JOB_TIMEOUTS.
    schedule.every().sunday.at("08:00").do(
        _bounded(_refresh_market_universe, "universe_refresh")
    )
    schedule.every().hour.at(":35").do(_bounded(_hourly_bd_sync, "hourly_bd_sync"))
    # Valeur de part : un relevé par jour APRÈS la clôture US, quand toutes les
    # lignes ont un cours de fin de séance. Silencieux — c'est une mesure, pas
    # un événement ; il ne parle que s'il flaire un mouvement d'espèces non
    # déclaré (voir nav.relever).
    schedule.every().day.at("22:15").do(
        _bounded(lambda: _releve_nav() if _market_day() else None, "nav"))

    # Séance US : les 4 CHECK_TIMES s'arrêtent à 17:00, mais Wall Street tourne
    # jusqu'à 22:00 Paris. On prolonge la surveillance (positions/ordres US
    # uniquement, alertes seules) et on lance un scan US en début de séance.
    if US_EXTENDED_HOURS:
        for t in US_CHECK_TIMES:
            schedule.every().day.at(t).do(
                _bounded(lambda: (
                    monitor.check_pending_orders(telegram_bot.send, us_only=True),
                    monitor.check_positions(telegram_bot.send, us_only=True),
                ) if _market_day() else None, f"us_check_{t}")
            )
        if US_SCAN_TIME:
            schedule.every().day.at(US_SCAN_TIME).do(
                _bounded(lambda: analysis.scan_us_opportunities(telegram_bot.send)
                         if _market_day() else None, "us_scan")
            )

    us_sched = (f" | US checks: {', '.join(US_CHECK_TIMES)}"
                + (f" | Scan US: {US_SCAN_TIME}" if US_SCAN_TIME else "")
                if US_EXTENDED_HOURS else "")
    print(f"   Checks: {', '.join(CHECK_TIMES)} | Briefing: {ANALYSIS_TIME} | Swap: lundi 09:10 | Revue SL: 1er du mois 09:15 | Version: lundi 09:20 | Sync BD silencieux: toutes les heures à :35{us_sched} (heure Paris)")
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

    import dashboard
    dashboard.start_server()

    # Adresse d'accès : recalculée au démarrage et ANNONCÉE si elle a bougé.
    # Tailscale crée un nœud dupliqué à chaque réinstallation ou mise à jour
    # (`yok` → `yok-2` → `yok-3`) et l'IP du tailnet suit — un lien noté quelque
    # part devient faux sans prévenir. Le bot est le seul à savoir où il est
    # joignable : c'est donc à lui de le dire.
    try:
        _urls, _changed = dashboard.refresh_link_file()
        for _lbl, _u in _urls:
            print(f"   Dashboard {_lbl} : {_u}")
        if _changed and _urls:
            telegram_bot.send(
                "🔗 ADRESSE DU DASHBOARD MODIFIÉE\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(f"{l} : {u}" for l, u in _urls)
                + "\n\n(Tailscale a renommé ou ré-adressé la machine. "
                  "/dashboard redonne toujours le lien à jour.)"
            )
    except Exception as _de:
        print(f"[dashboard] lien : {_de}")

    telegram_bot.start_polling()

    nb = len(positions)
    cash = data.get("cash_available", 0)
    telegram_bot.send(
        f"TradingBot en ligne 🤖\n"
        f"{nb} position{'s' if nb > 1 else ''} | {cash}€ cash"
    )

    run_scheduler()
