"""Valeurs refusées par BD — les mémoriser au lieu de les represcrire.

14/09/2026, 21h08 : le scan US valide DSGN, le contrôle pré-achat confirme,
l'ordre part, BD répond HTTP 403 « Cette valeur n'est plus négociable sur les
US et CANADA ». Deux défauts d'un coup :

  1. le motif restait dans le log — Telegram n'affichait qu'un « HTTP 403 » nu,
     illisible pour qui reçoit ça sur son téléphone (le code ne lisait que
     `data.fields`, jamais `data.message`) ;
  2. rien n'en gardait trace : au scan suivant, DSGN remonte, repasse une
     validation IA complète et se refait refuser à l'identique.

Règle à figer : c'est le MOTIF qui bloque, jamais le code HTTP. Un 403 peut
aussi être une session expirée — bannir sur le statut mettrait au ban des
titres sains au premier hoquet d'authentification.
"""
from datetime import datetime, timedelta

import pytest

import bd_blocklist as bl


MSG_BD = "Cette valeur n'est plus négociable sur les US et CANADA"


@pytest.fixture(autouse=True)
def fichier_temporaire(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "FICHIER", str(tmp_path / "bd_blocklist.json"))


class TestCeQuiBloque:
    def test_le_motif_reel_de_BD_bloque(self):
        assert bl.motif_de_blocage(MSG_BD) == MSG_BD

    def test_insensible_aux_accents_et_a_la_casse(self):
        """BD renvoie du JSON échappé : ne pas dépendre de l'encodage reçu."""
        assert bl.motif_de_blocage("CETTE VALEUR N'EST PLUS NEGOCIABLE")

    @pytest.mark.parametrize("msg", [
        "Session expirée",
        "Une erreur est intervenue",
        "Quantité insuffisante",
        "",
        None,
    ])
    def test_les_autres_refus_ne_bloquent_rien(self, msg):
        """Un 403 d'authentification ne doit PAS bannir un titre sain."""
        assert bl.motif_de_blocage(msg) is None


class TestMemoire:
    def test_un_titre_bloque_est_retenu(self):
        assert bl.ajouter("DSGN", MSG_BD) is True
        assert bl.raison("DSGN") == MSG_BD

    def test_la_casse_du_ticker_est_indifferente(self):
        bl.ajouter("dsgn", MSG_BD)
        assert bl.raison("DSGN") and bl.raison("dsgn")

    def test_un_refus_non_bloquant_n_ecrit_rien(self):
        assert bl.ajouter("AAPL", "Session expirée") is False
        assert bl.raison("AAPL") is None

    def test_titre_inconnu(self):
        assert bl.raison("MSFT") is None

    def test_le_blocage_expire_et_le_titre_retente(self):
        """« N'est plus négociable » est une décision de courtier, pas une loi
        de la nature : une valeur réadmise doit pouvoir revenir."""
        bl.ajouter("DSGN", MSG_BD)
        plus_tard = datetime.now() + timedelta(days=bl.DUREE_JOURS + 1)
        assert bl.raison("DSGN", aujourd_hui=plus_tard) is None

    def test_juste_avant_l_expiration_il_reste_bloque(self):
        bl.ajouter("DSGN", MSG_BD)
        presque = datetime.now() + timedelta(days=bl.DUREE_JOURS - 1)
        assert bl.raison("DSGN", aujourd_hui=presque) == MSG_BD

    def test_retrait_manuel(self):
        bl.ajouter("DSGN", MSG_BD)
        assert bl.retirer("DSGN") is True
        assert bl.raison("DSGN") is None

    def test_fichier_corrompu_ne_fait_pas_planter_le_scan(self):
        with open(bl.FICHIER, "w") as f:
            f.write("{pas du json")
        assert bl.charger() == {}
        assert bl.tickers_bloques() == set()


class TestFiltrageDuScreen:
    def test_le_screen_ecarte_les_valeurs_bloquees(self, monkeypatch):
        import analysis
        bl.ajouter("DSGN", MSG_BD)
        vus = []

        def faux_technicals(t):
            vus.append(t)
            return None            # écarté ensuite, on ne teste que le passage

        monkeypatch.setattr(analysis.prices, "get_technicals", faux_technicals)
        analysis._quant_screen(["AAPL", "DSGN", "MSFT"], set(), "BULL", 0.0)
        assert "DSGN" not in vus and "AAPL" in vus

    def test_sans_blocage_le_screen_voit_tout(self, monkeypatch):
        import analysis
        vus = []
        monkeypatch.setattr(analysis.prices, "get_technicals",
                            lambda t: vus.append(t) or None)
        analysis._quant_screen(["AAPL", "DSGN"], set(), "BULL", 0.0)
        assert set(vus) == {"AAPL", "DSGN"}
