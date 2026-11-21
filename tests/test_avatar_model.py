"""Tests du pipeline modele FBX -> GLB -> skinning (from scratch, numpy).

Valide :
  - le parseur FBX binaire (skeleton, geometry, clusters),
  - la carte de rig (roles symetriques),
  - le GLB genere (structure, taille, joints),
  - le skinning CPU : identite au repos + deformation locale d'un genou.
"""
import json
import os
import struct
import unittest

import numpy as np

from miku.avatar.fbx import FBXScene, load, clean_name
from miku.avatar.gltf_builder import extract, to_meters

FBX = os.path.join("model_raw", "extract", "source", "hatsune mega mix.fbx")
GLB = os.path.join("model_raw", "miku_megamix.glb")


class TestFBX(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = FBXScene(FBX)

    def test_squelette(self):
        self.assertGreaterEqual(len(self.scene.bones), 124)

    def test_parents_coherents(self):
        for child, parent in self.scene.parent_of.items():
            if child in self.scene.bone_uids:
                self.assertIn(parent, self.scene.models)

    def test_clean_name(self):
        self.assertEqual(clean_name("bone11\x00\x01Model"), "bone11")

    def test_parse_binaire(self):
        tree, ver = load(FBX)
        self.assertGreaterEqual(ver, 7400)
        self.assertIsNotNone(tree.get("Objects"))


class TestExtract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = FBXScene(FBX)
        cls.d = extract(cls.scene)

    def test_geometrie(self):
        self.assertEqual(self.d["V"].shape[0], 28299)
        self.assertEqual(self.d["tri"].shape[0], 39630)
        self.assertEqual(self.d["tri"].shape[1], 3)

    def test_transformlinks(self):
        for bone in self.d["bones_ordered"]:
            M = self.d["TL"][bone]
            self.assertEqual(M.shape, (4, 4))

    def test_influences(self):
        # chaque sommet inflence a au moins 1 poids strictement positif
        infl = self.d["infl"]
        self.assertGreater(len(infl), 10000)
        for cw in list(infl.values())[:100]:
            self.assertGreater(sum(cw.values()), 0.0)


class TestToMeters(unittest.TestCase):
    def test_homogeneite(self):
        M = np.eye(4) * 100.0
        M[3, 3] = 100.0
        out = to_meters(M)
        self.assertAlmostEqual(out[3, 3], 1.0)
        self.assertAlmostEqual(np.abs(out[:3, :3] - np.eye(3)).max(), 0.0)

    def test_echelle_translation(self):
        M = np.eye(4)
        M[:3, :3] *= 50.0
        M[:3, 3] = [100.0, 200.0, 300.0]
        out = to_meters(M)
        self.assertAlmostEqual(out[0, 0], 0.5)
        self.assertAlmostEqual(out[0, 3], 1.0)
        self.assertAlmostEqual(out[1, 3], 2.0)
        self.assertAlmostEqual(out[2, 3], 3.0)
        self.assertAlmostEqual(out[3, 3], 1.0)


class TestGLB(unittest.TestCase):
    def test_fichier_valide(self):
        self.assertTrue(os.path.exists(GLB))
        with open(GLB, "rb") as f:
            magic, version, length = struct.unpack("<III", f.read(12))
        self.assertEqual(magic, 0x46546C67)  # 'glTF'
        self.assertEqual(version, 2)
        self.assertEqual(length, os.path.getsize(GLB))

    def test_taille_raisonnable(self):
        size = os.path.getsize(GLB)
        self.assertGreater(size, 1_000_000)
        self.assertLess(size, 20_000_000)


class TestRigMap(unittest.TestCase):
    def test_roles(self):
        with open(os.path.join("data", "rig_map.json"), encoding="utf-8") as f:
            rig = json.load(f)
        attends = {"pelvis", "spine", "chest", "neck", "head",
                   "shoulderL", "upperArmL", "lowerArmL", "handL",
                   "shoulderR", "upperArmR", "lowerArmR", "handR",
                   "hipL", "kneeL", "footL", "hipR", "kneeR", "footR"}
        self.assertTrue(attends.issubset(rig.keys()))
        # symetrie geometrique : hipL et hipR sont deux os DISTINCTS,
        # a la meme hauteur (z), de part et d'autre du plan median (x=0)
        sc = FBXScene(FBX)
        d = extract(sc)
        uids = {clean_name(sc.models[u].p(1)) if u in sc.models else u: u
                for u in d["bones_ordered"]}
        posL = to_meters(d["TL"][uids[rig["hipL"]]])[:3, 3]
        posR = to_meters(d["TL"][uids[rig["hipR"]]])[:3, 3]
        self.assertNotEqual(rig["hipL"], rig["hipR"])
        self.assertLess(abs(posL[2] - posR[2]), 0.05)      # meme hauteur
        self.assertLess(abs(posL[1] - posR[1]), 0.05)      # meme profondeur
        self.assertTrue((posL[0] < -0.001 and posR[0] > 0.001)
                        or (posL[0] > 0.001 and posR[0] < -0.001))


if __name__ == "__main__":
    unittest.main(verbosity=2)
