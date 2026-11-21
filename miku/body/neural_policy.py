"""Politiques paramétrées de MIKU-01 (from scratch, numpy).

Contrat commun (interface `ParametrizedPolicy`) :
  - get_params() / set_params(p) : vecteur plat de paramètres,
  - act(state, dt) : action (une par joint),
  - save(path) / load(path) : npz.

CPGPolicy : oscillateur de phase + réflexes linéaires.
  action_j = tanh( A_j·[sin φ, cos φ] + B_j·state[feedback] + c_j )
  → la marche de base est un motif d'oscillation PUIS modulée par les
  réflexes sur l'état réel. Rien n'est pré-écrit : A, B, c sont APRIS.

MLPPolicy : petit réseau tanh (entrée état → couche cachée → joints).
"""
import numpy as np


def _flat(path, **blobs):
    np.savez(path, **blobs)


class ParametrizedPolicy:
    def get_params(self):
        raise NotImplementedError

    def set_params(self, p):
        raise NotImplementedError

    def n_params(self):
        return len(self.get_params())

    def save(self, path):
        np.savez(path, params=self.get_params(), meta=np.array([self.n_params]))

    def load(self, path):
        d = np.load(path)
        self.set_params(d["params"])


class CPGPolicy(ParametrizedPolicy):
    """Oscillateur central + réflexes. n = nb de joints actionnés."""

    def __init__(self, n=6, seed=0, feedback=(4, 5), freq=2.4):
        self.n = n
        self.k = len(feedback)
        self.feedback = tuple(feedback)
        self.freq = freq
        self.seed = seed
        self._rebuild(np.random.default_rng(seed))
        self.phase = 0.0

    def _rebuild(self, rng):
        n, k = self.n, self.k
        self.A = rng.normal(0, 0.7, (n, 2))     # entrainement du motif
        self.B = rng.normal(0, 0.5, (n, k))     # réflexes sur l'état réel
        self.c = rng.normal(0, 0.15, n)

    # ---- contrat paramétrique
    def get_params(self):
        return np.concatenate([self.A.ravel(), self.B.ravel(), self.c])

    def set_params(self, p):
        n, k = self.n, self.k
        p = np.asarray(p, dtype=np.float64)
        need = n * 2 + n * k + n
        if len(p) != need:
            # warm start : A et c copiés, B paddé
            old = p[: n * 2].reshape(n, 2) if len(p) >= n * 2 else \
                np.random.default_rng(0).normal(0, 0.7, (n, 2))
            self.A = old
            rest = p[n * 2:]
            self.B = np.zeros((n, k))
            m = min(k, max(0, len(rest) - n) // max(n, 1))
            if m > 0:
                self.B[:, :m] = (rest[: n * m].reshape(n, m))
            self.c = rest[n * m:n * m + n] if len(rest) >= n * m + n else \
                np.zeros(n)
            return
        i = n * 2
        self.A = p[:i].reshape(n, 2)
        self.B = p[i:i + n * k].reshape(n, k)
        self.c = p[i + n * k:need]

    def act(self, state, dt=0.02):
        self.phase += 2 * np.pi * self.freq * dt
        ref = np.array([state[i] for i in self.feedback])
        u = self.A @ np.array([np.sin(self.phase), np.cos(self.phase)]) \
            + (self.B @ ref if self.k else 0.0) + self.c
        return np.tanh(u)

    def reset_phase(self):
        self.phase = 0.0


class MLPPolicy(ParametrizedPolicy):
    """MLP tanh : state[:in_dim] -> H -> n joints. Pour des compétences non
    périodiques (attraper, viser...)."""

    def __init__(self, in_dim=22, n=6, h=24, seed=0):
        self.in_dim, self.n, self.h = in_dim, n, h
        rng = np.random.default_rng(seed)
        self.W1 = rng.normal(0, 0.4, (in_dim, h))
        self.b1 = np.zeros(h)
        self.W2 = rng.normal(0, 0.4, (h, n))
        self.b2 = np.zeros(n)

    def get_params(self):
        return np.concatenate([self.W1.ravel(), self.b1, self.W2.ravel(), self.b2])

    def set_params(self, p):
        i1 = self.in_dim * self.h
        i2 = i1 + self.h
        i3 = i2 + self.h * self.n
        self.W1 = p[:i1].reshape(self.in_dim, self.h)
        self.b1 = p[i1:i2]
        self.W2 = p[i2:i3].reshape(self.h, self.n)
        self.b2 = p[i3:]

    def act(self, state, dt=0.02):
        z = np.tanh(state[:self.in_dim] @ self.W1 + self.b1)
        return np.tanh(z @ self.W2 + self.b2)
