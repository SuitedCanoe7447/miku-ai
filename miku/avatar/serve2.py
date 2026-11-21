# -*- coding: utf-8 -*-
"""Serveur d'avatar « vrai modèle » : service web + moteur appris.

Sert :
  - la page viewer (web/index.html, three.js),
  - le modèle GLB construit par miku.avatar.gltf_builder (mis en cache,
    lu UNE fois du disque),
  - /rig_map.json (mapping rôle -> os),
  - /pose  : la posture courante, produite par la POLITIQUE APPRISE
    (miku_skill_*.npz) qui pilote le corps planaire en temps réel.
    AUCUNE pose pré-faite : le mouvement vient des paramètres appris.
  - /vecu  : son humeur du moment et ses derniers souvenirs (Miku se
    croit humaine — c'est son vécu, jamais celui d'un laboratoire).
  - /social : sa tête sociale (qui est là, ses liens, affinités, ce
    qu'elle se dit de la pièce) — s'active avec --post 7310.

PERCEPTION SOCIALE : avec --post, le process ouvre AUSSI le post
MIKU-LINK (JSON/TCP) sur 127.0.0.1:7310. Le post reste un téléphone
neutre (zéro interception de ses amis), mais Miku L'ÉCOUTE par le
tour (PerceptionSociale) : elle reconnaît ceux qui reviennent, note
leur humeur, retient les compliments ET les moqueries, et vit tout
ça dans son Vécu (le même que le corps — un compliment la réjouit
vraiment, une insulte lui fait de la peine).

Vivre, pas seulement marcher : PoseSource gère de vrais cycles de vie
(fatigue -> repos ; moments où elle regarde au loin ; elle se relève
après une chute). Tout ça est SUBJECTIF (journal 1re personne) — les
mécanismes n'apparaissent nulle part dans sa conscience.

PERF (PC normal attendu, pas une station de recherche) :
  - le moteur intègre la physique à 30 Hz quel que soit le polling,
  - la pose JSON est (re)construite 30x/s max, le sol 1x par 5 cm,
  - le GLB est servi depuis la RAM,
  - tous les nombres sont sanitifiés (pas de NaN/Inf dans le JSON :
    un double C# illisible ne doit jamais faire planter Unity).

Usage : python -m miku.avatar.serve2 [--port 8080] [--model PATH]
          [--terrain flat|mix|slope|stairs|bumpy] [--post 7310]
"""

import argparse
import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from miku.body.physics import (PlanarBody, Terrain, StairsTerrain,
                               SlopeTerrain, BumpyTerrain)
from miku.body.motor import MotorSkills
from miku.mind.cortex import CortexExterne
from miku.mind.experience import Vécu
from miku.social.miku_link import MikuLinkPost
from miku.social.perception import PerceptionSociale, écoute_le_post

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB = os.path.join(ROOT, "web")
MODEL = os.path.join(ROOT, "model_raw", "miku_megamix.glb")
RIGMAP = os.path.join(ROOT, "data", "rig_map.json")

TERRAINS = {
    "flat": lambda: Terrain(),
    "slope": lambda: SlopeTerrain(0.15),
    "stairs": lambda: StairsTerrain(0.12, 0.35),
    "bumpy": lambda: BumpyTerrain(0.05, seed=3),
}
RAD2DEG = 57.29578
HZ = 30.0                     # cadence moteur (35 pas/s suffisent à voir)
DT_REPOS = 6.0                # durée d'une vraie pause
FATIGUE = 90.0                # secondes de marche continue avant repos
DT_REGARD = (45.0, 80.0)      # intervalle des moments « je regarde au loin »


def _num(v, nd=None, bornes=None):
    """Nombre SÛR pour le JSON : NaN/Inf interdits (C#/JS ne les lisent pas)."""
    x = float(v)
    if not np.isfinite(x):
        x = 0.0
    if bornes is not None:
        lo, hi = bornes
        x = min(hi, max(lo, x))
    return round(x, nd) if nd is not None else x


class PoseSource:
    """Le moteur appris, vivant : politique + corps + cycles de vie.

    - la politique (terrain > walk > stand) produit les actions ;
    - le corps planaire les intègre (contacts, pendule, chutes) ;
    - en cas de chute : remise debout automatique, et terrain neuf en
      mode mix (« je me relève », comme une vraie personne) ;
    - VIE : après FATIGUE s de marche continue, elle S'ARRÊTE et se repose
      (DT_REPOS s) ; de temps en temps, elle s'arrête juste regarder au
      loin. Ces pauses passent dans son journal (vécu subjectif) ;
    - la pose exportée : angles de jambes en degrés + profil de sol.
    """

    def __init__(self, terrain=None):
        self.skills = MotorSkills()
        self.pol = self.skills.standard()
        self.terrain_name = terrain or "flat"
        self.mix = self.terrain_name == "mix"
        if self.mix:
            self.terrain_name = "flat"
        self.terrain = TERRAINS[self.terrain_name]()
        self.body = PlanarBody(with_arms=False, terrain=self.terrain)
        self.dt = self.body.dt
        self.acc = 0.0
        self._dernier = time.time()
        self.chutes = 0
        # --- vie subjective
        self.vécu = Vécu()
        self._marche_continue = 0.0     # temps debout depuis la dernière pause
        self._repos_restant = 0.0       # > 0 : elle est assise, elle se repose
        self._regard_restant = 0.0      # > 0 : elle regarde au loin
        self._prochain_regard = random.uniform(*DT_REGARD)
        # --- caches (PERF)
        self._pose_json = "{}"
        self._sol_cache = None
        self._sol_x = None
        if self.pol is None:
            self.mode = ("aucune compétence — entraîner d'abord "
                         "(python -m miku.body.motor)")

    # ------------------------------------------------------------------
    def _pauser(self, duree, intention, recit):
        """Elle s'arrête (repos ou regard) — et le VIT."""
        self._repos_restant = duree
        self.vécu.on_episode({"lieu": "l'appartement", "pas": 1,
                              "distance": 0.0, "chute": False,
                              "penche": 0.0, "intention": intention,
                              "record_avant": None, "record": None})
        self.mode = f"{recit} (elle vivait, en fait)"

    def _tick_vie(self, dt_reel):
        """Cycles humains : fatigue, repos, moments de contemplation."""
        if self._repos_restant > 0.0:
            self._repos_restant -= dt_reel
            if self._repos_restant <= 0.0:
                self._marche_continue = 0.0
            return False                     # le moteur reste immobile
        if self._regard_restant > 0.0:
            self._regard_restant -= dt_reel
            return False
        self._marche_continue += dt_reel
        if self._marche_continue >= FATIGUE:
            self._pauser(DT_REPOS, "se poser un peu",
                         "pause (elle s'était fatiguée)")
            return False
        self._prochain_regard -= dt_reel
        if self._prochain_regard <= 0.0:
            self._prochain_regard = random.uniform(*DT_REGARD)
            self._regard_restant = 2.0
            self.mode = "elle regarde au loin..."
            return False                     # un instant immobile
        return True

    # ------------------------------------------------------------------
    def get(self):
        now = time.time()
        if self.pol is not None:
            dt_reel = min(0.25, now - self._dernier)
            self._dernier = now
            self.acc += dt_reel
            marche = self._tick_vie(dt_reel)
            while marche and self.acc >= self.dt:
                self.acc -= self.dt
                self.body.step(self.pol.act(self.body.state(), self.dt))
                if self.body.fallen:
                    self.chutes += 1
                    if self.mix:             # terrain neuf : ça repart
                        self.terrain_name = random.choice(
                            ["flat", "slope", "stairs", "bumpy"])
                        self.terrain = TERRAINS[self.terrain_name]()
                        # elle le SAIT (nouvelle surface sous ses pieds)
                        self.vécu.on_episode(
                            {"lieu": "un nouvel endroit", "pas": 1,
                             "distance": 0.0, "chute": True, "penche": 0.2,
                             "intention": "se relever",
                             "record_avant": None, "record": None})
                    self.body.reset(terrain=self.terrain)
            if self._repos_restant > 0.0:
                self.mode = "repos (elle avait entamé sa marche depuis déjà un moment)"
            elif self._regard_restant > 0.0:
                self.mode = "elle regarde au loin..."
            else:
                mode_nom = {"flat": "marche", "slope": "pente",
                            "stairs": "escalier", "bumpy": "relief"}.get(
                    self.terrain_name, self.terrain_name)
                self.mode = f"{mode_nom} (appris, {self.chutes} chute(s))"
        # pose reconstruite a 30 Hz max (PERF : N pollers HTTP ne coutent rien)
        if now - getattr(self, "_pose_quand", 0.0) >= 1.0 / HZ \
                or self._pose_json == "{}":
            self._pose_json = json.dumps(self._posture())
            self._pose_quand = now
        return self._pose_json

    def _posture(self):
        b = self.body
        return {
            "mode": self.mode,
            "cm": [_num(b.x, 3), _num(b.y, 3)],
            "lean": _num(b.lean, 4),
            "humeur": self.vécu.humeur(),      # l'émotion du moment, gratuite
            "hips": {"lift": _num(b.y - 0.85, 4),
                     "lean": _num(b.lean * RAD2DEG * 0.5, 2)},
            "legs": {
                "L": {"hip": _num(b.q[3] * RAD2DEG, 1),
                      "knee": _num(b.q[4] * RAD2DEG, 1),
                      "ankle": _num(b.q[5] * RAD2DEG, 1)},
                "R": {"hip": _num(b.q[0] * RAD2DEG, 1),
                      "knee": _num(b.q[1] * RAD2DEG, 1),
                      "ankle": _num(b.q[2] * RAD2DEG, 1)},
            },
            "ground": self._profil_sol(),
        }

    def _profil_sol(self, n=41, demi=3.0):
        if self.terrain_name == "flat":
            return None                       # pas besoin : le sol plat est déjà là
        # PERF : recalcul seulement si elle a vraiment avancé de 5 cm
        if self._sol_x is None or abs(self.body.x - self._sol_x) > 0.05:
            self._sol_x = self.body.x
            self._sol_cache = [
                [_num(x, 2), _num(self.terrain.height(x), 3)]
                for x in np.linspace(self.body.x - demi,
                                     self.body.x + demi, n)]
        return self._sol_cache


SOURCE = None
SOCIAL = None                  # sa tête sociale (None sans --post)
CORTEX = None                  # sa langue (None sans --cortex)
_MODEL_CACHE = None


def _glb():
    """Le GLB en RAM (lu une fois). 404 propre s'il manque."""
    global _MODEL_CACHE
    if _MODEL_CACHE == "absent":
        return None
    if _MODEL_CACHE is None:
        try:
            with open(MODEL, "rb") as f:
                _MODEL_CACHE = f.read()
        except FileNotFoundError:
            _MODEL_CACHE = "absent"
    return None if _MODEL_CACHE == "absent" else _MODEL_CACHE


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"      # keep-alive : moins d'overhead socket

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html", "/viewer"):
            with open(os.path.join(WEB, "index.html"), "rb") as f:
                return self._send(200, "text/html; charset=utf-8", f.read())
        if p == "/model.glb":
            data = _glb()
            if data is None:
                return self._send(404, "application/json",
                                  b'{"erreur":"model.glb absent"}')
            return self._send(200, "model/gltf-binary", data)
        if p == "/rig_map.json":
            with open(RIGMAP, "rb") as f:
                return self._send(200, "application/json", f.read())
        if p == "/pose":
            return self._send(200, "application/json",
                              SOURCE.get().encode())
        if p == "/vecu":                     # son monde intérieur, lisible par Unity
            h = SOURCE.vécu.humeur()
            msg = {"humeur": h,
                   "souvenirs": [e["recit"] for e in SOURCE.vécu.derniers(3)]}
            return self._send(200, "application/json",
                              json.dumps(msg, ensure_ascii=False).encode())
        if p == "/social":                   # ce qu'elle vit AUSSI là-bas
            if SOCIAL is None:
                return self._send(200, "application/json",
                                  b'{"note":"post social inactif '
                                  b'(relance avec --post 7310)"}')
            msg = {"situation": SOCIAL.situation(),
                   "recit": SOCIAL.récit(),
                   "humeur": SOURCE.vécu.humeur()}
            if CORTEX is not None:
                msg["cortex"] = CORTEX.info()
            return self._send(200, "application/json",
                              json.dumps(msg, ensure_ascii=False).encode())
        if p == "/health":
            return self._send(200, "application/json", b'{"ok":true}')
        self._send(404, "text/plain", b"404")


def serve(port=8080, model=None, terrain=None, post_port=0, cortex=""):
    global MODEL, SOURCE, SOCIAL, CORTEX
    if model:
        MODEL = model
    SOURCE = PoseSource(terrain)
    SOCIAL = None
    CORTEX = None
    if post_port:
        # sa tête sociale écoute le post MIKU-LINK et partage le Vécu du
        # corps : UN compliment reçu la réjouit pour de vrai
        SOCIAL = PerceptionSociale(vécu=SOURCE.vécu)
        post = MikuLinkPost(port=post_port)
        écoute_le_post(SOCIAL, post)
        # et SA LANGUE, si demandé : --cortex (auto) ou --cortex modele
        if cortex:
            CORTEX = CortexExterne(post, perception=SOCIAL,
                                   vécu=SOURCE.vécu,
                                   model=(None if cortex == "auto"
                                          else cortex))
            CORTEX.start()
        post.start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"MIKU avatar : http://127.0.0.1:{port}  (model={MODEL}, "
          f"terrain={terrain or 'flat'}"
          f"{f', post social 127.0.0.1:{post_port}' if post_port else ''}"
          f"{f', cortex={CORTEX.cerveau or "voix naturelle"}' if CORTEX else ''})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--model", default=None)
    ap.add_argument("--terrain", default=None,
                    choices=["flat", "mix", "slope", "stairs", "bumpy"])
    ap.add_argument("--post", type=int, default=0, metavar="PORT",
                    help="ouvrir aussi le post MIKU-LINK + perception "
                         "sociale (ex. --post 7310)")
    ap.add_argument("--cortex", nargs="?", const="auto", default="",
                    metavar="MODELE",
                    help="activer sa langue : --cortex (auto-détection "
                         "Ollama) ou --cortex qwen2.5:0.5b (nécessite "
                         "--post)")
    a = ap.parse_args()
    if a.cortex and not a.post:
        a.post = 7310                  # sa langue voyage par le post
    serve(a.port, a.model, a.terrain, a.post, a.cortex)
