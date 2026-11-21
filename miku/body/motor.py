"""Compétences motrices de MIKU-01 : apprentissage par ES-CEM.

Chaque compétence :
  - est APPRISE (jamais scriptée) — le fitness seul définit l'objectif ;
  - est stockée dans un .npz (miku_skill_<nom>.npz) ;
  - transitte par MotorSkills qui charge la bonne politique pour le viewer.

Curriculum : `stand` (tenir debout) -> `walk` (marcher, warm start debout)
-> `terrain` (marche avec randomisation de domaine : pentes, escaliers,
relief), warm start depuis `walk`.
"""
import os

import numpy as np

from miku.es import CEM
from miku.body.physics import (PlanarBody, Terrain, SlopeTerrain,
                               StairsTerrain, BumpyTerrain, ANGLE_SCALES)
from miku.body.neural_policy import CPGPolicy, MLPPolicy

DIMS_STAND = 6 * (2 + 2 + 1)          # 6 joints, feedback (4,5) -> 30
DIMS_TERRAIN = 6 * (2 + 7 + 1)        # feedback 7 dims           -> 60


def _vécu(vécu, competence, pol, body, steps, intention, record_avant=None):
    """Un épisode vécu : court l'épisode, puis le passe au journal
    subjectif (la population/fitness reste en coulisses)."""
    if vécu is None:
        return None
    i = 0
    pol.reset_phase()
    penche = 0.0
    for i in range(steps):
        body.step(pol.act(body.state(), body.dt))
        penche = max(penche, abs(body.lean))
        if body.fallen:
            break
    info = {"lieu": competence, "pas": i + 1, "distance": float(body.x),
            "chute": body.fallen, "penche": penche,
            "record_avant": record_avant,
            "record": max(float(body.x), record_avant or 0.0),
            "intention": intention}
    return vécu.on_episode(info)


# ---------------------------------------------------------------------------
# environnements d'évaluation
# ---------------------------------------------------------------------------
def _ep_stand(policy, steps=160):
    body = PlanarBody(with_arms=False)
    policy.reset_phase()
    inv = 0.0
    pas = 0
    for _ in range(steps):
        body.step(policy.act(body.state(), body.dt))
        pas += 1
        if body.fallen:
            break
        # penalite d'immobilite douce (tenir = rester droit, sans jerk)
        inv += min(1.5, abs(body.lean) + 0.1 * abs(body.vy))
        if pas > 12 and abs(body.vx) > 0.8:        # debout ≠ courir sur place
            inv += min(3.0, 0.5 * abs(body.vx))
    return (pas / steps) * (1.5 - 0.5 * inv / max(pas, 1))


def _ep_walk(policy, steps=220, terrain=None, halte=0.05):
    body = PlanarBody(with_arms=False, terrain=terrain)
    policy.reset_phase()
    inv = 0.0
    pas = 0
    for _ in range(steps):
        body.step(policy.act(body.state(), body.dt))
        pas += 1
        if body.fallen:
            break
        inv += abs(body.lean)
    gain = body.x
    return (gain + 0.5 * gain * (pas / steps)) * (0.3 + 0.7 * pas / steps) \
        + 0.3 * pas / steps - halte * inv / max(pas, 1)


def _ep_skill(policy, steps=220):
    """Run complet : survie + distance (utilisé pour les évaluations)."""
    return _ep_walk(policy, steps)


# ---------------------------------------------------------------------------
# entraînements
# ---------------------------------------------------------------------------
def train_stand(generations=15, pop=24, episode_steps=160, seed=0,
                verbose=False, vécu=None):
    pol = CPGPolicy(6, seed=seed, feedback=(4, 5))

    def fit(p):
        pol.set_params(p)
        return _ep_stand(pol, episode_steps)

    # depart neutre (mu=0 = ne rien faire, deja stable) + exploration douce :
    # elle APPREND a tenir en affineant autour de l'immobilite
    es = CEM(lambda: pol, fit, DIMS_STAND, pop=pop, std0=0.12, seed=seed)
    rec = None
    for g in range(generations):
        es.step(verbose=verbose)
        pol.set_params(es.best_params)
        body = PlanarBody(with_arms=False)
        e = _vécu(vécu, "l'appartement", pol, body, episode_steps,
                  "rester droite", rec)
        if e:
            rec = e and max(rec or 0.0, 0.0)
    pol.set_params(es.best_params)
    pol.save("miku_skill_stand.npz")
    if verbose:
        print(f"[stand] entraîné : fit={es.best_fit:.3f}, "
              f"{pol.n_params()} params")
    return pol


def train_walk(generations=25, pop=28, episode_steps=220, seed=0,
               verbose=False, warm_path="miku_skill_stand.npz", vécu=None):
    pol = CPGPolicy(6, seed=seed, feedback=(4, 5))
    if warm_path and os.path.exists(warm_path):
        warm = np.load(warm_path)["params"]
        pol.set_params(warm)                       # A/c déjà bons, B paddé

    def fit(p):
        pol.set_params(p)
        return _ep_walk(pol, episode_steps)

    es = CEM(lambda: pol, fit, DIMS_STAND, pop=pop, std0=0.18, seed=seed)
    rec = None
    for g in range(generations):
        es.step(verbose=verbose)
        pol.set_params(es.best_params)
        body = PlanarBody(with_arms=False)
        e = _vécu(vécu, "l'appartement", pol, body, episode_steps,
                  "marcher", rec)
        if e:
            # record : distance du champion de la génération
            rec = float(body.x)
    pol.set_params(es.best_params)
    pol.save("miku_skill_walk.npz")
    if verbose:
        b = CPGPolicy(6, seed=seed, feedback=(4, 5))
        b.set_params(es.best_params)
        body = PlanarBody(with_arms=False)
        b.reset_phase()
        for _ in range(400):
            body.step(b.act(body.state(), body.dt))
            if body.fallen:
                break
        print(f"[walk] fit={es.best_fit:.2f}, distance 400 pas = "
              f"{body.x:.2f} m, chute à {body.t:.1f}s")
    return pol


def _sample_terrain(rng, diff=None):
    choix = ["flat", "slope_up", "slope_down", "stairs_up", "stairs_down",
             "stairs_up", "stairs_down", "bumpy"]
    t = choix[rng.integers(len(choix))]
    if t == "flat":
        return Terrain()
    if t == "slope_up":
        return SlopeTerrain(rng.uniform(0.08, 0.15))
    if t == "slope_down":
        return SlopeTerrain(-rng.uniform(0.08, 0.15))
    if t == "stairs_up":
        return StairsTerrain(rng.uniform(0.08, 0.14), rng.uniform(0.28, 0.40))
    if t == "stairs_down":
        return StairsTerrain(-rng.uniform(0.08, 0.14), rng.uniform(0.28, 0.40))
    return BumpyTerrain(rng.uniform(0.02, 0.05), seed=int(rng.integers(1e9)))


def train_terrain(generations=15, pop=24, episode_steps=250, seed=0,
                  verbose=False, warm_path="miku_skill_walk.npz"):
    pol = CPGPolicy(6, seed=seed, feedback=(4, 5, 3, 18, 19, 20, 21))
    if warm_path and os.path.exists(warm_path):
        warm = np.load(warm_path)["params"]
        pol.set_params(warm)

    def fit(p):
        pol.set_params(p)
        rng = np.random.default_rng(9000 + 7 * g)
        terrains = [_sample_terrain(rng) for _ in range(3)]
        tot = 0.0
        for tr in terrains:
            tot += _ep_walk(pol, episode_steps, terrain=tr)
        return tot / len(terrains)

    es = CEM(lambda: pol, fit, pol.n_params(), pop=pop, seed=seed)
    for g in range(generations):
        es.step(verbose=verbose)
        rng = np.random.default_rng(9000 + 7 * g)
    pol.set_params(es.best_params)
    pol.save("miku_skill_terrain.npz")
    return pol


# ---------------------------------------------------------------------------
# bibliothèque de compétences
# ---------------------------------------------------------------------------
class MotorSkills:
    """Charge/stocke les compétences (.npz) et fournit la bonne politique."""

    FILES = {"stand": "miku_skill_stand.npz",
             "walk": "miku_skill_walk.npz",
             "terrain": "miku_skill_terrain.npz"}

    SPECS = {
        "stand": lambda seed: CPGPolicy(6, seed=seed, feedback=(4, 5)),
        "walk": lambda seed: CPGPolicy(6, seed=seed, feedback=(4, 5)),
        "terrain": lambda seed: CPGPolicy(6, seed=seed,
                                          feedback=(4, 5, 3, 18, 19, 20, 21)),
    }

    def __init__(self, seed=0):
        self.seed = seed
        self._cache = {}

    def has(self, name):
        return os.path.exists(self.FILES[name])

    def get(self, name):
        if name not in self.FILES:
            raise KeyError(name)
        if name not in self._cache:
            pol = self.SPECS[name](self.seed)
            if self.has(name):
                pol.load(self.FILES[name])
            else:
                pol = None
            self._cache[name] = pol
        return self._cache[name]

    def standard(self):
        """Politique par défaut : terrain > walk > stand."""
        for n in ("terrain", "walk", "stand"):
            p = self.get(n)
            if p is not None:
                return p
        return None


def scale_actions(actions, skill=None):
    return actions
