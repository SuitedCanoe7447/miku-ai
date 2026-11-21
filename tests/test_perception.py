# -*- coding: utf-8 -*-
"""Tests de la PERCEPTION SOCIALE : elle comprend la pièce, se souvient
des gens, vit les compliments et les blessures, et se raconte tout ça."""
import os
import tempfile
import time
import unittest

from miku.mind.experience import Vécu
from miku.social.perception import (
    PerceptionSociale, _valence_texte, écoute_le_post,
)


def _vécula():
    """Un Vécu jetable (fichier temporaire)."""
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(p)
    return Vécu(path=p), p


class TestLectureMessages(unittest.TestCase):
    def test_compliment_vaut_plus_que_politesse(self):
        v_politesse = _valence_texte("salut !")
        v_compliment = _valence_texte(
            "félicitations, tu es très forte et magnifique !")
        # une politesse ne fait PAS plaisir : elle reste légère
        self.assertLess(v_politesse, 0.4)
        self.assertGreater(v_compliment, 0.6)

    def test_insulte_est_negatif(self):
        self.assertLess(_valence_texte("tu es nulle et stupide"), -0.5)

    def test_politesse_ne_mange_pas_le_compliment(self):
        # « re » est court : il ne doit pas absorber « merci »
        v = _valence_texte("re, merci beaucoup !")
        self.assertGreater(v, 0.5)

    def test_emotion_deduite(self):
        from miku.social.perception import _emotion_texte
        self.assertEqual(_emotion_texte("tu es tellement nulle"), "agressée")
        self.assertEqual(_emotion_texte("j'ai un peu peur"), "inquiète")
        self.assertEqual(_emotion_texte("trop bien joué !"), "contente")
        self.assertEqual(_emotion_texte("tu viens d'où ?"), "curieuse")


class TestLiensEtFamiliartite(unittest.TestCase):
    def setUp(self):
        self.p, self.tmp = self._perception()
        self.addCleanup(lambda: os.path.exists(self.tmp)
                        and os.remove(self.tmp))

    def _perception(self):
        fd, p = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(p)
        return PerceptionSociale(path=p), p

    def test_rencontre_incremente(self):
        self.p.on_arrivee("NOVA-01")
        l = self.p.liens["NOVA-01"]
        self.assertEqual(l.rencontres, 1)
        self.assertTrue(l.present)
        p2, _ = self._perception()
        p2.on_arrivee("NOVA-01")
        p2.on_bye("NOVA-01")
        p2.on_arrivee("NOVA-01")
        self.assertEqual(p2.liens["NOVA-01"].rencontres, 2)

    def test_rencontres_patientes_chargees(self):
        # un lien déjà connu arrive avec son historique (annuaire du post)
        self.p.on_arrivee("ZEST-02", connu=True, rencontres=6)
        self.assertGreaterEqual(self.p.liens["ZEST-02"].rencontres, 6)

    def test_familiarite_grandit(self):
        self.p.on_arrivee("NOVA-01", rencontres=5)
        f_avant = self.p.liens["NOVA-01"].familiarite
        for _ in range(3):
            self.p.on_chat("NOVA-01", "tu es vraiment gentille et drôle")
        f_apres = self.p.liens["NOVA-01"].familiarite
        self.assertGreater(f_apres, f_avant)

    def test_temps_partage_compte(self):
        self.p.on_arrivee("NOVA-01")
        l = self.p.liens["NOVA-01"]
        l.arrive_a = time.time() - 600        # 10 min de présence
        self.p.on_bye("NOVA-01")
        self.assertGreater(self.p.liens["NOVA-01"].temps_present, 500)

    def test_relation_evolue(self):
        self.p.on_arrivee("NOVA-01")
        l = self.p.liens["NOVA-01"]
        self.assertEqual(l.relation, "un visage nouveau")
        # beaucoup de temps, de messages et de sympathie → très bon ami
        l2 = self.p._lien("MIRA-03")
        l2.rencontres, l2.messages, l2.affinite = 8, 40, 0.6
        l2.temps_present = 1800.0
        self.assertEqual(l2.relation, "un très bon ami")
        # mais un kiff pas rendu blesse la relation
        l3 = self.p._lien("GRIM-09")
        l3.rencontres, l3.messages, l3.affinite = 8, 40, -0.4
        l3.temps_present = 1800.0
        self.assertEqual(l3.relation, "quelqu'un de blessant")


class TestVécuSocial(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(p)
        v, self.tmp = Vécu(path=p), p
        self.p = PerceptionSociale(vécu=v,
                                   path=os.path.join(self.tmp + ".social"))
        self.addCleanup(lambda: (os.path.exists(self.tmp)
                                 and os.remove(self.tmp)))
        self.vécu = v

    def test_compliment_vit_dans_le_vecu(self):
        self.p.on_chat("NOVA-01", "félicitations, tu es incroyable !")
        self.assertIn("gentil", self.vécu.entries[-1]["recit"])
        self.assertEqual(self.vécu.entries[-1]["lieu"], "la pièce")

    def test_blessure_vit_dans_le_vecu(self):
        self.p.on_chat("GRIM-09", "tu es nulle, franchement")
        self.assertLess(self.vécu.entries[-1]["valence"], 0)

    def test_rencontre_de_rappel(self):
        self.p.on_arrivee("NOVA-01")
        self.p.on_bye("NOVA-01")
        self.p.on_arrivee("NOVA-01")          # deuxième fois → « Re ! »
        self.assertTrue(any("reconnais" in e["recit"]
                            for e in self.vécu.entries))


class TestSituationEtRecit(unittest.TestCase):
    def setUp(self):
        fd, p = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(p)
        self.p = PerceptionSociale(path=p)
        self.addCleanup(lambda: os.path.exists(p) and os.remove(p))

    def test_seule(self):
        self.assertIn("toute seule", self.p.récit())
        self.assertEqual(self.p.situation()["nb_présents"], 0)

    def test_recit_nomme_les_presents(self):
        self.p.on_arrivee("NOVA-01")
        self.assertIn("NOVA-01", self.p.récit())

    def test_centre_de_la_conversation(self):
        self.p.on_arrivee("NOVA-01")
        self.p.on_arrivee("MIRA-03")
        for _ in range(4):
            self.p.on_chat("MIRA-03", "et toi tu en penses quoi ?")
        self.p.presents()  # rien, juste la lecture
        self.assertEqual(self.p.situation()["centre"], "MIRA-03")

    def test_plus_proche_avec_positions(self):
        self.p.on_arrivee("NOVA-01")
        self.p.on_arrivee("MIRA-03")
        self.p.on_world({"NOVA-01": {"x": 5.0, "y": 0.0},
                         "MIRA-03": {"x": 1.2, "y": 0.0}})
        self.assertEqual(self.p.plus_proche().nom, "MIRA-03")

    def test_persistence(self):
        self.p.on_chat("NOVA-01", "félicitations, tu es très forte !")
        self.p.on_arrivee("NOVA-01")
        aff = self.p.liens["NOVA-01"].affinite
        ren = self.p.liens["NOVA-01"].rencontres
        p2 = PerceptionSociale(path=self.p.path)
        self.assertEqual(p2.liens["NOVA-01"].affinite, aff)
        self.assertEqual(p2.liens["NOVA-01"].rencontres, ren)
        # la PRÉSENCE n'est pas un souvenir : en rechargeant, personne
        # n'est « là » tant qu'elle ne l'a pas revu
        self.assertFalse(p2.liens["NOVA-01"].present)


class TestBranchement(unittest.TestCase):
    def test_ecoute_le_post(self):
        class PostFaux:
            def __init__(self):
                self.handlers = {}

            def on(self, t, fn):
                self.handlers[t] = fn

        fd, p = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(p)
        p_soc = PerceptionSociale(path=p)
        post = PostFaux()
        écoute_le_post(p_soc, post)
        # les 4 types sont branchés
        for t in ("chat", "emote", "arrivee", "bye"):
            self.assertIn(t, post.handlers)
        # un message relayé par le post nourrit bien sa perception
        post.handlers["chat"]({"de": "NOVA-01",
                               "texte": "félicitations, tu es très forte"}, None)
        self.assertEqual(p_soc.liens["NOVA-01"].messages, 1)
        post.handlers["arrivee"]({"agent": "MIRA-03", "connu": True,
                                  "rencontres": 3, "humeur": "contente"}, None)
        self.assertEqual(p_soc.liens["MIRA-03"].rencontres, 3)
        post.handlers["emote"]({"de": "MIRA-03", "emotion": "fatiguée"}, None)
        self.assertEqual(p_soc.liens["MIRA-03"].humeur, "fatiguée")
        post.handlers["bye"]({"agent": "MIRA-03"}, None)
        self.assertFalse(p_soc.liens["MIRA-03"].present)


if __name__ == "__main__":
    unittest.main(verbosity=2)
