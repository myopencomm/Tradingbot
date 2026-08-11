"""La surface des commandes ne peut plus dériver.

Au 11/08/2026, avant cette phase : 5 commandes manquaient au menu Telegram et
9 à `/help` — dont `/dashboard`, `/lessons`, `/reticker`, `/fallback` et
`/scan_us`, toutes utilisables et documentées nulle part. Tenir cinq listes à
la main n'est pas un problème de discipline mais de conception.

Ces tests garantissent que le dispatch, le menu et l'aide restent DÉRIVÉS de
la table unique — la dérive redevient impossible, pas seulement corrigée.
"""
import commands
import telegram_bot


class TestTableUnique:
    def test_chaque_commande_est_routee(self):
        for c in commands.ALL:
            assert c.slash in telegram_bot.COMMANDS, f"{c.slash} déclaré mais non routé"

    def test_aucune_route_hors_table(self):
        """Une route sans déclaration serait invisible du menu ET de l'aide —
        exactement le cas /capture, /testordre, /oui, /non, /start d'avant."""
        declarees = {c.slash for c in commands.ALL}
        assert set(telegram_bot.COMMANDS) == declarees

    def test_tous_les_handlers_existent(self):
        """Un handler mal nommé doit échouer à l'IMPORT du module, pas au
        premier appel de la commande par l'utilisateur."""
        for c in commands.ALL:
            assert callable(getattr(telegram_bot, c.handler, None)), \
                f"{c.slash} → {c.handler} introuvable"

    def test_pas_de_doublon(self):
        noms = [c.name for c in commands.ALL]
        assert len(noms) == len(set(noms))

    def test_pas_de_slash_dans_les_noms(self):
        for c in commands.ALL:
            assert not c.name.startswith("/")


class TestMenuTelegram:
    def test_le_menu_derive_de_la_table(self):
        assert commands.menu() == [(c.name, c.menu) for c in commands.ALL if c.menu]

    def test_toute_commande_du_menu_a_une_description(self):
        for nom, desc in commands.menu():
            assert desc.strip(), f"/{nom} au menu sans description"

    def test_limite_telegram_respectee(self):
        """Telegram plafonne à 100 commandes et 256 caractères de description."""
        menu = commands.menu()
        assert len(menu) <= 100
        for nom, desc in menu:
            assert len(desc) <= 256
            assert len(nom) <= 32

    def test_seules_les_reponses_de_confirmation_sont_hors_menu(self):
        """Les seules commandes légitimement absentes du menu sont celles qui
        répondent à une question du bot, et le /start automatique de Telegram.
        Toute autre absence est une commande que l'utilisateur ne peut pas
        découvrir — c'était le cas de /capture et /testordre."""
        hors_menu = {c.name for c in commands.ALL if not c.menu}
        assert hors_menu == {"oui", "non", "start"}


class TestAide:
    def test_toute_commande_du_menu_est_dans_l_aide(self):
        """LE test qui aurait attrapé la dérive : /dashboard, /lessons,
        /reticker, /fallback et /scan_us étaient au menu, absents de /help."""
        txt = commands.help_text(7, 10)
        for c in commands.ALL:
            if c.menu:
                assert c.slash in txt, f"{c.slash} au menu mais absent de /help"

    def test_chaque_section_declaree_a_du_contenu(self):
        cles = {c.section for c in commands.ALL if c.section}
        for cle, _titre, intro, _outro in commands.SECTIONS:
            assert cle in cles or intro, f"section « {cle} » vide"

    def test_toute_section_utilisee_est_declaree(self):
        declarees = {cle for cle, *_ in commands.SECTIONS}
        for c in commands.ALL:
            if c.section:
                assert c.section in declarees, f"{c.slash} : section « {c.section} » inconnue"

    def test_les_pourcentages_sont_substitues(self):
        txt = commands.help_text(7, 10)
        assert "{SL}" not in txt and "{TP}" not in txt
        assert "SL -7%" in txt and "TP +10%" in txt

    def test_l_aide_reste_lisible_sur_telegram(self):
        """Telegram coupe à 4096 caractères — au-delà, la fin de l'aide serait
        silencieusement perdue."""
        assert len(commands.help_text(7, 10)) <= 4096

    def test_la_syntaxe_de_ordre_est_conservee(self):
        """Prose qui ne se déduit d'aucune commande : elle doit survivre à la
        génération."""
        txt = commands.help_text(7, 10)
        assert "/ordre acheter TICKER QTE expert ENTREE SL TP" in txt
        assert "validite : seance | max (defaut) | JJ/MM/AAAA" in txt
        assert "./bot.sh start|stop|restart|status|test|logs" in txt


class TestAlias:
    def test_scan_us_equivaut_a_scan_us(self, monkeypatch):
        recu = []
        monkeypatch.setattr(telegram_bot, "cmd_scan",
                            lambda args, cid: recu.append((args, cid)))
        # Le dispatch est construit à l'import : on re-résout pour prendre le
        # handler monkeypatché.
        telegram_bot._resoudre(commands.BY_NAME["scan_us"])(["extra"], "42")
        assert recu == [(["us", "extra"], "42")]


class TestGuideInteractif:
    """Les 457 lignes de prose de /tuto sont sorties du code vers
    docs/tuto/*.txt. Elles doivent rester lisibles et envoyables."""

    def test_toutes_les_sections_ont_leur_fichier(self):
        for section in telegram_bot.TUTO_SECTIONS:
            assert telegram_bot._tuto_pages(section), f"/tuto {section} vide"

    def test_chaque_page_tient_dans_un_message_telegram(self):
        """Telegram coupe à 4096 caractères — d'où le découpage en pages."""
        for section in telegram_bot.TUTO_SECTIONS:
            for page in telegram_bot._tuto_pages(section):
                assert len(page) <= 4096, f"/tuto {section} : page trop longue"

    def test_les_substitutions_sont_appliquees(self):
        for section in telegram_bot.TUTO_SECTIONS:
            for page in telegram_bot._tuto_pages(section):
                assert "{SL}" not in page and "{TP}" not in page

    def test_section_inconnue_affiche_le_menu(self, monkeypatch):
        envoyes = []
        monkeypatch.setattr(telegram_bot, "send", lambda t, c=None: envoyes.append(t))
        telegram_bot.cmd_tuto(["nimporte"], "1")
        assert len(envoyes) == 1 and "Guide interactif" in envoyes[0]
        for section in telegram_bot.TUTO_SECTIONS:
            assert f"/tuto {section}" in envoyes[0]


class TestReadme:
    """Le README est la 5e copie de la liste. Il garde sa prose (chaque
    commande y est expliquée en détail, pas juste listée), mais il ne peut
    plus OUBLIER une commande : ce test échoue si l'une manque."""

    def _readme(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "README.md").read_text()

    def test_toute_commande_du_menu_est_documentee(self):
        txt = self._readme()
        manquantes = [c.slash for c in commands.ALL
                      if c.menu and f"`{c.slash}" not in txt]
        assert not manquantes, f"absentes du README : {manquantes}"
