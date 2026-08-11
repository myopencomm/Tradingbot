"""positions.json : écriture atomique et sérialisation des écrivains.

Ce fichier porte TOUT l'état du bot et il est écrit depuis une dizaine de
threads. Deux garanties sont testées ici :
  · un lecteur ne voit jamais un fichier à moitié écrit ;
  · deux modifications concurrentes ne se perdent pas l'une l'autre.
"""
import json
import threading

import pytest

import portfolio


@pytest.fixture
def etat(tmp_path, monkeypatch):
    """Redirige positions.json vers un fichier temporaire."""
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({"cash_available": 1000, "positions": {}}))
    monkeypatch.setattr(portfolio, "POSITIONS_PATH", path)
    return path


class TestEcritureAtomique:
    def test_aller_retour(self, etat):
        portfolio.save({"cash_available": 42, "positions": {"AIR": {"qty": 5}}})
        assert portfolio.load()["cash_available"] == 42

    def test_aucun_fichier_temporaire_ne_survit(self, etat):
        portfolio.save({"cash_available": 1})
        assert list(etat.parent.glob("*.tmp")) == []

    def test_le_fichier_reste_toujours_du_json_valide(self, etat):
        """Le remplacement est atomique : à tout instant le fichier sur disque
        est l'ancien OU le nouveau, jamais un JSON tronqué."""
        stop = threading.Event()
        corrompu = []

        def lecteur():
            while not stop.is_set():
                try:
                    json.loads(etat.read_text())
                except Exception as e:
                    corrompu.append(e)

        t = threading.Thread(target=lecteur, daemon=True)
        t.start()
        for i in range(300):
            portfolio.save({"cash_available": i,
                            "positions": {f"P{j}": {"qty": j} for j in range(50)}})
        stop.set()
        t.join(timeout=5)
        assert not corrompu


class TestMutate:
    def test_lecture_modification_ecriture(self, etat):
        with portfolio.mutate() as data:
            data["cash_available"] = 777
        assert portfolio.load()["cash_available"] == 777

    def test_rien_n_est_sauvegarde_si_le_bloc_leve(self, etat):
        """Un état à moitié modifié ne part pas sur le disque."""
        with pytest.raises(ValueError):
            with portfolio.mutate() as data:
                data["cash_available"] = -1
                raise ValueError("boom")
        assert portfolio.load()["cash_available"] == 1000

    def test_pas_de_mise_a_jour_perdue_entre_threads(self, etat):
        """Le cas réel : le sync et une commande Telegram écrivent en même
        temps. Sans verrou, l'un des deux incréments disparaît."""
        def incrementer():
            for _ in range(200):
                with portfolio.mutate() as data:
                    data["cash_available"] = data.get("cash_available", 0) + 1

        threads = [threading.Thread(target=incrementer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert portfolio.load()["cash_available"] == 1000 + 4 * 200

    def test_champs_independants_preserves(self, etat):
        """Deux écrivains touchant des clés différentes ne s'effacent pas."""
        def ajouter(nom):
            for i in range(100):
                with portfolio.mutate() as data:
                    data.setdefault("positions", {})[f"{nom}{i}"] = {"qty": i}

        ts = [threading.Thread(target=ajouter, args=(n,)) for n in ("A", "B")]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        assert len(portfolio.load()["positions"]) == 200
