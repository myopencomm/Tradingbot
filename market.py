"""
Places de marché — LA source unique sur « où se traite ce ticker ».

Avant ce module, la question avait CINQ réponses indépendantes :
  · `config._suffix` + `CURRENCY_BY_SUFFIX`   (devise, pour les frais)
  · `monitor._is_us`                          (`"." not in ticker`)
  · `autonomous_engine.market_open_for`       (heures d'ouverture)
  · `portfolio.market_close_expiry`           (heure de clôture)
  · `sync_engine.MIC_MARKETS`                 (MIC BD → suffixe + devise)

Cette dispersion a déjà coûté : le défaut « place inconnue → .PA » a enregistré
NVDA en **NVDA.PA**, ticker inexistant chez Yahoo — position de 1 233 €
invisible du suivi SL/TP et annoncée « COURS SUSPENDU » (03/08/2026). Le
correctif d'alors a fusionné deux tables dans `sync_engine` ; les trois autres
définitions vivaient toujours ailleurs.

CONVENTION DU PROJET : le ticker est un ticker **Yahoo**. Le suffixe désigne la
place, et l'absence de suffixe désigne les États-Unis (`NVDA`, `BAC`).

Ce module est une FEUILLE : il n'importe aucun autre module du projet. C'est ce
qui lui permet d'être appelé partout sans créer de cycle d'import.
"""
from datetime import datetime, timedelta

import pytz

PARIS = pytz.timezone("Europe/Paris")


# ─── Table unique des places ────────────────────────────────────────────────
# Une place = une ligne qui porte TOUT ce qu'on sait d'elle. Les informations
# ne peuvent plus se contredire, puisqu'elles ne peuvent plus être écrites à
# deux endroits différents.
#
#   suffixe Yahoo : (devise, ouverture, dernier_ordre, clôture, libellé)
# Horaires en minutes depuis minuit, HEURE DE PARIS — c'est l'heure du bot.
#
# ⚠️ `dernier_ordre` et `clôture` sont DEUX nombres distincts, et c'est
# volontaire — la centralisation a justement révélé qu'ils divergeaient :
#   · `market_open_for` (passage d'ordre) fermait Euronext à 17h25 : une marge
#     avant la clôture réelle, pour ne pas poster un ordre qui n'aurait plus le
#     temps d'être servi ;
#   · `market_close_expiry` (péremption d'une validation IA) utilisait 17h30,
#     la fin de séance réelle.
# Les fondre en un seul chiffre aurait changé un comportement sans le vouloir.
_MARKETS = {
    #        devise  ouverture      dernier ordre  clôture        libellé
    "":     ("USD", 15 * 60 + 35, 21 * 60 + 55, 21 * 60 + 55, "NYSE / NASDAQ"),
    ".PA":  ("EUR",  9 * 60 +  5, 17 * 60 + 25, 17 * 60 + 30, "Euronext Paris"),
    ".AS":  ("EUR",  9 * 60 +  5, 17 * 60 + 25, 17 * 60 + 30, "Euronext Amsterdam"),
    ".BR":  ("EUR",  9 * 60 +  5, 17 * 60 + 25, 17 * 60 + 30, "Euronext Bruxelles"),
    ".LS":  ("EUR",  9 * 60 +  5, 17 * 60 + 25, 17 * 60 + 30, "Euronext Lisbonne"),
    ".DE":  ("EUR",  9 * 60 +  5, 17 * 60 + 25, 17 * 60 + 30, "Xetra"),
    ".MI":  ("EUR",  9 * 60 +  5, 17 * 60 + 25, 17 * 60 + 30, "Borsa Italiana"),
    ".MC":  ("EUR",  9 * 60 +  5, 17 * 60 + 25, 17 * 60 + 30, "Madrid"),
    ".L":   ("GBP",  9 * 60 +  5, 17 * 60 + 25, 17 * 60 + 30, "London Stock Exchange"),
    ".SW":  ("CHF",  9 * 60 +  5, 17 * 60 + 25, 17 * 60 + 30, "SIX Suisse"),
}

DEFAULT_MARKET = ".PA"          # place supposée quand rien ne permet de trancher

# Places au tarif de courtage Euronext (voir config.brokerage_fee).
EURONEXT_SUFFIXES = (".PA", ".AS", ".BR")

# Places où BD accepte la validité « end_of_year ».
EURONEXT_MICS = {"XPAR", "XAMS", "XBRU", "XLIS"}


# ─── MIC de Bourse Direct → suffixe Yahoo ───────────────────────────────────
# BD identifie la place par son code MIC. `XNGS` (NASDAQ Global Select) est
# celui qu'il renvoie réellement pour NVDA — il manquait dans la table des
# suffixes, d'où le défaut « .PA » et l'incident NVDA.PA.
MIC_SUFFIX = {
    "XPAR": ".PA", "XAMS": ".AS", "XBRU": ".BR", "XLIS": ".LS",
    "XETR": ".DE", "XMIL": ".MI", "XMAD": ".MC", "XLON": ".L", "XSWX": ".SW",
    # US — aucun suffixe chez Yahoo, quel que soit le compartiment.
    "XNYS": "", "XNAS": "", "XNGS": "", "XNMS": "", "XNCM": "",
    "ARCX": "", "XASE": "", "BATS": "",
}


def suffix(ticker: str) -> str:
    """Suffixe Yahoo du ticker. '' = États-Unis (convention du projet)."""
    t = (ticker or "").strip().upper()
    return t[t.rindex("."):] if "." in t else ""


def base(ticker: str) -> str:
    """Mnémonique sans suffixe : 'AIR.PA' → 'AIR', 'NVDA' → 'NVDA'."""
    return (ticker or "").strip().upper().split(".")[0]


def _market(ticker: str):
    return _MARKETS.get(suffix(ticker)) or _MARKETS[DEFAULT_MARKET]


def currency(ticker: str) -> str:
    """Devise de COTATION d'après la place. Ne fait aucun appel réseau.

    (`prices._ticker_currency` interroge Yahoo pour la même information : il
    reste la référence quand le ticker est douteux, celle-ci suffit partout
    ailleurs et ne peut pas échouer.)
    """
    return _market(ticker)[0]


def symbol(currency_code: str) -> str:  # noqa: D401
    """Symbole d'affichage d'une devise."""
    return {"USD": "$", "GBP": "£", "JPY": "¥", "CHF": "CHF"}.get(
        (currency_code or "").upper(), "€")


def is_us(ticker: str) -> bool:
    return suffix(ticker) == ""


def is_euronext(ticker: str) -> bool:
    return suffix(ticker) in EURONEXT_SUFFIXES


def is_foreign_currency(ticker: str) -> bool:
    """Ordre libellé en devise → commission de change côté BD."""
    return currency(ticker) != "EUR"


def label(ticker: str) -> str:
    return _market(ticker)[4]


def is_open_now(ticker: str, now: datetime | None = None) -> bool:
    """Peut-on encore passer un ordre sur le marché DU TICKER (heure de Paris) ?

    Se ferme quelques minutes AVANT la clôture réelle : un ordre posté dans les
    dernières secondes n'a plus le temps d'être servi.

    Sans gestion des jours fériés locaux : BD rejette alors l'ordre et le bot
    réessaie au cycle suivant — l'opportunité reste en attente.
    """
    now = now or datetime.now(PARIS)
    if now.weekday() >= 5:
        return False
    _cur, open_min, last_order, _close, _lbl = _market(ticker)
    return open_min <= now.hour * 60 + now.minute <= last_order


def any_market_open(now: datetime | None = None) -> bool:
    """Au moins un marché tradable est ouvert (Euronext OU US)."""
    return is_open_now("XX.PA", now) or is_open_now("NVDA", now)


def close_time_today(ticker: str, now: datetime | None = None) -> datetime:
    """Clôture du marché du titre aujourd'hui. Si elle est déjà passée :
    9h00 le lendemain — fenêtre minimale avant re-validation.

    Sert d'expiration aux opportunités validées : pas question d'agir sur une
    validation du matin le lendemain sans la refaire.
    """
    now = now or datetime.now(PARIS)
    _cur, _open, _last, close_min, _lbl = _market(ticker)
    expires = now.replace(hour=close_min // 60, minute=close_min % 60,
                          second=0, microsecond=0)
    if now >= expires:
        expires = (now + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0)
    return expires


def yf_ticker(bd_ticker: str, mic: str, bd_currency: str = "",
              on_unknown=None) -> str:
    """Reconstruit le ticker Yahoo depuis le mnémo BD et la place (MIC).

    Une place inconnue ne tombe PAS silencieusement sur « .PA » — c'est ce
    défaut qui a produit NVDA.PA. Deux garde-fous avant de deviner :
      · la devise cotée par BD tranche le cas le plus fréquent (USD ⇒ US) ;
      · toute place non répertoriée est signalée via `on_unknown`, pour être
        ajoutée à MIC_SUFFIX plutôt que subie une seconde fois.
    """
    bd_ticker = (bd_ticker or "").strip().upper()
    if not bd_ticker:
        return ""
    mic = (mic or "").strip().upper()
    if mic in MIC_SUFFIX:
        return f"{bd_ticker}{MIC_SUFFIX[mic]}"

    guess = "" if (bd_currency or "").upper() == "USD" else DEFAULT_MARKET
    if on_unknown:
        on_unknown(f"place BD inconnue « {mic or '?'} » pour {bd_ticker} "
                   f"(devise {bd_currency or '?'}) → suffixe supposé "
                   f"« {guess or 'aucun (US)'} ». À ajouter dans MIC_SUFFIX.")
    return f"{bd_ticker}{guess}"
