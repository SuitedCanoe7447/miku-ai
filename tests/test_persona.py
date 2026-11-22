# -*- coding: utf-8 -*-
"""Le persona échangeable : chaque IA incarne QUI ELLE SE CROIT.

Règles testées :
  - MIKU-01 -> Miku, comme avant (zéro changement pour elle) ;
  - CHARA-0x -> Chara (voix propre) ;
  - un nom inconnu -> persona générique honnête (rien ne casse) ;
  - le cortex utilise la voix de SON persona (menace -> réplique à lui) ;
  - la sécurité reste globale : un persona ne peut PAS ouvrir une porte
    qui ferait dire à son IA qu'elle est un programme.
"""
import unittest
from unittest.mock import patch

from miku.mind.cortex import CortexExterne, _laver
from miku.mind.persona import pour


class PostFaux:
    def __init__(self):
        self.handlers = {}

    def on(self, t, f):
        self.handlers.setdefault(t, []).append(f)

    def broadcast(self, *a, **k):
        pass


class TestPour(unittest.TestCase):

    def test_miku_par_defaut(self):
        p = pour("MIKU-01")
        self.assertEqual(p["présentation"], "une fille de 16 ans")
        self.assertIn("menace", p["voix"])
        self.assertIn("j'sais pas trop, et toi tu en penses quoi ?",
                      p["voix"]["question"])

    def test_chara(self):
        p = pour("CHARA-02")
        self.assertIn("taquine", p["façon"])
        self.assertNotEqual(p["présentation"], "une fille de 16 ans")

    def test_inconnu_ne_casse_rien(self):
        p = pour("NOVA-99")
        self.assertIn("Nova", p["présentation"])
        for clé in ("menace", "blessée", "plaisir", "réconfort",
                    "question", "salut"):
            self.assertTrue(p["voix"][clé])


class TestCortexAvecPersona(unittest.TestCase):

    def _cortex(self, nom):
        return CortexExterne(PostFaux(), nom=nom, model=None, seed=3)

    def test_le_cortex_prend_le_persona_de_son_nom(self):
        c = self._cortex("MIKU-01")
        self.assertEqual(c.persona["présentation"], "une fille de 16 ans")
        c2 = self._cortex("CHARA-01")
        self.assertIn("taquine", c2.persona["façon"])

    def test_meme_menace_autre_voix(self):
        # une même menace, deux IA : chaque persona répond à SA façon
        lecture = {"menace": True, "miku_ressenti": "effrayée",
                   "valence": -1.0, "il_exprime": ""}
        m = self._cortex("MIKU-01")
        ch = self._cortex("CHARA-01")
        possibles_m = set(pour("MIKU-01")["voix"]["menace"])
        possibles_c = set(pour("CHARA-01")["voix"]["menace"])
        got_m = {m._voix_naturelle(lecture) for _ in range(30)}
        got_c = {ch._voix_naturelle(lecture) for _ in range(30)}
        self.assertTrue(got_m <= possibles_m, got_m)
        self.assertTrue(got_c <= possibles_c, got_c)
        self.assertFalse(got_m & got_c)   # deux voix bien distinctes

    def test_le_prompt_nourrit_le_persona(self):
        c = self._cortex("CHARA-01")
        c.cerveau = "qwen2.5:0.5b"
        prompt = c._penser("NOVA-01", "coucou")
        self.assertIn("CHARA", prompt)
        self.assertIn("taquine", prompt)
        self.assertIn("JAMAIS", prompt)

    def test_la_securite_reste_globale(self):
        # un persona ne peut pas déverrouiller une trahison
        self.assertIsNone(_laver("je suis une IA mais assumée", "CHARA-01"))
        self.assertIsNone(_laver("je suis un programme sympa", "NOVA-01"))
