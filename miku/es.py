"""Entraîneur par entropie croisée (ES-CEM) — from scratch, numpy.

Boucle : tirer `pop` vecteurs de paramètres ~ N(mu, diag(sigma²)),
évaluer le fitness de chacun (une politique = un corps), garder les
meilleurs `elite_frac`, recentrer mu / sigma sur les élites.
Un plancher de std (0.02) évite la convergence prématurée.
"""
import numpy as np


class CEM:
    def __init__(self, make_policy, fitness, dims, pop=32, elite_frac=0.25,
                 std0=0.5, std_floor=0.02, std_cap=1.2, seed=0):
        self.make_policy = make_policy      # () -> politique
        self.fitness = fitness              # (params) -> float
        self.dims = dims
        self.pop = pop
        self.n_elites = max(2, int(pop * elite_frac))
        self.std_floor = std_floor
        self.std_cap = std_cap
        self.rng = np.random.default_rng(seed)
        self.mu = np.zeros(dims)
        self.sigma = np.full(dims, std0)
        self.best_fit = -1e9
        self.best_params = self.mu.copy()
        self.history = []

    def ask(self):
        return self.mu + self.sigma * self.rng.standard_normal(
            (self.pop, self.dims))

    def tell(self, fits, params):
        fits = np.asarray(fits, dtype=np.float64)
        order = np.argsort(-fits)
        idx = order[:self.n_elites]
        elite = params[idx]
        self.mu = elite.mean(axis=0)
        self.sigma = np.clip(elite.std(axis=0), self.std_floor, self.std_cap)
        if fits[idx[0]] > self.best_fit:
            self.best_fit = float(fits[idx[0]])
            self.best_params = params[idx[0]].copy()
        self.history.append(float(fits.mean()))

    def step(self, verbose=False):
        params = self.ask()
        fits = np.array([self.fitness(p) for p in params])
        self.tell(fits, params)
        if verbose:
            print(f"  gen : moy {fits.mean():7.2f}  max {fits.max():7.2f}  "
                  f"record {self.best_fit:7.2f}")
        return self.best_fit

    def rollout(self, policy, env, steps, render_each=0):
        """Évalue une politique sur un épisode ; renvoie le score détaillé."""
        env.reset()
        if hasattr(policy, "reset_phase"):
            policy.reset_phase()
        pas, cum = 0, 0.0
        for t in range(steps):
            env.step(policy.act(env.state(), env.dt))
            pas += 1
            if env.fallen:
                break
        return pas
