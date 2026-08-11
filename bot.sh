#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# TradingBot — script de gestion
#
#   ./bot.sh start        Démarre le bot en arrière-plan
#   ./bot.sh stop         Arrête le bot
#   ./bot.sh restart      Redémarre le bot
#   ./bot.sh status       Vérifie si le bot tourne
#   ./bot.sh logs         Affiche les logs en direct (Ctrl+C pour quitter)
#   ./bot.sh update       git pull + dépendances + redémarrage
#   ./bot.sh autostart    Lance le bot automatiquement au démarrage de l'ordi
#                         (macOS : launchd | Linux : systemd) + relance si crash
#   ./bot.sh unautostart  Désactive le démarrage automatique
# ─────────────────────────────────────────────────────────────────────────────
set -u
cd "$(dirname "$0")"
DIR="$(pwd)"
PYTHON="$DIR/venv/bin/python3"
LOG="$DIR/tradingbot.log"
LABEL="com.tradingbot.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/tradingbot.service"

# macOS résout le symlink du venv dans la cmdline (".../Python main.py"),
# donc on matche "python* main.py" plutôt que "venv/bin/python3 main.py".
PROC_PATTERN='[Pp]ython[^ ]* main\.py'

_pid() { pgrep -f "$PROC_PATTERN" 2>/dev/null | head -1; }

# Autostart « installé » = le fichier launchd/systemd existe.
# (launchctl list ne suffit pas : un agent déchargé via stop se recharge au boot.)
_autostart_active() {
    if [ "$(uname)" = "Darwin" ]; then
        [ -f "$PLIST" ]
    else
        [ -f "$UNIT" ]
    fi
}

do_start() {
    if [ -n "$(_pid)" ]; then
        echo "✅ Le bot tourne déjà (PID $(_pid))."
        return 0
    fi
    # Sécurité : restreint les permissions des fichiers sensibles au seul
    # propriétaire (600) — ils contiennent secrets et données financières.
    for f in .env positions.json trades_history.json CLAUDE_TRADING_CONTEXT.md bot_state.json; do
        [ -f "$f" ] && chmod 600 "$f"
    done
    if _autostart_active; then
        echo "Démarrage via le service auto..."
        if [ "$(uname)" = "Darwin" ]; then
            launchctl kickstart "gui/$(id -u)/$LABEL" 2>/dev/null || launchctl load "$PLIST"
        else
            systemctl --user start tradingbot
        fi
    else
        PYTHONUNBUFFERED=1 nohup "$PYTHON" main.py > "$LOG" 2>&1 &
    fi
    sleep 3
    if [ -n "$(_pid)" ]; then
        echo "✅ Bot démarré (PID $(_pid)). Dernières lignes :"
        tail -3 "$LOG"
    else
        echo "❌ Échec du démarrage. Voir les logs :"
        tail -10 "$LOG"
        return 1
    fi
}

do_stop() {
    if [ -z "$(_pid)" ]; then
        echo "Le bot ne tourne pas."
        return 0
    fi
    if _autostart_active; then
        # Sans ça, launchd/systemd relancerait le bot immédiatement
        if [ "$(uname)" = "Darwin" ]; then
            launchctl unload "$PLIST" 2>/dev/null
        else
            systemctl --user stop tradingbot
        fi
        sleep 1
    fi
    pkill -f "$PROC_PATTERN" 2>/dev/null
    sleep 1
    if [ -z "$(_pid)" ]; then
        echo "🛑 Bot arrêté."
        if _autostart_active; then
            echo "   (autostart toujours actif — il redémarrera au prochain boot)"
        fi
    else
        echo "⚠️ Le bot tourne encore (PID $(_pid)). Essayez : kill -9 $(_pid)"
        return 1
    fi
}

do_status() {
    if [ -n "$(_pid)" ]; then
        echo "✅ Bot en cours d'exécution (PID $(_pid))."
    else
        echo "🛑 Bot arrêté."
    fi
    if _autostart_active; then
        echo "🔄 Autostart : actif (démarre au boot, relance après crash)"
    else
        echo "   Autostart : inactif (./bot.sh autostart pour l'activer)"
    fi
    [ -f "$LOG" ] && { echo; echo "Dernières lignes du log :"; tail -3 "$LOG"; }
}

do_autostart() {
    if [ "$(uname)" = "Darwin" ]; then
        mkdir -p "$HOME/Library/LaunchAgents"
        cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>main.py</string>
    </array>
    <key>WorkingDirectory</key><string>$DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
    <key>EnvironmentVariables</key>
    <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict>
</plist>
EOF
        # Si le bot tourne déjà en manuel, on l'arrête : launchd prend le relais
        pkill -f "$PROC_PATTERN" 2>/dev/null && sleep 1
        launchctl unload "$PLIST" 2>/dev/null
        launchctl load "$PLIST"
        sleep 3
        if [ -n "$(_pid)" ]; then
            echo "✅ Autostart activé (launchd). Le bot tourne (PID $(_pid))"
            echo "   et redémarrera automatiquement au boot et après un crash."
            echo "   ⚠️ Le Mac doit rester allumé (pas en veille) pour les checks."
        else
            echo "❌ Problème au lancement via launchd. Voir : tail $LOG"
            return 1
        fi
    else
        mkdir -p "$UNIT_DIR"
        cat > "$UNIT" <<EOF
[Unit]
Description=TradingBot Telegram
After=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$PYTHON main.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:$LOG
StandardError=append:$LOG

[Install]
WantedBy=default.target
EOF
        pkill -f "$PROC_PATTERN" 2>/dev/null && sleep 1
        systemctl --user daemon-reload
        systemctl --user enable --now tradingbot
        sleep 3
        if [ -n "$(_pid)" ]; then
            echo "✅ Autostart activé (systemd). Le bot tourne (PID $(_pid))."
            echo "   Pour survivre à la déconnexion SSH : sudo loginctl enable-linger $USER"
        else
            echo "❌ Problème au lancement. Voir : systemctl --user status tradingbot"
            return 1
        fi
    fi
}

do_unautostart() {
    if [ "$(uname)" = "Darwin" ]; then
        if [ -f "$PLIST" ]; then
            launchctl unload "$PLIST" 2>/dev/null
            rm -f "$PLIST"
            echo "🛑 Autostart désactivé (bot arrêté). ./bot.sh start pour le relancer en manuel."
        else
            echo "Autostart non installé."
        fi
    else
        if [ -f "$UNIT" ]; then
            systemctl --user disable --now tradingbot 2>/dev/null
            rm -f "$UNIT"
            systemctl --user daemon-reload
            echo "🛑 Autostart désactivé (bot arrêté). ./bot.sh start pour le relancer en manuel."
        else
            echo "Autostart non installé."
        fi
    fi
}

do_update() {
    echo "Mise à jour depuis GitHub..."
    git pull origin main || { echo "❌ git pull a échoué."; return 1; }
    echo "Vérification des dépendances..."
    "$DIR/venv/bin/pip" install -q -r requirements.txt
    do_stop
    do_start
    echo "✅ Mise à jour terminée. /update dans Telegram pour vérifier la version."
}

do_test() {
    # Contrôle AVANT commit : compilation de tous les modules + tests de
    # caractérisation. Aucun accès réseau, Playwright ni Telegram — donc
    # exécutable à tout moment, bot en marche compris.
    cd "$DIR" || exit 1
    echo "── Compilation ────────────────────────────────────────────"
    "$DIR/venv/bin/python3" -m py_compile ./*.py || exit 1
    echo "✅ tous les modules compilent"
    echo "── Tests ──────────────────────────────────────────────────"
    "$DIR/venv/bin/python3" -m pytest tests/ -q || exit 1
}

case "${1:-}" in
    start)       do_start ;;
    stop)        do_stop ;;
    restart)     do_stop; do_start ;;
    status)      do_status ;;
    test)        do_test ;;
    logs)        tail -f "$LOG" ;;
    update)      do_update ;;
    autostart)   do_autostart ;;
    unautostart) do_unautostart ;;
    *)
        echo "Usage: ./bot.sh start|stop|restart|status|test|logs|update|autostart|unautostart"
        exit 1
        ;;
esac
