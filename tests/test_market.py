"""market.py — la source unique sur « où se traite ce ticker ».

Ces tests figent l'équivalence avec les CINQ définitions qui existaient avant
(config._suffix, monitor._is_us, autonomous_engine.market_open_for,
portfolio.market_close_expiry, sync_engine.MIC_MARKETS) : centraliser ne devait
rien changer au comportement.
"""
from datetime import datetime

import pytz

import config
import market
import sync_engine

PARIS = pytz.timezone("Europe/Paris")


def _paris(jour, heure, minute):
    """jour : 0 = lundi … 6 = dimanche (août 2026 : le 10 est un lundi)."""
    return PARIS.localize(datetime(2026, 8, 10 + jour, heure, minute))


class TestClassification:
    def test_suffixe(self):
        assert market.suffix("NVDA") == ""
        assert market.suffix("AIR.PA") == ".PA"
        assert market.suffix("shel.l") == ".L"
        assert market.suffix("") == ""

    def test_base_sans_suffixe(self):
        assert market.base("AIR.PA") == "AIR"
        assert market.base("NVDA") == "NVDA"

    def test_us_est_l_absence_de_suffixe(self):
        """Convention du projet, héritée de SCAN_UNIVERSE."""
        for t in ("NVDA", "BAC", "ILMN"):
            assert market.is_us(t)
        for t in ("AIR.PA", "SHEL.L", "SAP.DE"):
            assert not market.is_us(t)

    def test_devise_par_place(self):
        assert market.currency("NVDA") == "USD"
        assert market.currency("SHEL.L") == "GBP"
        assert market.currency("NESN.SW") == "CHF"
        assert market.currency("AIR.PA") == "EUR"
        assert market.currency("SAP.DE") == "EUR"     # Xetra cote en euros

    def test_place_inconnue_retombe_sur_le_defaut(self):
        assert market.currency("XXX.ZZ") == "EUR"

    def test_symboles(self):
        assert market.symbol("USD") == "$"
        assert market.symbol("EUR") == "€"
        assert market.symbol("GBP") == "£"


class TestHorairesDeMarche:
    def test_us_ouvre_l_apres_midi(self):
        assert not market.is_open_now("NVDA", _paris(0, 10, 0))
        assert market.is_open_now("NVDA", _paris(0, 16, 0))
        assert market.is_open_now("NVDA", _paris(0, 21, 0))
        assert not market.is_open_now("NVDA", _paris(0, 22, 30))

    def test_euronext_ouvre_le_matin(self):
        assert market.is_open_now("AIR.PA", _paris(0, 10, 0))
        assert not market.is_open_now("AIR.PA", _paris(0, 18, 0))

    def test_week_end_ferme_partout(self):
        for t in ("NVDA", "AIR.PA"):
            assert not market.is_open_now(t, _paris(5, 16, 0))   # samedi
            assert not market.is_open_now(t, _paris(6, 16, 0))   # dimanche

    def test_dernier_ordre_avant_la_cloture_reelle(self):
        """Distinction volontaire : on cesse de poster des ordres 5 minutes
        avant la fin de séance, mais la séance dure jusqu'à 17h30."""
        assert not market.is_open_now("AIR.PA", _paris(0, 17, 28))
        assert market.close_time_today("AIR.PA", _paris(0, 10, 0)).hour == 17
        assert market.close_time_today("AIR.PA", _paris(0, 10, 0)).minute == 30

    def test_any_market_open(self):
        assert market.any_market_open(_paris(0, 10, 0))    # Euronext
        assert market.any_market_open(_paris(0, 20, 0))    # US
        assert not market.any_market_open(_paris(0, 23, 0))


class TestPeremption:
    def test_avant_la_cloture_expire_le_jour_meme(self):
        exp = market.close_time_today("NVDA", _paris(0, 16, 0))
        assert (exp.hour, exp.minute) == (21, 55)
        assert exp.day == _paris(0, 16, 0).day

    def test_apres_la_cloture_expire_le_lendemain_9h(self):
        """Une validation du matin ne doit pas servir le lendemain sans être
        refaite."""
        exp = market.close_time_today("AIR.PA", _paris(0, 19, 0))
        assert (exp.hour, exp.minute) == (9, 0)
        assert exp.day == _paris(0, 19, 0).day + 1


class TestMicBourseDirect:
    def test_toutes_les_places_connues_sont_conservees(self):
        """La table MIC de sync_engine, à l'identique — c'est sa divergence
        avec celle de config qui avait produit NVDA.PA."""
        assert sync_engine.MIC_SUFFIX == {
            "XPAR": ".PA", "XAMS": ".AS", "XBRU": ".BR", "XLIS": ".LS",
            "XETR": ".DE", "XMIL": ".MI", "XMAD": ".MC", "XLON": ".L",
            "XSWX": ".SW", "XNYS": "", "XNAS": "", "XNGS": "", "XNMS": "",
            "XNCM": "", "ARCX": "", "XASE": "", "BATS": "",
        }

    def test_nvda_reste_nvda(self):
        """LE cas fondateur : XNGS est la place que BD renvoie réellement."""
        assert market.yf_ticker("NVDA", "XNGS", "USD") == "NVDA"
        assert sync_engine._yf_ticker("NVDA", "XNGS", "USD") == "NVDA"

    def test_place_inconnue_en_usd_reste_us(self):
        """Garde-fou : la devise BD tranche même si le MIC n'est pas répertorié."""
        vus = []
        assert market.yf_ticker("ZZ", "XXXX", "USD", on_unknown=vus.append) == "ZZ"
        assert vus, "une place inconnue doit être signalée, pas subie en silence"

    def test_place_inconnue_hors_usd_suppose_paris_mais_le_dit(self):
        vus = []
        assert market.yf_ticker("ZZ", "", "EUR", on_unknown=vus.append) == "ZZ.PA"
        assert vus

    def test_ticker_vide(self):
        assert market.yf_ticker("", "XPAR") == ""


class TestConfigDelegue:
    def test_les_frais_classent_toujours_pareil(self):
        assert config.is_foreign_ticker("NVDA") is True
        assert config.is_foreign_ticker("AIR.PA") is False
        assert config.is_foreign_ticker("") is False
        assert config.is_foreign_currency("NVDA") is True
        assert config.is_foreign_currency("SAP.DE") is False

    def test_le_bareme_euronext_couvre_les_memes_places(self):
        assert config.EURONEXT_SUFFIXES == market.EURONEXT_SUFFIXES
        for t in ("AIR.PA", "ASML.AS", "UCB.BR"):
            assert config.brokerage_fee(t, 900) == 1.90
