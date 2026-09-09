"""Briefing court — une ligne par position, rien d'autre.

Demandé le 09/09/2026. Le briefing envoyait ~30 lignes dont 3 de décision :
snapshot complet du portefeuille (qui n'est là que comme source de vérité DU
PROMPT), bloc HOLD (par définition hors décision), risque global (que rien dans
le code ne consommait), capacité, mode d'emploi du /scan. Demande de
l'utilisateur : « est-ce que mes positions sont sur la bonne lancée, ou y a-t-il
des arbitrages à faire ? Rien de plus, le reste c'est du bruit. »

`briefing_lines` est la garantie de format : l'icône et la longueur sont posées
par le CODE, pas par l'IA. Un message quotidien ne doit pas changer de forme
parce qu'un modèle a été bavard ce matin-là.
"""
import analysis


SORTIE_IA = """POSITIONS ACTIVES
- BAC (12t) : MAINTENIR. Consolide au-dessus du SL, aucun signal de rupture.
- JNJ (5t) : SURVEILLER. Approche du TP, momentum qui faiblit.
- CA (75t) : VENDRE. Cible analyste atteinte.
"""


class TestUneLigneParPosition:
    def test_chaque_position_geree_a_sa_ligne(self):
        lignes = analysis.briefing_lines(SORTIE_IA, ["BAC", "JNJ", "CA"])
        assert len(lignes) == 3

    def test_l_icone_vient_du_verdict_pas_de_l_ia(self):
        bac, jnj, ca = analysis.briefing_lines(SORTIE_IA, ["BAC", "JNJ", "CA"])
        assert bac.startswith("✅") and jnj.startswith("👀") and ca.startswith("🔴")

    def test_le_verdict_est_retire_du_commentaire(self):
        """« MAINTENIR » est déjà dit par l'icône — le répéter vole des mots
        au seul contenu utile."""
        bac = analysis.briefing_lines(SORTIE_IA, ["BAC"])[0]
        assert "MAINTENIR" not in bac
        assert "Consolide au-dessus du SL" in bac

    def test_position_oubliee_par_l_ia_sort_quand_meme(self):
        """Mieux vaut une ligne pauvre qu'une position absente du seul message
        quotidien."""
        lignes = analysis.briefing_lines("BAC : MAINTENIR ok", ["BAC", "JNJ"])
        assert len(lignes) == 2 and "pas d'avis rendu" in lignes[1]

    def test_un_modele_bavard_est_tronque(self):
        verbeux = "BAC : MAINTENIR. " + " ".join(f"mot{i}" for i in range(40))
        ligne = analysis.briefing_lines(verbeux, ["BAC"])[0]
        assert ligne.endswith("…") and len(ligne.split()) <= 17

    def test_les_puces_et_quantites_ne_cassent_pas_la_reconnaissance(self):
        """L'IA écrit « - CA (75t) : ... » — le ticker doit être reconnu."""
        ligne = analysis.briefing_lines("- CA (75t) : SURVEILLER. Proche du TP.",
                                        ["CA"])[0]
        assert ligne.startswith("👀 CA")

    def test_verdict_non_reconnu_reste_neutre_sans_planter(self):
        ligne = analysis.briefing_lines("BAC : rien de neuf", ["BAC"])[0]
        assert ligne.startswith("⬜ BAC")

    def test_analyse_vide_ne_leve_pas(self):
        assert analysis.briefing_lines("", ["BAC"]) == \
            ["⬜ BAC — pas d'avis rendu ce matin"]


class TestCeQuiADisparu:
    """Le bruit retiré du message — vérifié sur la sortie réelle du 09/09."""

    def test_ni_prix_ni_snapshot_ne_sont_reinjectes(self):
        lignes = "\n".join(analysis.briefing_lines(SORTIE_IA, ["BAC", "JNJ", "CA"]))
        assert "PRU" not in lignes and "SNAPSHOT" not in lignes

    def test_le_bloc_hold_n_a_pas_de_ligne(self):
        """ILMN, GVN et MCPHY sont hors gestion : ils n'entrent même pas dans
        la liste passée à la fonction."""
        lignes = analysis.briefing_lines(
            SORTIE_IA + "\n- ILMN : HOLD long terme", ["BAC", "JNJ", "CA"])
        assert not any("ILMN" in l for l in lignes)

    def test_le_risque_global_n_est_plus_demande_a_l_ia(self):
        """Rien dans le code ne le lisait — il coûtait des tokens et des
        lignes."""
        import inspect
        src = inspect.getsource(analysis.morning_briefing)
        assert "Risque global" not in src
