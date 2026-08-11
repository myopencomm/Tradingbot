"""Modèle de frais BD — vérifié au centime sur des ordres RÉELS.

Ces trois cas viennent des PRU affichés par Bourse Direct (qui incluent tous
les frais) : c'est l'étalon le plus dur qu'on ait. Si l'un d'eux casse, ce
n'est pas le test qu'il faut corriger.
"""
import config


class TestOrdresReels:
    """Frais reconstitués depuis nos propres exécutions (cf. config.py)."""

    def test_air_euronext_sans_ttf(self):
        # AIR 5 × 196.52€ = 982.60€ → PRU BD 984.50€ → 1.90€ de courtage seul
        # (Airbus SE : siège aux Pays-Bas, donc PAS de TTF française)
        assert config.brokerage_fee("AIR.PA", 982.60) == 1.90
        assert config.order_fees("AIR.PA", 982.60, "buy", ttf_liable=False) == 1.90

    def test_gle_euronext_avec_ttf(self):
        # GLE 12 × 75.55€ = 906.60€ → 1.90 courtage + 3.63 TTF (0.4%) = 5.53€
        assert config.order_fees("GLE.PA", 906.60, "buy", ttf_liable=True) == 5.53

    def test_bac_us_avec_change(self):
        # BAC : 8.50 courtage US + 0.52 de commission de change (0.08%)
        # Montant en EUR équivalent : 656.79 - 9.04 = 647.75€
        fees = config.order_fees("BAC", 647.75, "buy", ttf_liable=False)
        assert fees == round(8.50 + 647.75 * 0.0008, 2)
        # Aucune TTF sur une valeur US, même en forçant le calcul complet
        assert config.order_fees("BAC", 647.75, "sell") == fees


class TestBaremeParTranches:
    """Le barème Euronext est par TRANCHES — pas un forfait unique."""

    def test_tranches_euronext(self):
        assert config.brokerage_fee("MC.PA", 400) == 0.99
        assert config.brokerage_fee("MC.PA", 500) == 0.99      # borne incluse
        assert config.brokerage_fee("MC.PA", 501) == 1.90
        assert config.brokerage_fee("MC.PA", 1500) == 2.90
        assert config.brokerage_fee("MC.PA", 3000) == 3.80
        # Au-delà de 4 400€ : 0.09% et non plus un palier
        assert config.brokerage_fee("MC.PA", 10000) == round(10000 * 0.0009, 2)

    def test_us_forfait_puis_taux(self):
        assert config.brokerage_fee("NVDA", 900) == 8.50
        assert config.brokerage_fee("NVDA", 20000) == round(20000 * 0.0009, 2)

    def test_place_etrangere_minimum_qui_mord(self):
        # Londres : 0.15% mais MINIMUM 15€ — c'est le minimum qui s'applique
        # à nos tailles de position.
        assert config.brokerage_fee("SHEL.L", 900) == 15.00
        assert config.brokerage_fee("SAP.DE", 900) == 15.00

    def test_sans_ticker_defaut_euronext(self):
        assert config.brokerage_fee("", 900) == config.brokerage_fee("XX.PA", 900)


class TestClassification:
    def test_suffixe(self):
        assert config._suffix("NVDA") == ""
        assert config._suffix("AIR.PA") == ".PA"
        assert config._suffix("shel.l") == ".L"
        assert config._suffix("") == ""

    def test_devise_etrangere(self):
        assert config.is_foreign_currency("NVDA") is True     # USD
        assert config.is_foreign_currency("SHEL.L") is True   # GBP
        assert config.is_foreign_currency("AIR.PA") is False  # EUR
        assert config.is_foreign_currency("SAP.DE") is False  # EUR malgré Xetra

    def test_hors_euronext(self):
        assert config.is_foreign_ticker("NVDA") is True
        assert config.is_foreign_ticker("AIR.PA") is False
        assert config.is_foreign_ticker("") is False


class TestAllerRetour:
    def test_roundtrip_paye_la_ttf_une_seule_fois(self):
        """La TTF est due à l'ACHAT seulement — un A/R ne la paie pas deux fois."""
        buy  = config.order_fees("GLE.PA", 900, "buy",  ttf_liable=True)
        sell = config.order_fees("GLE.PA", 900, "sell", ttf_liable=True)
        assert config.roundtrip_fee("GLE.PA", 900, ttf_liable=True) == round(buy + sell, 2)
        assert sell < buy

    def test_us_bien_plus_cher_que_euronext(self):
        """C'est ce qui impose un plancher de position US (~930€)."""
        us  = config.roundtrip_fee("NVDA", 900, ttf_liable=False)
        eur = config.roundtrip_fee("AIR.PA", 900, ttf_liable=False)
        assert us > eur * 4

    def test_min_viable_amount_us_au_dessus_du_budget_max(self):
        """Le plancher US dépasse POSITION_BUDGET_MAX : aucun achat US possible
        sans relever le plafond. C'est un fait de configuration, pas un bug."""
        floor = config.min_viable_amount("NVDA", ttf_liable=False)
        assert floor > 800
