"""Parseurs de la page portefeuille BD.

Les textes de ce fichier sont des lignes RÉELLES relevées dans tradingbot.log
(traces `[position raw]` / `[order raw]` du 11/08/2026). Le log les écrit avec
« | » à la place des retours à la ligne : `_row()` refait la conversion.

Ce sont les fonctions les plus fragiles du bot — elles lisent du texte rendu
par un site tiers qui change sans prévenir. Elles doivent donc être les mieux
tenues.
"""
import bourse_direct_reader as reader


def _row(logline: str) -> str:
    """Reconstitue le inner_text d'origine depuis une ligne de log."""
    return logline.replace(" | ", "\n")


class TestParseFloat:
    def test_formats_bd(self):
        assert reader._parse_float("209.70 €") == 209.7
        assert reader._parse_float("213.46 $US") == 213.46      # format US de BD
        assert reader._parse_float("1 059.75 €") == 1059.75     # espace des milliers
        assert reader._parse_float("196,90") == 196.9           # virgule décimale FR
        assert reader._parse_float("1,059.75") == 1059.75       # virgule des milliers
        assert reader._parse_float("-132.79 €") == -132.79

    def test_valeurs_illisibles(self):
        assert reader._parse_float("") is None
        assert reader._parse_float(None) is None
        assert reader._parse_float("-") is None


class TestParsePosition:
    def test_euronext(self):
        p = reader._parse_position(_row(
            "1 | Airbus SE | XPAR › AIR | 211.95 EUR | -1.26 % | 5 | "
            "PRU : 196.90 € | +7.64 % | 1 059.75 € | +75.25 € | 12%"))
        assert p["name"] == "Airbus SE"
        assert p["bd_ticker"] == "AIR"
        assert p["mic"] == "XPAR"
        assert p["qty"] == 5
        assert p["pru"] == 196.90
        assert p["price"] == 211.95
        assert p["price_currency"] == "EUR"
        assert p["value_eur"] == 1059.75
        assert p["pnl_eur"] == 75.25

    def test_us_pru_converti_en_eur_mais_cours_en_usd(self):
        """Piège BD : sur l'onglet positions le PRU est en EUR, le cours en USD.
        Confondre les deux fabrique un P&L faux de ~14%."""
        p = reader._parse_position(_row(
            "1 | Bank of America Corporation(XNYS) | XNYS › BAC | 63.86 USD | "
            "+1.09 % | 12 | PRU : 54.7325 € | +1.09 % | 663.94 € | +7.15 € | 8%"))
        assert p["bd_ticker"] == "BAC"
        assert p["mic"] == "XNYS"
        assert p["price_currency"] == "USD"
        assert p["pru_currency"] == "EUR"
        assert p["pru"] == 54.7325

    def test_valeur_suspendue_sans_lien_marche(self):
        """Titre en faillite : plus de « place › ticker », la position doit
        quand même être retenue via son nom."""
        p = reader._parse_position(_row(
            "GV | GENOMIC VISION | 0.0018 EUR | - | 142 | PRU : 0.937 € | "
            "-99.81 % | 0.26 € | -132.79 € | <1%"))
        assert p["name"] == "GENOMIC VISION"
        assert p["qty"] == 142
        assert p["bd_ticker"] == ""
        assert p["value_eur"] == 0.26

    def test_ligne_trop_courte_ignoree(self):
        assert reader._parse_position("Airbus\nSE") is None


class TestParseOrder:
    ORDRE_AIR = (
        "Airbus SE | XPAR › AIR | 211.450 EUR | -1.49 % |  |  | \t | "
        "Vente(CPT)\t\t0/5\t\t-\t31/12/2026 à 17:35:00 | "
        "\tTake Profit\tSeuil209.70 €\tEn cours\tProfit217.10 €\tEn cours\t")

    ORDRE_NVDA = (
        "NVIDIA Corporation(XNGS) | XNGS › NVDA | 218.720 USD | -2.86 % |  |  | \t | "
        "Vente(CPT)\t\t0/7\t\t-\t31/08/2026 à 22:00:00 | "
        "\tTake Profit\tSeuil213.46 $US\tEn cours\tProfit225.00 $US\tEn cours\t")

    def test_ordre_protection_euronext(self):
        o = reader._parse_order(_row(self.ORDRE_AIR))
        assert o["bd_ticker"] == "AIR"
        assert o["mic"] == "XPAR"
        assert o["sens"] == "Vente"
        assert o["type"] == "Take Profit"
        assert o["seuil"] == 209.70          # SL
        assert o["profit"] == 217.10         # TP
        assert o["statut"] == "En cours"
        assert (o["qty_exec"], o["qty_total"]) == (0, 5)

    def test_ordre_protection_us(self):
        """La devise doit être lue sur l'ordre : les seuils US sont en $US même
        quand le PRU de la même position est affiché en EUR."""
        o = reader._parse_order(_row(self.ORDRE_NVDA))
        assert o["bd_ticker"] == "NVDA"
        assert o["mic"] == "XNGS"            # NASDAQ Global Select, pas XNAS
        assert o["currency"] == "USD"
        assert o["seuil"] == 213.46
        assert o["profit"] == 225.00
        assert o["statut"] == "En cours"

    def test_le_seuil_est_le_discriminant_de_protection(self):
        """C'est `seuil` sur un ordre « En cours » qui prouve qu'une position
        est protégée (sync_engine). Le contrat doit rester explicite."""
        for raw in (self.ORDRE_AIR, self.ORDRE_NVDA):
            o = reader._parse_order(_row(raw))
            assert o["statut"] == "En cours" and o["seuil"]


class TestCarnetLegacy:
    def test_sans_bd_account_on_s_abstient(self):
        """Sans correspondance certaine de compte, find_account_nc renvoie None
        et l'appelant s'abstient : annuler un ordre sur le mauvais compte est
        irrattrapable (incident du 28/07/2026, carnet du PEA lu à la place)."""
        html = '<select name="nc"><option value="1" selected>PEA</option></select>'
        assert reader.find_account_nc(html, account="") is None
