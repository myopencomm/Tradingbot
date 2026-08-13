"""Chaîne de providers IA : quarantaine d'un provider définitivement en panne.

Un solde épuisé ne se répare pas tout seul. Réessayer le principal à CHAQUE
appel ne fait que payer un aller-retour perdu à chaque fois : le 13/08/2026,
les 24 appels du briefing ont tous commencé par un 400 Anthropic « credit
balance too low » — 159 échecs dans le log, ~20 par jour depuis le 20/07.

Distinction essentielle : une panne PASSAGÈRE (rate limit, timeout, 5xx) doit
être retentée tout de suite ; un échec DÉFINITIF (solde, clé, permission) écarte
le provider pour un temps.
"""
import pytest

from ai_provider import FallbackProvider


class ProviderMort:
    """Échoue toujours, et compte combien de fois on l'a sollicité."""

    def __init__(self, message="Error code: 400 - credit balance is too low"):
        self.message = message
        self.appels = 0

    def complete_cheap(self, *a, **k):
        self.appels += 1
        raise RuntimeError(self.message)


class ProviderVivant:
    def __init__(self):
        self.appels = 0

    def complete_cheap(self, *a, **k):
        self.appels += 1
        return "ok"


@pytest.fixture(autouse=True)
def quarantaine_propre():
    """La quarantaine est un état de CLASSE : à isoler entre les tests."""
    FallbackProvider._quarantaine.clear()
    FallbackProvider._last_notify = 0.0
    yield
    FallbackProvider._quarantaine.clear()


def chaine(mort, vivant):
    f = FallbackProvider(["mort", "vivant"])
    f._instances = {"mort": mort, "vivant": vivant}
    return f


class TestQuarantaine:
    def test_un_solde_epuise_n_est_sollicite_qu_une_fois(self, capsys):
        """LE cas du 13/08 : 24 appels de briefing, 24 aller-retours perdus."""
        mort, vivant = ProviderMort(), ProviderVivant()
        f = chaine(mort, vivant)
        for _ in range(24):
            assert f._run("complete_cheap", "ping") == "ok"
        assert mort.appels == 1
        assert vivant.appels == 24

    def test_le_log_ne_repete_pas_la_bascule(self, capsys):
        """Une ligne par appel IA, c'était 24 lignes par briefing pour une
        information déjà écrite une fois."""
        f = chaine(ProviderMort(), ProviderVivant())
        for _ in range(24):
            f._run("complete_cheap", "ping")
        bascules = capsys.readouterr().out.count("servi par vivant")
        assert bascules == 1

    def test_une_panne_passagere_est_retentee(self):
        """Rate limit, timeout, 5xx : ça se répare tout seul, on ne renonce
        pas au provider principal pour autant."""
        mort = ProviderMort("429 rate limit exceeded, retry later")
        vivant = ProviderVivant()
        f = chaine(mort, vivant)
        for _ in range(5):
            f._run("complete_cheap", "ping")
        assert mort.appels == 5, "un échec passager ne doit pas écarter le provider"

    @pytest.mark.parametrize("message", [
        "Error code: 400 - credit balance is too low",
        "insufficient_quota",
        "billing hard limit reached",
        "invalid_api_key provided",
        "authentication_error",
        "not_found_error: model not found",
    ])
    def test_les_echecs_definitifs_sont_reconnus(self, message):
        assert FallbackProvider._echec_definitif(RuntimeError(message))

    @pytest.mark.parametrize("message", [
        "429 rate limit",
        "timeout after 30s",
        "503 service unavailable",
        "connection reset by peer",
    ])
    def test_les_echecs_passagers_ne_le_sont_pas(self, message):
        assert not FallbackProvider._echec_definitif(RuntimeError(message))

    def test_la_quarantaine_expire(self, monkeypatch):
        """Un solde rechargé doit être repris en compte sans redémarrage."""
        mort, vivant = ProviderMort(), ProviderVivant()
        f = chaine(mort, vivant)
        f._run("complete_cheap", "ping")
        assert mort.appels == 1
        # On avance le temps au-delà de la quarantaine
        monkeypatch.setattr(FallbackProvider, "QUARANTAINE_S", -1.0)
        FallbackProvider._quarantaine["mort"] = 0.0
        f._run("complete_cheap", "ping")
        assert mort.appels == 2, "après expiration, le principal est retenté"


class TestJamaisAveugle:
    def test_toute_la_chaine_ecartee_on_retente_quand_meme(self):
        """Mieux vaut un aller-retour perdu qu'un bot devenu aveugle parce
        qu'un solde a été rechargé sans qu'on s'en aperçoive."""
        vivant = ProviderVivant()
        f = FallbackProvider(["a", "b"])
        f._instances = {"a": vivant, "b": vivant}
        FallbackProvider._quarantaine.update({"a": 1e18, "b": 1e18})
        assert f._run("complete_cheap", "ping") == "ok"
        assert vivant.appels >= 1

    def test_l_erreur_remonte_si_personne_ne_repond(self):
        mort1, mort2 = ProviderMort(), ProviderMort("503 unavailable")
        f = FallbackProvider(["m1", "m2"])
        f._instances = {"m1": mort1, "m2": mort2}
        with pytest.raises(Exception):
            f._run("complete_cheap", "ping")

    def test_le_principal_qui_marche_reste_prioritaire(self):
        principal, secours = ProviderVivant(), ProviderVivant()
        f = FallbackProvider(["p", "s"])
        f._instances = {"p": principal, "s": secours}
        for _ in range(3):
            f._run("complete_cheap", "ping")
        assert (principal.appels, secours.appels) == (3, 0)
