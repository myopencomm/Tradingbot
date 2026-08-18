"""Choix du cours retenu et diagnostic d'absence de cours.

Deux fonctions nées d'incidents réels :
  · best_price   — yfinance a servi des cours vieux de 2 à 3 séances en les
                   présentant comme courants (04/08/2026).
  · quote_problem— NVDA enregistré en « NVDA.PA » : Yahoo muet sur un titre que
                   BD cotait, annoncé « COURS SUSPENDU » à tort (03/08/2026).

⚠️ Les dates sont RELATIVES à aujourd'hui. Écrites en dur, elles vieillissaient :
deux tests de ce fichier sont tombés le 18/08/2026 non pas parce que le code
avait changé, mais parce que « 2026-08-11 » était devenu vieux de cinq séances.
Un test de caractérisation dont le verdict dépend du calendrier ne caractérise
plus rien.
"""
from datetime import date, timedelta
from pathlib import Path

import portfolio


def _seance(il_y_a: int) -> str:
    """Date de la séance ouvrée `il_y_a` jours ouvrés avant aujourd'hui."""
    d, n = date.today(), 0
    while n < il_y_a:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    while d.weekday() >= 5:          # jamais un week-end
        d -= timedelta(days=1)
    return d.isoformat()


AUJOURD_HUI = _seance(0)
VEILLE      = _seance(1)
VIEUX       = _seance(4)
TRES_VIEUX  = _seance(8)


def q(price=None, currency="EUR", stale=False, as_of=None, status="ok"):
    return {"price": price, "currency": currency, "stale": stale,
            "as_of": as_of if as_of is not None else AUJOURD_HUI, "status": status}


class TestBestPrice:
    def test_yfinance_frais_gagne(self):
        b = portfolio.best_price({"ticker": "AIR.PA", "bd_price": 200.0},
                                 q(price=211.95))
        assert (b["price"], b["source"]) == (211.95, "yf")
        assert b["stale"] is False and b["note"] == ""

    def test_bd_bat_un_yfinance_perime(self):
        """Le relevé BD est le cours du courtier chez qui la position est
        détenue — c'est lui qui déclenchera le SL."""
        b = portfolio.best_price(
            {"ticker": "NVDA", "bd_price": 218.42, "bd_price_currency": "USD",
             "bd_price_at": f"{VEILLE}T22:35"},
            q(price=200.75, currency="USD", stale=True, as_of=VIEUX))
        assert b["price"] == 218.42
        assert b["source"] == "bd"
        assert b["currency"] == "USD"

    def test_un_releve_bd_plus_vieux_que_yfinance_ne_vaut_pas_mieux(self):
        """Session Playwright déconnectée depuis des jours : on garde yfinance,
        périmé mais moins."""
        b = portfolio.best_price(
            {"ticker": "NVDA", "bd_price": 180.0, "bd_price_at": f"{TRES_VIEUX}T10:00"},
            q(price=200.75, stale=True, as_of=VIEUX))
        assert b["price"] == 200.75
        assert b["source"] == "yf_stale"

    def test_aucune_source(self):
        b = portfolio.best_price({"ticker": "GVN.PA"}, q(price=None))
        assert b["price"] is None and b["source"] == ""


class TestCeQuOnDitDuCours:
    """Ce que le LECTEUR doit savoir, et rien d'autre.

    Le bot annonçait « ⚠️ cours Bourse Direct du 17/08 22:35 — yfinance périmé
    (2026-08-14) » sur trois positions à la fois, alors que le cours affiché
    était la dernière clôture, donc parfaitement bon. De la plomberie interne
    prise pour une alerte — et trois avertissements sur un status de six lignes
    n'inspirent pas confiance (18/08/2026).

    La question du lecteur n'est pas « quelle bibliothèque a répondu ? » mais
    « ce chiffre est-il à jour ? ».
    """

    def test_la_derniere_cloture_ne_declenche_aucun_avertissement(self):
        """LE cas signalé : cours BD de la veille au soir, lu le matin avant
        l'ouverture. C'est la dernière clôture — il n'y a rien à signaler."""
        b = portfolio.best_price(
            {"ticker": "AIR.PA", "bd_price": 214.25,
             "bd_price_at": f"{VEILLE}T22:35"},
            q(price=None, stale=True, as_of=VIEUX))
        assert b["price"] == 214.25
        assert b["stale"] is False
        assert b["note"] == ""

    def test_un_cours_vraiment_vieux_est_signale(self):
        b = portfolio.best_price(
            {"ticker": "AIR.PA", "bd_price": 214.25,
             "bd_price_at": f"{VIEUX}T22:35"},
            q(price=None, stale=True, as_of=TRES_VIEUX))
        assert b["stale"] is True
        assert "pas de cotation" in b["note"]
        assert VIEUX in b["note"], "la date du cours doit être dite"

    def test_aucune_note_ne_nomme_la_plomberie(self):
        """Ni « yfinance », ni le nom d'une source qui a échoué : ce sont des
        détails d'implémentation, pas une information pour l'utilisateur."""
        cas = [
            ({"ticker": "X", "bd_price": 10.0, "bd_price_at": f"{TRES_VIEUX}T10:00"},
             q(price=None, stale=True, as_of=TRES_VIEUX)),
            ({"ticker": "X"}, q(price=9.0, stale=True, as_of=TRES_VIEUX)),
            ({"ticker": "X", "bd_price": 10.0, "bd_price_at": f"{VEILLE}T10:00"},
             q(price=None, stale=True, as_of=VIEUX)),
        ]
        for cfg, quote in cas:
            note = portfolio.best_price(cfg, quote)["note"].lower()
            for interdit in ("yfinance", "yahoo", "bourse direct", "yf"):
                assert interdit not in note, f"« {interdit} » dans : {note}"

    def test_un_cours_sans_date_n_est_jamais_presente_comme_frais(self):
        """Reconstitution du 18/08 : GVN s'est retrouvé avec un cours BD sans
        `bd_price_at`. Sans date, l'âge est inconnu — le déclarer à jour serait
        exactement l'erreur que ce module existe pour empêcher."""
        b = portfolio.best_price(
            {"ticker": "X.PA", "bd_price": 10.0},
            q(price=None, stale=True, as_of=None))
        assert b["price"] == 10.0
        assert b["stale"] is True
        assert "sans date" in b["note"]

    def test_stale_et_note_vont_toujours_ensemble(self):
        """Un cours périmé sans explication, ou une explication sur un cours
        frais, seraient tous deux des incohérences."""
        cas = [
            ({"ticker": "X", "bd_price": 10.0, "bd_price_at": f"{VEILLE}T10:00"},
             q(price=None, stale=True, as_of=VIEUX)),
            ({"ticker": "X", "bd_price": 10.0, "bd_price_at": f"{TRES_VIEUX}T10:00"},
             q(price=None, stale=True, as_of=TRES_VIEUX)),
            ({"ticker": "X"}, q(price=9.0)),
        ]
        for cfg, quote in cas:
            b = portfolio.best_price(cfg, quote)
            assert bool(b["note"]) == b["stale"]

    def test_le_status_reste_muet_quand_tout_va_bien(self, monkeypatch):
        """Bout en bout : le STATUS planifié ne doit porter aucun ⚠️ quand les
        cours sont ceux de la dernière clôture."""
        import monitor
        import prices

        monkeypatch.setattr(prices, "get_quote",
                            lambda t: q(price=None, stale=True, as_of=VIEUX))
        monkeypatch.setattr(prices, "get_intraday_range", lambda t, hours=4: {})
        # Seuils neutres : SL déjà au-dessus du PRU (pas d'alerte breakeven),
        # cours loin du SL et du TP. On isole la note de cours — le STATUS
        # n'est d'ailleurs envoyé que si AUCUNE alerte ne part.
        monkeypatch.setattr(portfolio, "load", lambda: {"positions": {"AIR": {
            "ticker": "AIR.PA", "qty": 5, "entry_price": 196.9,
            "target_low": 200.0, "target_high": 300.0, "protected": True,
            "bd_price": 214.25, "bd_price_at": f"{VEILLE}T22:35"}}})
        envoyes = []
        monitor.check_positions(envoyes.append)
        status = next(m for m in envoyes if m.startswith("📊 STATUS"))
        assert "214.25" in status
        assert "⚠️" not in status, status
        assert "yfinance" not in status


class TestQuoteProblem:
    def test_bd_cote_donc_le_ticker_est_faux(self):
        code, msg = portfolio.quote_problem(
            {"ticker": "NVDA.PA", "bd_price": 218.42, "bd_price_currency": "USD"},
            q(price=None, status="suspended"))
        assert code == "ticker"
        assert "$218.42" in msg
        assert "NON SUIVIE" in msg

    def test_vraie_suspension(self):
        code, msg = portfolio.quote_problem({"ticker": "GVN.PA"},
                                            q(price=None, status="suspended"))
        assert code == "suspended"

    def test_indisponible_sans_diagnostic(self):
        code, _ = portfolio.quote_problem({"ticker": "X.PA"}, q(price=None))
        assert code == "unavailable"

    def test_le_releve_bd_prime_sur_le_statut_yahoo(self):
        """Ordre de priorité : si BD cote, c'est un problème de ticker, JAMAIS
        une suspension — même si Yahoo dit « suspended » et que la position
        porte le drapeau worthless."""
        code, _ = portfolio.quote_problem(
            {"ticker": "X.PA", "bd_price": 5.0, "worthless": True},
            q(price=None, status="suspended"))
        assert code == "ticker"


class TestBacASableEtat:
    """Aucun test ne doit pouvoir écrire dans les fichiers d'état réels.

    INCIDENT DU 18/08/2026 : un test remplaçait `portfolio.load()` sans
    remplacer `portfolio.save()`. `monitor.check_positions` écrit — il réarme
    les drapeaux d'alerte — et a donc sauvegardé le portefeuille FICTIF
    par-dessus le vrai. `positions.json` s'est retrouvé réduit à une position
    portant les seuils du test.
    """

    def test_les_chemins_pointent_ailleurs_que_le_vrai_etat(self):
        import history
        import portfolio
        from config import BASE_DIR
        for chemin in (portfolio.POSITIONS_PATH, history.HISTORY_PATH):
            assert BASE_DIR not in chemin.parents, (
                f"{chemin} est dans le dossier du bot — un test peut écraser "
                f"l'état réel")

    def test_un_save_non_simule_n_atteint_rien_de_reel(self):
        """LE geste qui a cassé : sauvegarder sans avoir simulé save()."""
        import portfolio
        portfolio.save({"cash_available": 999999, "positions": {"FAUX": {}}})
        assert portfolio.load()["cash_available"] == 999999      # dans le bac
        vrai = Path(__file__).resolve().parent.parent / "positions.json"
        if vrai.exists():
            import json
            assert json.loads(vrai.read_text()).get("cash_available") != 999999

    def test_l_incident_rejoue_ne_touche_plus_rien(self, monkeypatch):
        """`check_positions` avec un load() simulé et un save() réel — le
        scénario exact du 18/08."""
        import json

        import monitor
        import portfolio
        import prices

        vrai = Path(__file__).resolve().parent.parent / "positions.json"
        avant = vrai.read_text() if vrai.exists() else None

        monkeypatch.setattr(prices, "get_quote",
                            lambda t: q(price=None, stale=True, as_of=VIEUX))
        monkeypatch.setattr(prices, "get_intraday_range", lambda t, hours=4: {})
        monkeypatch.setattr(portfolio, "load", lambda: {"positions": {"FICTIF": {
            "ticker": "X.PA", "qty": 1, "entry_price": 100.0,
            "target_low": 50.0, "target_high": 200.0,
            "bd_price": 150.0, "bd_price_at": f"{VEILLE}T22:35"}}})
        monitor.check_positions(lambda m: None)

        if avant is not None:
            assert vrai.read_text() == avant, "l'état réel a été modifié"
            assert "FICTIF" not in json.loads(avant).get("positions", {})
