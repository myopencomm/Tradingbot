"""
La surface des commandes Telegram — table UNIQUE.

Avant ce module, la même liste vivait à cinq endroits : le dispatch
`COMMANDS`, le menu `BOT_COMMANDS`, le texte de `/help`, le guide `/tuto` et
les tableaux du README. Il fallait les tenir à la main, et ça n'a pas tenu :
au 11/08/2026, **5 commandes manquaient au menu et 9 à `/help`** — dont
`/dashboard`, `/lessons`, `/reticker`, `/fallback` et `/scan_us`, toutes
utilisables et documentées nulle part.

Ici, une commande est déclarée UNE fois. Le dispatch, le menu Telegram et le
texte de `/help` en sont dérivés, et un test échoue si l'un d'eux s'en écarte.

Ce module ne contient QUE des données : il n'importe pas `telegram_bot` (qui
l'importe), donc aucun cycle. Le champ `handler` est le NOM de la fonction, que
`telegram_bot` résout chez lui.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str                       # sans le « / »
    handler: str                    # nom de la fonction dans telegram_bot
    menu: str = ""                  # description du menu Telegram ('' = hors menu)
    section: str = ""               # section de /help ('' = absent de /help)
    usage: str = ""                 # arguments, affichés après le nom
    help: str = ""                  # description dans /help (défaut : `menu`)
    extra: tuple = ()               # lignes supplémentaires sous la commande
    args: tuple = ()                # arguments figés (alias, ex. /scan_us → us)
    playwright: bool = False        # exige la connexion Bourse Direct

    @property
    def slash(self) -> str:
        return f"/{self.name}"

    def help_line(self) -> str:
        tete = f"{self.slash} {self.usage}".rstrip()
        return f"{tete} — {self.help or self.menu}"


# ─── Sections de /help, dans l'ordre d'affichage ────────────────────────────
# `intro` / `outro` portent la prose qui ne se déduit d'aucune commande (les
# deux façons de passer un ordre, la syntaxe de /ordre, la gestion en
# terminal). Les LIGNES de commandes, elles, sont générées.
SECTIONS = [
    ("portefeuille", "VOIR MON PORTEFEUILLE", "", ""),
    ("positions",    "GERER MES POSITIONS (dans le bot)", "", ""),
    ("ia",           "ANALYSE IA", "", ""),
    ("vente",        "VENDRE / CLOTURER", "", ""),
    ("classic",      "━━━ 2 FACONS DE PASSER UN ORDRE ━━━\n\n"
                     "A) MODE CLASSIC — le bot ecrit les instructions,\n"
                     "   TU les saisis toi-meme sur Bourse Direct :", "", ""),
    ("playwright",   "B) MODE PLAYWRIGHT — le bot passe l'ordre\n"
                     "   REELLEMENT sur Bourse Direct pour toi :", "",
                     "/ordre acheter TICKER QTE marche [validite]\n"
                     "/ordre acheter TICKER QTE limite PRIX [validite]\n"
                     "/ordre acheter TICKER QTE expert ENTREE SL TP [validite]\n"
                     "/ordre vendre TICKER QTE marche [validite]\n"
                     "/ordre vendre TICKER QTE limite PRIX [validite]\n"
                     "/ordre vendre TICKER QTE expert SL TP [validite]\n"
                     "  validite : seance | max (defaut) | JJ/MM/AAAA\n"
                     "  /oui confirme et envoie  |  /non annule"),
    ("auto",         "MODE AUTONOME (Playwright requis)", "",
                     "  Le bot scanne, entre, gere SL/TP, releve\n"
                     "  le SL au PRU tout seul"),
    ("mail",         "DETECTION AUTO DES VENTES", "",
                     "  (utile si tu n'utilises PAS le mode Playwright)"),
    ("import",       "IMPORT",
                     "Envoie une photo de l'app BD → import auto (vision IA)", ""),
    ("suivi",        "SUIVI ET REGLAGES", "", ""),
    ("aide",         "AIDE", "",
                     "\nGESTION DU BOT (terminal)\n"
                     "./bot.sh start|stop|restart|status|test|logs\n"
                     "./bot.sh update — maj en 1 commande\n"
                     "./bot.sh autostart — relance auto au boot"),
]


# ─── LA table ───────────────────────────────────────────────────────────────
# Ordre = ordre du menu Telegram. `section` = ordre dans /help.
ALL = [
    # ── Portefeuille ──
    Command("status", "cmd_status", "Voir mon portefeuille", "portefeuille",
            help="positions + P&L en temps reel"),
    Command("cash", "cmd_cash", "Cash dispo  |  /cash 1234 le definir", "portefeuille",
            help="cash dispo  |  /cash 1234 — le definir"),
    Command("stats", "cmd_stats", "Bilan : win rate, P&L, profit factor", "portefeuille",
            help="bilan (win rate, P&L, profit factor)"),
    Command("dashboard", "cmd_dashboard", "Graphique P&L + resume visuel", "portefeuille",
            help="graphique P&L + resume visuel (lien web)"),
    Command("nav", "cmd_nav", "Croissance de l'investissement (valeur de la part)",
            "portefeuille", usage="[depot|retrait MONTANT]",
            help="croissance en % de l'investissement, base 100",
            extra=("   apres un virement : /nav depot 1000 (ou /nav retrait 500)\n"
                   "   sinon le versement serait compte comme une performance",)),

    # ── Positions ──
    Command("add", "cmd_add", "Acheter (deduit le cash) — TICKER QTE PRU SL TP", "positions",
            usage="TICKER QTE PRU SL TP", help="acheter (deduit le cash)"),
    Command("remove", "cmd_remove", "Retirer une position — /remove TICKER", "positions",
            usage="TICKER", help="retirer une position"),
    Command("hold", "cmd_hold", "HOLD long terme, hors gestion bot — /hold TICKER [off]", "positions",
            usage="TICKER [off]", help="HOLD long terme, hors gestion bot"),
    Command("sl", "cmd_sl", "Changer le stop-loss — /sl TICKER PRIX", "positions",
            usage="TICKER PRIX", help="changer le stop-loss"),
    Command("tp", "cmd_tp", "Changer le take-profit — /tp TICKER PRIX", "positions",
            usage="TICKER PRIX", help="changer le take-profit"),
    Command("reticker", "cmd_reticker",
            "Corriger le ticker Yahoo d'une position — /reticker POSITION TICKER", "positions",
            usage="POSITION TICKER", help="corriger le ticker Yahoo d'une position"),

    # ── Analyse IA ──
    Command("morning", "cmd_morning", "Briefing du jour (macro + positions + opps)", "ia",
            help="briefing du jour (macro + positions + opps)"),
    Command("scan", "cmd_scan", "Meilleures opportunites avec mon cash", "ia",
            help="meilleures opportunites avec ton cash"),
    Command("scan_us", "cmd_scan", "Scan des valeurs US uniquement (/scan us)", "ia",
            help="valeurs US uniquement (seance 15h35-22h)", args=("us",)),
    Command("research", "cmd_research", "Analyser une action — /research TICKER", "ia",
            usage="TICKER [question]", help="analyse d'une action",
            extra=("  ex: /research EXENS.PA dois-je vendre ?",)),
    Command("lessons", "cmd_lessons", "Ce que le bot a appris de ses trades", "ia",
            help="ce que le bot a appris de ses trades passes"),

    # ── Vente ──
    Command("vendu", "cmd_vendu", "Enregistrer une vente — /vendu NOM [PRIX]", "vente",
            usage="NOM [PRIX]", help="enregistre une vente (prix TP si omis)"),
    Command("close", "cmd_close", "Vente avec frais — TICKER QTE PRIX [FRAIS]", "vente",
            usage="TICKER QTE PRIX [FRAIS]", help="vente avec frais"),

    # ── Mode Classic ──
    Command("setup", "cmd_setup", "Texte ordres protection SL+TP — TICKER QTE PRU", "classic",
            usage="TICKER QTE PRU", help="apres un achat deja fait",
            extra=("  → texte des 2 ordres protection (SL -{SL}% + TP +{TP}%)",)),
    Command("buy", "cmd_buy", "Texte ordre Expert achat+SL+TP — TICKER QTE PRU", "classic",
            usage="TICKER QTE PRU", help="avant l'achat",
            extra=("  → texte d'1 ordre Expert (achat+SL+TP groupes)",)),
    Command("order", "cmd_order", "1 ordre simple (texte) — buy|sell TICKER QTE PRIX", "classic",
            usage="buy|sell TICKER QTE PRIX", help="1 ordre simple"),
    Command("attente", "cmd_attente", "Ordre en attente, alerte au cours — NOM TICKER QTE PRIX", "classic",
            usage="NOM TICKER QTE PRIX [SL TP]", help="ordre en attente",
            extra=("  → reserve le cash, t'alerte quand le cours est atteint",)),
    Command("annuler", "cmd_annuler", "Annuler un ordre en attente (bot) — /annuler NOM", "classic",
            usage="NOM", help="annule un ordre en attente (bot)"),

    # ── Mode Playwright ──
    Command("connect", "cmd_connect", "Se connecter a Bourse Direct (code TOTP)", "playwright",
            help="se connecter a BD (code TOTP)"),
    Command("sync", "cmd_sync", "Lire portefeuille + ordres reels depuis BD", "playwright",
            help="lire portefeuille + ordres reels depuis BD", playwright=True),
    Command("trailing", "cmd_trailing", "Verifier le trailing stop (SL au PRU) maintenant", "playwright",
            help="verifier le trailing stop maintenant", playwright=True),
    Command("stagnation", "cmd_stagnation", "Vitesse des positions (constat, aucune vente)",
            "playwright", help="quelles positions trainent (constat seul)", playwright=True),
    Command("ordre", "cmd_ordre", "Passer un ordre reel sur BD — acheter|vendre TICKER QTE ...",
            "playwright", help="passer un ordre REEL sur BD (syntaxe ci-dessous)",
            playwright=True),
    Command("annuler_bd", "cmd_annuler_bd", "Annuler un ordre en cours sur BD — /annuler_bd TICKER",
            "playwright", usage="TICKER", help="annule un ordre en cours sur BD", playwright=True),
    Command("mode", "cmd_mode", "Etat connexion BD", "playwright",
            help="etat connexion  |  /disconnect — repasser Classic"),
    Command("disconnect", "cmd_disconnect", "Repasser en mode Classic"),

    # ── Mode autonome ──
    Command("auto", "cmd_auto", "Mode autonome — /auto on 500 | off | status", "auto",
            usage="on 500", help="active avec 500€ de budget",
            extra=("/auto positions 3 — nb max de positions simultanees",
                   "/auto on 20%   — active avec 20% du cash",
                   "/auto off      — desactive",
                   "/auto status   — etat + positions autonomes")),

    # ── Mail ──
    Command("syncmail", "cmd_syncmail", "Detecter les ventes via emails BD", "mail",
            help="lit les emails BD 'strategie finalisee'"),

    # ── Import ──
    Command("import", "cmd_import", "Guide import CSV", "import",
            help="guide import CSV"),

    # ── Suivi et réglages ──
    Command("fallback", "cmd_fallback", "IA de secours — /fallback gemini CLE_API", "suivi",
            usage="gemini CLE_API", help="configure une IA de secours"),
    Command("capture", "cmd_capture", "Capturer les appels reseau BD (diagnostic)", "suivi",
            help="capture les appels reseau BD (diagnostic d'un ordre qui echoue)",
            playwright=True),
    Command("testordre", "cmd_testordre", "Tester un ordre sans l'envoyer (diagnostic)", "suivi",
            usage="TICKER", help="teste la construction d'un ordre sans l'envoyer",
            playwright=True),

    # ── Aide ──
    Command("tuto", "cmd_tuto", "Guide pas a pas", "aide", help="guide pas a pas"),
    Command("update", "cmd_update", "Version du bot", "aide", help="version du bot"),
    Command("help", "cmd_help", "Liste complete des commandes", "aide",
            help="cette liste"),

    # ── Hors menu et hors /help : réponses à une confirmation, ou point
    #    d'entrée automatique de Telegram. Les lister ajouterait du bruit
    #    sans rien apprendre.
    Command("oui", "cmd_oui"),
    Command("non", "cmd_non"),
    Command("start", "cmd_start"),
]


BY_NAME = {c.name: c for c in ALL}


def menu() -> list[tuple[str, str]]:
    """Menu Telegram (`setMyCommands`) : (nom, description)."""
    return [(c.name, c.menu) for c in ALL if c.menu]


def help_text(sl_pct: float, tp_pct: float) -> str:
    """Texte de `/help`, GÉNÉRÉ depuis la table — il ne peut plus dériver."""
    out = ["TradingBot — Aide", "━" * 36, ""]
    for key, titre, intro, outro in SECTIONS:
        cmds = [c for c in ALL if c.section == key]
        if not cmds and not intro:
            continue
        out.append(titre)
        if intro:
            out.append(intro)
        for c in cmds:
            out.append(c.help_line())
            out.extend(c.extra)
        if outro:
            out.append(outro)
        out.append("")
    out.append("━" * 36)
    return ("\n".join(out)
            .replace("{SL}", f"{sl_pct:.0f}")
            .replace("{TP}", f"{tp_pct:.0f}"))
