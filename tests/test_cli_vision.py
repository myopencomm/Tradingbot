"""Lecture de graphique par la CLI claude (06/11/2026) : l'image est écrite
sur disque et seul l'outil Read est autorisé. Aucun appel réel."""
import os

import ai_provider


def test_image_ecrite_et_seul_read_autorise(monkeypatch):
    vu = {}

    def faux_call(self, prompt, model, effort, tools="", system=None):
        chemin = prompt.split("Ouvre l'image ")[1].split(" avec")[0]
        vu.update(existe=os.path.exists(chemin), octets=open(chemin, "rb").read(),
                  tools=tools, model=model, chemin=chemin)
        return "Signal : NEUTRE"

    monkeypatch.setattr(ai_provider.ClaudeCliProvider, "__init__", lambda self: None)
    monkeypatch.setattr(ai_provider.ClaudeCliProvider, "_call", faux_call)
    p = ai_provider.ClaudeCliProvider()
    assert p.complete_with_image("Analyse", b"PNGDATA") == "Signal : NEUTRE"
    assert vu["existe"] and vu["octets"] == b"PNGDATA"
    assert vu["tools"] == "Read" and vu["model"] == p.MODEL
    assert not os.path.exists(vu["chemin"])      # dossier temporaire nettoyé
