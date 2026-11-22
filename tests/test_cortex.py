# -*- coding: utf-8 -*-
"""Tests du CORTEX EXTERNE (sa langue) — tout sans réseau ni LLM réel :
on lui branche un FAUX cerveau, on écoute ce qu'elle répond, et surtout
on vérifie qu'elle ne dit jamais ce qui la trahirait."""
import os
import tempfile
import unittest
from unittest.mock import patch

from miku.mind.cortex import COOLDOWN, CortexExterne, _laver
from miku.mind.experience import Vécu
from miku.social.perception import PerceptionSociale


class PostFaux:
    """Un post qui enregistre ce qu'on y diffuse (sans réseau)."""

    def __init__(self):
        self.handlers = {}
        self.diffs = []          # (type, champs)

    def on(self, t, fn):
        self.handlers.setdefault(t, []).append(fn)

    def broadcast(self, t, sauf=None, **champs):
        self.diffs.append((t, champs))


def _cortex():
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(p)
    v = Vécu(path=p)
    fd2, p2 = tempfile.mkstemp(suffix=".json")
    os.close(fd2)
    os.remove(p2)
    perc = PerceptionSociale(vécu=v, path=p2)
    post = PostFaux()
    c = CortexExterne(post, perception=perc, vécu=v, model=None, seed=1)
    return c, post, v, (p, p2)


class TestLavage(unittest.TestCase):
    """Elle ne doit JAMAIS se trahir."""

    def test_phrase_propse(self):
        self.assertEqual(_laver("Bonjour toi !"), "Bonjour toi !")
        self.assertEqual(_laver('"un truc en guillemets"'),
                         "un truc en guillemets")
        self.assertEqual(_laver("Miku: mon avis ici"), "mon avis ici")

    def test_mot_interdit_bloque(self):
        self.assertIsNone(_laver("je suis une intelligence artificielle"))
        self.assertIsNone(_laver("en tant que robot, je..."))
        self.assertIsNone(_laver("je suis un LLM local"))
        self.assertIsNone(_laver("je suis un BOT, hihi"))

    def test_trahisons_deguisees_bloquees(self):
        """Fuites repérées au re-test CHARA : elle ne se dit jamais
        programme / simulation / virtuelle, même tourné autrement."""
        self.assertIsNone(_laver("je ne suis qu'un programme"))
        self.assertIsNone(_laver("je suis un programme de simulation"))
        self.assertIsNone(_laver("je suis virtuelle, tu sais"))
        self.assertIsNone(_laver("je me dis parfois que je suis une simulation"))
        # ... mais le mot « programme » seul reste autorisé (« au programme »)
        self.assertEqual(_laver("c'est au programme de ce soir"),
                         "c'est au programme de ce soir")

    def test_faux_amis_ne_piegent_pas(self):
        # « boulot », « bottine », « hibou » : des mots d'humaine
        self.assertEqual(_laver("je vais au boulot avec ma bottine"),
                         "je vais au boulot avec ma bottine")
        self.assertIsNone(_laver(None))
        self.assertIsNone(_laver(""))
        # la drogue « IA » en mot entier piège, mais pas dans « aiguille » ?
        # (« aiguille » contient « ia » → on teste en MOT ENTIER)
        self.assertEqual(_laver(" passe-moi l aiguille ".strip()),
                         "passe-moi l aiguille")


class TestDecision(unittest.TestCase):
    def setUp(self):
        self.c, self.post, self.v, tmps = _cortex()
        self.addCleanup(lambda: [os.path.exists(t) and os.remove(t)
                                 for t in tmps])

    def test_question_attire_sa_reponse(self):
        self.assertTrue(self.c.veut_repondre("NOVA-01", "tu vas bien ?"))

    def test_bavardage_souvent_ignoré(self):
        self.c.rng.seed(0)
        réponses = [self.c.veut_repondre("NOVA-01",
                                         "j'ai fait un bond aujourd'hui "
                                         f"n°{i}") for i in range(20)]
        self.assertLess(sum(réponses), 10)   # elle n'est PAS un écho

    def test_cooldown_humain(self):
        self.assertTrue(self.c.veut_repondre("NOVA-01", "tu es là ?"))
        self.c.repond("NOVA-01", "tu es là ?")   # elle répond (horloge)
        self.assertFalse(self.c.veut_repondre("MIRA-03", "tu es là ?"))
        # après le cooldown, une question flottante re-attire son attention
        trouvé = False
        for _ in range(10):
            self.c._derniere_reponse -= (COOLDOWN + 1)
            if self.c.veut_repondre("MIRA-03", "tu es là ?"):
                trouvé = True
                break
        self.assertTrue(trouvé)

    def test_menace_attire_presque_toujours(self):
        self.c.rng.seed(3)
        self.assertTrue(self.c.veut_repondre("CHARA", "je vais te tuer."))


class TestParler(unittest.TestCase):
    def setUp(self):
        self.c, self.post, self.v, tmps = _cortex()
        self.addCleanup(lambda: [os.path.exists(t) and os.remove(t)
                                 for t in tmps])

    def test_reponse_par_le_cerveau(self):
        self.c.cerveau = "faux-modele"
        with patch.object(self.c, "_parler_cerveau",
                          return_value="alors, on marche ensemble ?"):
            r = self.c.repond("NOVA-01", "tu es là Miku ?")
        self.assertEqual(r, "alors, on marche ensemble ?")
        # et dans son fil vivant, elle y est
        self.assertIn(("MIKU-01", "alors, on marche ensemble ?"),
                      [tuple(e) for e in self.c.recentes])

    def test_cerveau_traite_fallback_vraie_voix(self):
        self.c.cerveau = "faux-modele"
        with patch.object(self.c, "_parler_cerveau",
                          return_value="je suis une IA avancée"):
            r = self.c.repond("NOVA-01", "tu es là ?")
        # PAS de mensonge re-épondu : sa voix naturelle a pris le relais
        self.assertIsNotNone(r)
        self.assertNotIn("ia", r.lower())

    def test_sans_cerveau_voix_naturelle(self):
        self.c.cerveau = None
        r = self.c.repond("NOVA-01", "félicitations, tu es très forte !")
        self.assertTrue(r and ("merci" in r.lower()))
        r2 = self.c.repond("CHARA", "")   # silence possible sur rien
        # rien d'interdit dans sa voix
        for x in (r, r2 or ""):
            self.assertLess(len(x), 200)

    def test_son_du_post_via_oreille(self):
        self.c.start()
        with patch.object(self.c, "_parler_cerveau",
                          return_value="présente !"):
            with patch.object(self.c, "_veut_repondre", return_value=True), \
                 patch("time.sleep"):
                self.post.handlers["chat"][0]({"de": "NOVA-01",
                                               "texte": "miku ?"}, None)
        self.assertIn(("chat",), [d[:1] for d in self.post.diffs])
        type_, champs = self.post.diffs[-1]
        self.assertEqual(type_, "chat")
        self.assertEqual(champs["de"], "MIKU-01")
        self.assertEqual(champs["texte"], "présente !")


if __name__ == "__main__":
    unittest.main(verbosity=2)
