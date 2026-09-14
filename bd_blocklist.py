"""
Valeurs que Bourse Direct refuse de négocier — ne plus les représenter.

LE CAS FONDATEUR
----------------
14/09/2026, 21h08 : le scan US valide DSGN (Design Therapeutics), le contrôle
pré-achat confirme, l'ordre part... et BD répond

    HTTP 403 {"message": "Cette valeur n'est plus négociable sur les US et
                          CANADA"}

Rien de cassé côté bot : le courtier a retiré cette valeur de son périmètre.
Mais rien n'en gardait trace. Au scan suivant, DSGN remonte dans le classement
momentum exactement comme avant, repasse une validation IA complète, un
contrôle pré-achat, et se refait refuser. Le coût se paie à chaque tour.

CE QUI DÉCLENCHE UN BLOCAGE — ET CE QUI N'EN DÉCLENCHE PAS
-----------------------------------------------------------
UNIQUEMENT un refus dont le message dit que la valeur n'est pas négociable. Un
403 peut aussi signifier une session expirée, un droit manquant, un incident
passager : bloquer sur le CODE seul mettrait au ban des titres parfaitement
bons au premier hoquet d'authentification. C'est le motif qui décide, pas le
statut HTTP.

POURQUOI UNE DURÉE ET PAS UN BANNISSEMENT
------------------------------------------
« N'est plus négociable » se veut définitif, mais c'est une décision de
courtier, pas une loi de la nature : une valeur réadmise resterait invisible
pour toujours. Le blocage expire donc au bout de `DUREE_JOURS`, et le titre
retente sa chance une fois. Le fichier est un cache local, pas une donnée de
portefeuille : il n'est pas versionné.
"""
import json
import os
from datetime import datetime, timedelta

FICHIER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "bd_blocklist.json")
DUREE_JOURS = 180

# Motifs de refus qui valent blocage. Comparés SANS accents ni casse : BD
# renvoie « négociable » accentué dans le JSON, et une comparaison naïve sur la
# chaîne accentuée casse au premier changement d'encodage.
MOTIFS = ("n'est plus negociable", "non negociable", "pas negociable")


def _sans_accents(txt: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", txt or "")
                   if unicodedata.category(c) != "Mn").lower()


def motif_de_blocage(message: str) -> str | None:
    """Ce message de refus BD justifie-t-il un blocage ? Retourne le message.

    Fonction PURE : c'est elle qui distingue « BD ne veut pas de ce titre » de
    « BD n'a pas voulu de cet ordre-là », et elle se teste sans fichier.
    """
    plat = _sans_accents(message)
    if any(m in plat for m in MOTIFS):
        return (message or "").strip()
    return None


def charger() -> dict:
    try:
        with open(FICHIER, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _sauver(data: dict) -> None:
    tmp = FICHIER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FICHIER)      # écriture atomique


def ajouter(ticker: str, message: str) -> bool:
    """Bloque `ticker` si le message le justifie. Retourne True si bloqué."""
    motif = motif_de_blocage(message)
    if not motif:
        return False
    data = charger()
    data[ticker.upper()] = {"motif": motif,
                            "depuis": datetime.now().strftime("%Y-%m-%d")}
    _sauver(data)
    print(f"[Blocklist BD] {ticker} bloqué {DUREE_JOURS} j — {motif}")
    return True


def raison(ticker: str, aujourd_hui: datetime | None = None) -> str | None:
    """Motif du blocage si `ticker` est bloqué et non périmé, sinon None."""
    rec = charger().get((ticker or "").upper())
    if not rec:
        return None
    try:
        depuis = datetime.strptime(rec["depuis"], "%Y-%m-%d")
    except (KeyError, ValueError):
        return rec.get("motif") or "bloqué"
    now = aujourd_hui or datetime.now()
    if now - depuis > timedelta(days=DUREE_JOURS):
        return None               # périmé : le titre retente sa chance
    return rec.get("motif") or "bloqué"


def tickers_bloques(aujourd_hui: datetime | None = None) -> set:
    """Ensemble des tickers actuellement bloqués — pour filtrer un univers."""
    return {t for t in charger() if raison(t, aujourd_hui)}


def retirer(ticker: str) -> bool:
    data = charger()
    if data.pop((ticker or "").upper(), None) is None:
        return False
    _sauver(data)
    return True
