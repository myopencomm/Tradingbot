"""
Pas de cotation — un prix qui n'est pas un multiple du pas n'existe pas.

Module FEUILLE : il n'importe rien du bot. C'est ce qui permet à l'analyse
(qui décide le prix) et à l'envoi d'ordre (qui le transmet) de partager le
MÊME arrondi, sans se connaître ni créer de cycle.

Pourquoi ça compte, deux fois :

  · À L'ENVOI — le 18/08/2026, RTX est parti à 224.431 $, trois décimales. BD a
    répondu 200, puis le NYSE a refusé : « Achat rejeté marché », sans motif ni
    bouton d'annulation fonctionnel. BD renvoie pourtant d'ordinaire un 400
    « Le pas de cotation pour cette limite est 0.01 » — mais pas toujours. Une
    garantie qui ne se déclenche qu'une fois sur deux n'en est pas une.

  · À L'AFFICHAGE — arrondir seulement à l'envoi laissait le message Telegram,
    le contexte mémorisé et l'ordre réel porter trois chiffres différents pour
    un même achat. Rien de cassé, mais un bot qui annonce un prix et en envoie
    un autre n'inspire pas confiance. On arrondit donc à la SOURCE, et l'envoi
    ne fait plus que confirmer.

CE QU'ON SAIT ET CE QU'ON NE SAIT PAS :
  · USA — SEC Rule 612 : 0.01 $ au-dessus d'un dollar, 0.0001 $ en dessous.
    Connu, universel, applicable sans réseau.
  · Europe — MiFID II fait dépendre le pas du cours ET de la liquidité du
    titre ; il n'est PAS déductible d'un cours seul. On arrondit donc au plus
    fin plausible pour la tranche de cours, et on laisse le 400 de BD corriger
    le reste — sur Euronext, LUI, il est fiable.

    Le sens de l'erreur est choisi : trop FIN coûte un aller-retour avec BD,
    qui le signale et que le code retente ; trop GROSSIER déplace le prix pour
    de bon. Arrondir au centime un titre à 0,15 € (MCPHY) le bougerait de plus
    de 3 % — un SL déplacé de 3 %, ce sont des euros réels. On ne prend jamais
    ce risque-là.
"""
import math

TICK_US_SOUS_UN_DOLLAR = 0.0001   # SEC Rule 612
TICK_US = 0.01

# Tranches européennes : le plus fin plausible pour la tranche, jamais assez
# grossier pour déplacer le prix de plus de ~0,1 %. Un pas trop fin est
# rattrapé par le 400 de BD ; un pas trop grossier ne se rattrape pas.
TRANCHES_EUR = ((1.0, 0.0001), (10.0, 0.001))
TICK_EUR_DEFAUT = 0.01


def tick_for(price: float | None, currency: str | None) -> float:
    """Pas de cotation applicable à ce prix, dans cette devise."""
    p = price if price is not None else 1.0
    if (currency or "").upper() == "USD":
        return TICK_US if p >= 1 else TICK_US_SOUS_UN_DOLLAR
    for plafond, pas in TRANCHES_EUR:
        if p < plafond:
            return pas
    return TICK_EUR_DEFAUT


def round_to_tick(price: float, tick: float, direction: str = "nearest") -> float:
    """Arrondit au pas. `direction` : 'up' | 'down' | 'nearest'.

    La tolérance epsilon est indispensable : sans elle un prix DÉJÀ sur le pas
    serait poussé d'un cran, et le trailing annulerait puis reposerait la même
    protection en boucle.
    """
    steps = price / tick
    if direction == "up":
        return round(math.ceil(steps - 1e-9) * tick, 4)
    if direction == "down":
        return round(math.floor(steps + 1e-9) * tick, 4)
    return round(round(steps) * tick, 4)


def round_price(price: float | None, currency: str | None,
                direction: str = "nearest") -> float | None:
    """Arrondi au pas du marché. `None` passe sans bruit."""
    if price is None:
        return None
    return round_to_tick(price, tick_for(price, currency), direction)


def round_levels(entry: float | None, sl: float | None, tp: float | None,
                 currency: str | None) -> tuple:
    """Les trois niveaux d'un ordre Expert, arrondis DANS LE BON SENS.

    Le sens n'est pas cosmétique — c'est le sens conservateur :
      · entrée vers le BAS  → on ne paie jamais plus cher que décidé ;
      · SL vers le HAUT     → arrondir vers le bas éloignerait la protection ;
      · TP vers le BAS      → arrondir vers le haut le rendrait plus dur à atteindre.
    """
    return (round_price(entry, currency, "down"),
            round_price(sl, currency, "up"),
            round_price(tp, currency, "down"))
