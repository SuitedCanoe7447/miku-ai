"""Tests des améliorations « vie humaine » + robustesse (tour améliorations)."""
import json
import os
import sys
import socket
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from miku.mind.experience import Vécu
from miku.social.miku_link import MikuLinkPost


def _ep(dist=1.0, chute=False, penche=0.1, pas=60):
    return {"lieu": "l'appartement", "pas": pas, "distance": dist,
            "chute": chute, "penche": penche, "record_avant": None,
            "record": dist}


class TestVécu(unittest.TestCase):
    def setUp(self):
        self.v = Vécu(path="/tmp/test_journal_v3.json")
        if os.path.exists(self.v.path):
            os.remove(self.v.path)
        self.v.entries = []

    def test_humeur_calme_par_defaut(self):
        h = self.v.humeur()
        self.assertEqual(h["emotion"], "calme")
        self.assertIn("phrase", h)

    def test_humeur_joyeuse_apres_fiertes(self):
        self.v.on_episode(_ep(dist=8.0, penche=0.05))
        h = self.v.humeur()
        self.assertGreater(h["valence"], 0.0)
        self.assertIn(h["emotion"], ("fière", "contente", "calme"))

    def test_oubli_naturel(self):
        for i in range(400):
            self.v.on_episode(_ep(dist=0.5 + (i % 5) * 0.1,
                                  chute=(i % 7 == 0), penche=0.1))
        self.assertLessEqual(len(self.v.entries), self.v.MAX_ENTRIES)

    def test_sauvegarde_throttle(self):
        t0 = time.time()
        for i in range(60):
            self.v.on_episode(_ep(pas=40 + i))
        # pas plus d'écritures/seconde : au plus ~2 fichiers (throttle 1 s)
        self.assertGreaterEqual(self.v._derniere_sauvegarde, t0)


class TestPostSocial(unittest.TestCase):
    """Le post : annuaire persistant, reconnaissance des connus, garde-fous."""

    def _post(self, port):
        post = MikuLinkPost(host="127.0.0.1", port=port)
        post.start()
        time.sleep(0.3)
        self.addCleanup(post.stop)
        return post

    def _client(self, port, agent):
        s = socket.create_connection(("127.0.0.1", port), timeout=4)
        hello = json.dumps({"type": "hello", "agent": agent,
                            "perception": "vue"}) + "\n"
        s.sendall(hello.encode())
        time.sleep(0.3)
        self.addCleanup(s.close)
        return s

    def _lire(self, s, n=8, delai=0.6):
        s.settimeout(delai)
        lignes = []
        try:
            while len(lignes) < n:
                data = s.recv(4096)
                if not data:
                    break
                lignes += [l for l in data.decode().split("\n") if l.strip()]
        except (socket.timeout, OSError):
            pass
        return lignes

    def test_reconnaissance_connus(self):
        port = 7491
        p_ = self._post(port)
        s1 = self._client(port, "NOVA-01")
        self._lire(s1)
        s2 = self._client(port, "NOVA-02")
        self._lire(s2)
        s1.close()
        time.sleep(0.4)
        s3 = socket.create_connection(("127.0.0.1", port), timeout=4)
        s3.sendall((json.dumps({"type": "hello", "agent": "NOVA-01",
                                "perception": "vue"}) + "\n").encode())
        got = self._lire(s3)
        # le poste d'annuaire liste les autres (NOVA-02) avec leur état « connu »
        self.assertTrue(got)
        ar = [json.loads(l) for l in got if '"arrivee"' in l]
        self.assertTrue(any(a["agent"] == "NOVA-02" for a in ar))
        s3.close()

    def test_limites_flood(self):
        port = 7493
        self._post(port)
        s = socket.create_connection(("127.0.0.1", port), timeout=4)
        s.sendall((json.dumps({"type": "hello", "agent": "FLEAU",
                               "perception": "vue"})).encode()
                  + b"X" * (300 * 1024) + b"\n")     # 300 Ko : > MAX_LIGNE
        time.sleep(0.6)
        _l = self._lire(s, n=1)
        # la connexion a dû être coupée par le garde-fou anti-flood
        self.assertLessEqual(len(_l), 1)
        s.close()


if __name__ == "__main__":
    unittest.main()
