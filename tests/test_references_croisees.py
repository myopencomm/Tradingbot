"""Les références d'un module à l'autre doivent RÉSOUDRE.

INCIDENT DU 14/08/2026 : `/trailing` répondait « module 'autonomous_engine' has
no attribute '_trailing_cancel_failed' » et ne lançait même pas le cycle — il
échouait sur sa première ligne.

`telegram_bot` remettait à zéro un compteur d'échecs en allant chercher l'état
PRIVÉ d'`autonomous_engine`. La découpe du 13/08 a déplacé cet état dans
`trailing.py` ; le bloc d'alias laissé derrière ne ré-exportait que les
FONCTIONS, pas l'état.

Rien ne pouvait l'attraper : un accès `module.attribut` se résout à
l'exécution, `py_compile` n'y voit rien, et le `except Exception` du handler
transformait l'AttributeError en message Telegram au lieu d'un plantage. Le
défaut n'est apparu qu'au premier `/trailing` lancé à la main — le trailing
AUTOMATIQUE, lui, passait par un nom bien ré-exporté et n'a jamais cessé.

Ces tests vérifient que tout `module.attribut` écrit en dur dans le code existe
réellement. C'est bon marché et ça couvre exactement la classe d'erreur qu'un
déplacement de code produit.
"""
import ast
import glob
import importlib
import pathlib

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
MODULES = {pathlib.Path(f).stem for f in glob.glob(str(RACINE / "*.py"))}

# Modules qu'on n'importe pas pour un test : ils lancent des traitements longs
# ou parlent au réseau à l'import.
HORS_PORTEE = {"backtest", "main"}


def _references() -> list[tuple[str, int, str, str]]:
    """Tous les `autre_module.attribut` écrits en dur, avec leur emplacement.

    Un nom ne compte que s'il est IMPORTÉ comme module dans ce fichier et
    jamais réaffecté : `bourse_direct_reader` a une variable locale `orders`
    qui porte le nom d'un module, et `orders.append(...)` n'a évidemment rien
    à voir avec `orders.py`.
    """
    trouvees = []
    for f in sorted(glob.glob(str(RACINE / "*.py"))):
        ici = pathlib.Path(f).stem
        if ici in HORS_PORTEE:
            continue
        arbre = ast.parse(pathlib.Path(f).read_text(encoding="utf-8"))

        # alias tel qu'écrit dans le fichier → vrai nom de module
        # (`import sizing as _ae` : c'est `sizing` qu'il faudra interroger).
        importes: dict[str, str] = {}
        affectes: set[str] = set()
        for n in ast.walk(arbre):
            if isinstance(n, ast.Import):
                for al in n.names:
                    reel = al.name.split(".")[0]
                    if reel in MODULES:
                        importes[al.asname or reel] = reel
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                affectes.add(n.id)
            elif isinstance(n, ast.arg):
                affectes.add(n.arg)

        for n in ast.walk(arbre):
            if not (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)):
                continue
            alias = n.value.id
            reel = importes.get(alias)
            if reel and alias not in affectes and reel != ici and reel not in HORS_PORTEE:
                trouvees.append((ici, n.lineno, reel, n.attr))
    return trouvees


class TestReferencesResolvent:
    def test_il_y_a_bien_des_references_a_verifier(self):
        """Garde-fou du garde-fou : si l'analyse ne trouve plus rien, c'est
        elle qui est cassée, pas le code."""
        assert len(_references()) > 20

    def test_chaque_attribut_reference_existe(self):
        """LE test qui aurait attrapé l'incident du 14/08."""
        manquants = []
        for ici, ligne, cible, attribut in _references():
            mod = importlib.import_module(cible)
            if not hasattr(mod, attribut):
                manquants.append(f"{ici}:{ligne} → {cible}.{attribut}")
        assert not manquants, (
            "références qui ne résolvent plus (attribut déplacé ou renommé) :\n  "
            + "\n  ".join(manquants))

    def test_les_alias_de_compatibilite_pointent_au_bon_endroit(self):
        """Après la découpe, `autonomous_engine` ré-exporte le trailing et le
        sizing. Ces alias sont ce sur quoi main et monitor s'appuient encore."""
        import autonomous_engine as ae
        import sizing
        import trailing
        assert ae.trailing_stop_cycle is trailing.trailing_stop_cycle
        assert ae.trailing_target is trailing.trailing_target
        assert ae.tp_progress is trailing.tp_progress
        assert ae.compute_position_size is sizing.compute_position_size
        assert ae.entry_capacity_block is sizing.entry_capacity_block


class TestRearmementTrailing:
    """Le geste qui a cassé, désormais derrière une fonction publique."""

    def test_rearm_vide_les_echecs_memorises(self):
        import trailing
        trailing._trailing_cancel_failed.add("AIR")
        trailing.rearm_notifications()
        assert not trailing._trailing_cancel_failed

    def test_la_commande_trailing_va_jusqu_au_cycle(self, monkeypatch):
        """Le bug faisait échouer /trailing AVANT le cycle : la commande
        répondait une erreur sans rien vérifier du tout."""
        import telegram_bot as tb
        import trailing

        envoyes, lance = [], []
        monkeypatch.setattr(tb, "send", lambda t, c=None: envoyes.append(t))
        monkeypatch.setattr(tb, "_run_long", lambda cid, fn, *a, **k: fn())
        monkeypatch.setattr(tb.bot_mode, "is_playwright", lambda: True)
        monkeypatch.setattr(tb.playwright_session, "is_connected", lambda: True)
        monkeypatch.setattr(trailing, "trailing_stop_cycle",
                            lambda send_fn, verbose=False: lance.append(verbose))

        trailing._trailing_cancel_failed.add("AIR")
        tb.cmd_trailing([], "1")

        assert lance == [True], "le cycle doit être lancé, et en mode verbeux"
        assert not trailing._trailing_cancel_failed
        assert not any("Erreur" in m for m in envoyes), envoyes

    def test_aucun_module_ne_touche_l_etat_prive_du_trailing(self):
        """C'est la cause racine : atteindre `_trailing_cancel_failed` depuis
        ailleurs. `rearm_notifications()` existe pour ça."""
        fautifs = [f"{ici}:{ligne}" for ici, ligne, cible, attr in _references()
                   if attr == "_trailing_cancel_failed"]
        assert not fautifs, (
            "utilise trailing.rearm_notifications() plutôt que l'état privé : "
            + ", ".join(fautifs))
