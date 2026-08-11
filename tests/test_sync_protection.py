"""Contrôle de protection du sync — non-régression de la fausse alerte du 11/08.

Ce jour-là le sync auto a annoncé les trois positions « sans protection » alors
que les trois ordres étaient intacts : l'onglet « Mes ordres » n'avait rien
rendu, et `orders == []` valait indifféremment « aucun ordre » et « lecture
ratée ».

Ces tests figent les deux règles du correctif :
  1. sans lecture aboutie (`orders_read`), AUCUNE conclusion — et surtout aucun
     drapeau `protected` écrasé ;
  2. avant toute alerte, une RELECTURE ; une protection vue dans l'UNE des deux
     lectures compte comme réelle.
"""
import copy

import pytest

import bourse_direct_reader as reader
import portfolio
import sync_engine

POSITIONS_BD = [{
    "bd_ticker": "AIR", "name": "Airbus SE", "qty": 5, "price": 211.95,
    "price_currency": "EUR", "pru": 196.9, "pru_currency": "EUR",
    "value_eur": 1059.75, "pnl_eur": 75.25, "mic": "XPAR",
}]

ORDRE_AIR = {
    "statut": "En cours", "sens": "Vente", "type": "Take Profit",
    "bd_ticker": "AIR", "name": "Airbus SE", "seuil": 209.7, "profit": 217.1,
    "currency": "EUR",
    "order_entries": [{"id": "4b07d823", "text": "Vente(CPT) 0/5 Seuil209.70 € En cours"}],
}

LOCAL = {"AIR": {"ticker": "AIR.PA", "qty": 5, "entry_price": 196.9,
                 "target_low": 209.7, "target_high": 217.1,
                 "protected": True, "trailable": True, "bd_name": "AIRBUS SE"}}


def bd(orders, orders_read):
    return {"cash": 244.13, "positions": copy.deepcopy(POSITIONS_BD),
            "orders": orders, "orders_read": orders_read, "programmed": []}


@pytest.fixture
def sync(monkeypatch):
    """Lance sync() sur un portefeuille en mémoire. `reads` = les payloads BD
    servis successivement (le 2e est la relecture de confirmation).
    Retourne (texte envoyé, état sauvegardé)."""
    saved = {}
    monkeypatch.setattr(portfolio, "save", lambda d: saved.update(copy.deepcopy(d)))
    monkeypatch.setattr(portfolio, "load", lambda: {
        "cash_available": 244.13, "positions": copy.deepcopy(LOCAL),
        "auto_pending_orders": {}})

    def run(reads, silent=True):
        seq = list(reads)
        monkeypatch.setattr(reader, "get_portfolio",
                            lambda page, send_fn=None: seq.pop(0) if seq else None)
        out = []
        sync_engine.sync(None, out.append, silent=silent)
        return "\n".join(out), saved

    return run


class TestLectureRatee:
    def test_onglet_illisible_aucune_alerte(self, sync):
        """Le cas exact du 11/08 : liste vide non probante."""
        msg, saved = sync([bd([], orders_read=False)])
        assert "SANS PROTECTION" not in msg

    def test_onglet_illisible_le_drapeau_n_est_pas_ecrase(self, sync):
        """Écraser `protected` ferait ensuite lire au trailing un « à nu »
        fabriqué par une lecture ratée."""
        _msg, saved = sync([bd([], orders_read=False)])
        assert saved["positions"]["AIR"]["protected"] is True

    def test_sync_manuel_le_dit_au_lieu_d_afficher_aucun_ordre(self, sync):
        msg, _ = sync([bd([], orders_read=False)], silent=False)
        assert "onglet illisible" in msg and "SUSPENDU" in msg


class TestRelectureDeConfirmation:
    def test_premiere_lecture_partielle_relecture_confirme(self, sync):
        """Une page à moitié rendue ne doit pas déclencher d'alerte."""
        msg, saved = sync([bd([], True), bd([ORDRE_AIR], True)])
        assert "SANS PROTECTION" not in msg
        assert saved["positions"]["AIR"]["protected"] is True

    def test_relecture_impossible_alerte_suspendue(self, sync):
        msg, saved = sync([bd([], True), None])
        assert "SANS PROTECTION" not in msg
        assert saved["positions"]["AIR"]["protected"] is True

    def test_deux_lectures_concordantes_alerte_emise(self, sync):
        """Protection réellement perdue : là, le bot DOIT crier."""
        msg, saved = sync([bd([], True), bd([], True)])
        assert "SANS PROTECTION" in msg and "AIR" in msg
        assert saved["positions"]["AIR"]["protected"] is False


class TestNominal:
    def test_ordre_lu_du_premier_coup(self, sync):
        msg, _ = sync([bd([ORDRE_AIR], True)], silent=False)
        assert "SANS PROTECTION" not in msg
        assert "Airbus SE" in msg

    def test_protection_remontable_detectee(self, sync):
        """Une jambe « Vente … En cours » sans « Ordre exécuté » a un id
        annulable : le trailing peut la remonter."""
        _msg, saved = sync([bd([ORDRE_AIR], True)], silent=False)
        assert saved["positions"]["AIR"]["trailable"] is True

    def test_protection_soudee_a_l_achat_non_remontable(self, sync):
        """Protection rendue dans le nœud de l'ordre d'ACHAT exécuté : elle
        protège, mais BD n'expose pas d'id annulable (cas NVDA 05/08)."""
        soude = dict(ORDRE_AIR, order_entries=[
            {"id": "d57ffcb4", "text": "Achat(CPT) Ordre exécuté Seuil209.70 €"}])
        _msg, saved = sync([bd([soude], True)], silent=False)
        assert saved["positions"]["AIR"]["protected"] is True
        assert saved["positions"]["AIR"]["trailable"] is False
