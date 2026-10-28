"""/ordre vendre … expert : la position garde les ids de SA protection.
Sans ça, /securiser annulait les ids de la protection précédente (403 BD)."""
import portfolio
import telegram_bot


def _pos(monkeypatch, rec):
    etat = {"positions": {"XYZ": rec}}
    monkeypatch.setattr(portfolio, "load", lambda: etat)
    monkeypatch.setattr(portfolio, "save", lambda d: None)
    return etat


def test_ids_et_seuils_remplaces(monkeypatch):
    etat = _pos(monkeypatch, {"ticker": "XYZ", "qty": 10, "target_low": 40.0,
                              "target_high": 50.0, "protection_ids": ["vieux-1", "vieux-2"]})
    telegram_bot._enregistrer_protection("XYZ", {"children": ["neuf-sl", "neuf-tp"],
                                                 "adjusted": {}, "sl": 42.0, "tp": 48.0})
    p = etat["positions"]["XYZ"]
    assert p["protection_ids"] == ["neuf-sl", "neuf-tp"]
    assert (p["target_low"], p["target_high"]) == (42.0, 48.0)


def test_sans_ids_les_anciens_sont_oublies(monkeypatch):
    # Des ids périmés feraient croire à une annulation possible.
    etat = _pos(monkeypatch, {"ticker": "XYZ", "qty": 10, "protection_ids": ["vieux-1"]})
    telegram_bot._enregistrer_protection("XYZ", {"children": [], "adjusted": {},
                                                 "sl": 42.0, "tp": 48.0})
    assert "protection_ids" not in etat["positions"]["XYZ"]
