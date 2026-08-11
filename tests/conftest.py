"""Configuration pytest — rend les modules du bot importables depuis tests/.

Les tests de ce dossier sont des tests de CARACTÉRISATION : ils figent le
comportement ACTUEL du bot, pas un comportement idéal. C'est ce qui permet de
refactoriser (sources uniques, découpe de modules) en prouvant que rien n'a
bougé. Un test qui casse pendant un refactoring = une régression, pas un test
à mettre à jour.

Règle : aucun test ne doit toucher le réseau, Playwright, Telegram, ni
positions.json. Tout ce qui en dépend est injecté (monkeypatch).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
