"""Le coupe-circuit réseau des tests — vérifié, pas supposé.

INCIDENT DU 13/08/2026 : la suite de tests a envoyé de VRAIS messages Telegram
au propriétaire du bot. `tests/test_fallback_ia.py` exerce la bascule de
provider IA, laquelle notifie par Telegram ; `tg.send` fait un POST HTTP réel.
Résultat : une alerte « 🔀 FALLBACK IA ACTIF — mort en échec » reçue sur le
téléphone, parlant de providers nommés « mort » et « vivant » — les doublures
de test. Une fausse alerte parfaitement crédible, sur le canal même qui sert
aux vraies.

La règle « aucun test ne touche le réseau » existait depuis le début… en
commentaire dans conftest.py. Ces tests-ci la rendent vérifiable.
"""
import pytest
import requests

import tg
from conftest import SortieReseauInterdite


class TestReseauBloque:
    @pytest.mark.parametrize("methode", ["get", "post", "put", "patch", "delete"])
    def test_requests_leve(self, methode):
        with pytest.raises(SortieReseauInterdite):
            getattr(requests, methode)("https://example.invalid")

    def test_une_session_aussi(self):
        """Les SDK (Anthropic, Gemini, yfinance) passent par une Session, pas
        par les raccourcis du module."""
        with pytest.raises(SortieReseauInterdite):
            requests.Session().get("https://example.invalid")

    def test_le_message_dit_quoi_faire(self):
        with pytest.raises(SortieReseauInterdite, match="monkeypatch"):
            requests.post("https://api.telegram.org/botX/sendMessage")


class TestTelegramMuet:
    def test_send_ne_part_pas(self, _coupe_circuit_reseau):
        """`tg.send` reste appelable — sinon on masquerait des bugs — mais
        n'émet rien."""
        assert tg.send("message de test") is True
        assert _coupe_circuit_reseau == ["message de test"]

    def test_toutes_les_sorties_telegram_sont_neutralisees(self):
        assert tg.send_photo(b"") is True
        assert tg.send_editable("x") is None
        assert tg.edit_message(1, "x") is True
        assert tg.delete_message(1) is True
        assert tg.get_updates(None) is None

    def test_la_bascule_de_provider_ne_notifie_plus_pour_de_vrai(
            self, _coupe_circuit_reseau):
        """LE cas de l'incident, rejoué : une bascule de provider pendant un
        test ne doit atteindre personne."""
        from ai_provider import FallbackProvider

        FallbackProvider._quarantaine.clear()
        FallbackProvider._last_notify = 0.0

        class Mort:
            def complete_cheap(self, *a, **k):
                raise RuntimeError("400 credit balance is too low")

        class Vivant:
            def complete_cheap(self, *a, **k):
                return "ok"

        f = FallbackProvider(["FAUX-PROVIDER-KO", "FAUX-PROVIDER-OK"])
        f._instances = {"FAUX-PROVIDER-KO": Mort(), "FAUX-PROVIDER-OK": Vivant()}
        f._run("complete_cheap", "ping")

        # Le message est bien FABRIQUÉ (le code fait son travail)…
        assert any("FALLBACK IA ACTIF" in m for m in _coupe_circuit_reseau)
        # …mais il n'est jamais PARTI : c'est la capture qui l'a retenu.
        FallbackProvider._quarantaine.clear()
