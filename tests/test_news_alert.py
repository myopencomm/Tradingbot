"""Alerte news et résultats sur les positions détenues (news_alert.py).

Alerte seule : ce module ne vend rien. Ce qu'on vérifie ici, c'est qu'il
prévient quand il faut, une seule fois, et qu'il se tait quand Jev est muet.
"""
from datetime import date, timedelta

import pytest

import news_alert


POSITIONS = {
    "AGRO": {"ticker": "AGRO", "bd_name": "ADECOAGRO S A", "entry_price": 11.75,
             "target_low": 10.62},
}

ARTICLES = [
    {"id": "n1", "title": "Why Adecoagro (AGRO) Stock Dropped After Its Latest Update?",
     "summary": "", "date": date.today().isoformat(), "url": "https://x/n1", "provider": "Zacks"},
    {"id": "n2", "title": "Archer Daniels Expands Natural Colors",
     "summary": "", "date": date.today().isoformat(), "url": "", "provider": "Zacks"},
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Portefeuille, sources et Jev simulés. Retourne (messages, appels Jev)."""
    monkeypatch.setattr(news_alert, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(news_alert, "_positions", lambda: dict(POSITIONS))
    monkeypatch.setattr(news_alert, "_price", lambda t: 10.93)
    monkeypatch.setattr(news_alert, "fetch_next_earnings", lambda t: None)
    monkeypatch.setattr(news_alert, "fetch_news", lambda t: list(ARTICLES))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    appels = []

    def jev(company, articles):
        appels.append([a["id"] for a in articles])
        return {a["id"]: ({"about": 0.95, "negative": 0.92} if a["id"] == "n1"
                          else {"about": 0.02, "negative": 0.3}) for a in articles}

    monkeypatch.setattr(news_alert, "classify", jev)
    out = []
    return out, appels, monkeypatch


class TestNews:
    def test_news_negative_sur_la_societe_alerte(self, env):
        out, _, _ = env
        assert news_alert.news_alert_cycle(out.append) == 1
        assert "AGRO" in out[0] and "Dropped" in out[0]
        assert "Aucune vente automatique" in out[0]
        assert "Archer" not in out[0]          # article sur une autre société

    def test_un_article_n_alerte_qu_une_fois(self, env):
        out, appels, _ = env
        news_alert.news_alert_cycle(out.append)
        assert news_alert.news_alert_cycle(out.append) == 0
        assert appels[1] == []            # rien de neuf envoyé à Jev

    def test_jev_en_panne_aucune_alerte_et_article_relu(self, env):
        out, _, mp = env
        mp.setattr(news_alert, "classify", lambda c, a: {})
        assert news_alert.news_alert_cycle(out.append) == 0
        assert out == []
        # Jev revient : l'article n'a pas été perdu
        mp.setattr(news_alert, "classify",
                   lambda c, a: {x["id"]: {"about": 0.9, "negative": 0.9} for x in a})
        assert news_alert.news_alert_cycle(out.append) == 1

    def test_sans_cle_aucun_appel(self, env):
        out, appels, mp = env
        mp.delenv("TYPESAFE_API_KEY")
        assert news_alert.news_alert_cycle(out.append) == 0
        assert appels == []

    def test_article_trop_vieux_ignore(self, env):
        out, _, mp = env
        vieux = [dict(ARTICLES[0], date=(date.today() - timedelta(days=30)).isoformat())]
        mp.setattr(news_alert, "fetch_news", lambda t: vieux)
        assert news_alert.news_alert_cycle(out.append) == 0


class TestResultats:
    def test_resultats_imminents_alertes_une_fois_par_date(self, env):
        out, _, mp = env
        mp.setattr(news_alert, "fetch_news", lambda t: [])
        mp.setattr(news_alert, "fetch_next_earnings", lambda t: date.today() + timedelta(days=3))
        assert news_alert.news_alert_cycle(out.append) == 1
        assert "résultats" in out[0] and "stop-loss" in out[0]
        assert news_alert.news_alert_cycle(out.append) == 0

    def test_resultats_lointains_silence(self, env):
        out, _, mp = env
        mp.setattr(news_alert, "fetch_news", lambda t: [])
        mp.setattr(news_alert, "fetch_next_earnings", lambda t: date.today() + timedelta(days=40))
        assert news_alert.news_alert_cycle(out.append) == 0


class TestParse:
    def test_nouveau_format_yfinance(self):
        items = [{"id": "abc", "content": {
            "title": "JNJ's Caplyta Shows Benefit", "summary": "s", "pubDate": "2026-09-22T12:00:00Z",
            "canonicalUrl": {"url": "https://u"}, "provider": {"displayName": "Zacks"}}}]
        a = news_alert._parse_news(items)[0]
        assert (a["id"], a["date"], a["url"], a["provider"]) == ("abc", "2026-09-22", "https://u", "Zacks")

    def test_ancien_format_yfinance(self):
        a = news_alert._parse_news([{"uuid": "x", "title": "T", "publisher": "P",
                                     "link": "https://l", "providerPublishTime": 1790000000}])[0]
        assert a["title"] == "T" and a["provider"] == "P" and a["date"]


class TestJevRequete:
    def test_seul_du_texte_part_chez_jev(self, monkeypatch):
        """Aucun montant, aucune quantité, aucun PRU dans la requête."""
        envoye = {}

        class R:
            status_code = 200
            def json(self):
                return {"answers": {"about0": {"noul": 0.9}, "neg0": {"noul": 0.1}}}

        def post(url, json, timeout, headers):
            envoye.update(json)
            return R()

        monkeypatch.setenv("TYPESAFE_API_KEY", "k")
        monkeypatch.setattr(news_alert.requests, "post", post)
        v = news_alert.classify("ADECOAGRO S A (AGRO)", ARTICLES[:1])
        assert v == {"n1": {"about": 0.9, "negative": 0.1}}
        assert set(envoye["state"]) == {"company", "articles"}
        assert set(envoye["state"]["articles"]["a0"]) == {"title", "summary"}
