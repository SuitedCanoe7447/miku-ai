"""Corps physique planaire (plan sagittal) de MIKU-01 — 100% from scratch, numpy.

Philosophie (règle ferme du projet) : ZÉRO pose pré-faite, ZÉRO animation
scriptée. Le corps n'est qu'une machine physique :
  - pelvis ponctuel (masse normalisée 1) avec équilibre de pendule inversé
    (le `lean` : pencher = tomber, comme un vrai humain) ;
  - jambes à 3 articulations (hanche, genou, cheville) câblées ;
  - pieds tracés par cinématique directe planaire ;
  - contacts : le sol pousse UNIQUEMENT par les pieds pénétrés, selon la
    normale de la pente. La marche n'est PAS câblée : elle doit émerger
    des politiques apprises (placement des pieds = poussée du pendule).
  - bras optionnels (passifs par défaut : amortis vers zéro).

Disposition de l'état (stable pour les politiques apprises) :
  [0]x [1]y [2]vx [3]vy [4]lean [5]lean_vel
  [6..11]  angles jambes R puis L (hanche, genou, cheville)
  [12..17] vitesses angulaires jambes
  [18]contact gauche [19]contact droit [20]dégagée L [21]dégagée R
  (avec bras) [22..25] angles bras, [26..29] vitesses bras
  → 22 dims sans bras, 30 avec.
Les indices 18..21 sont IDENTIQUES dans les deux configs (pratique pour
les réflexes terrain).
"""
import numpy as np

G = 9.81
# dimensions géométriques (m) — Miku fait 1.60 m
L_CUISSE, L_TIBIA, L_PIED = 0.42, 0.40, 0.16
HANCHE_Y = 0.88          # hauteur de pelvis au repos (debout, jambes tendues)
RAID = 350.0             # raideur verticale du contact (masse 1)
DAMP_BASE = 12.0         # amortissement vertical de base
DAMP_DEEP = 200.0        # amortissement ajouté si pénétration profonde (réception)
NORM_PEN = 0.04          # pénétration de référence pour la force max
K_PENDULE = G / 0.88     # rigidité pendule inversé (chute si pied derrière)
COUPLE_SOL = 0.0         # (couplage sol testé : trop brutal, désactivé)
REACTION_HANCHE = 0.0    # (réaction hanches testée : désactivée)
K_CHEV = 40.0            # stratégie de cheville : ressort buste<->chevilles     # rigidité pendule inversé (chute si pied derrière)
BUTEE = np.deg2rad(110)  # butée articulaire (genou ne plie pas en arrière !)

# échelles d'angle (rad) : action [-1,1] -> consigne d'angle
ANGLE_SCALES = {0: 1.2, 1: 2.0, 2: 0.6, 3: 1.2, 4: 2.0, 5: 0.6,
                6: 2.6, 7: 2.4, 8: 2.6, 9: 2.4}   # 6..9 = bras


class Terrain:
    """Sol plat (hauteur 0)."""
    def height(self, x):
        return 0.0

    def slope(self, x, eps=0.03):
        return 0.0


class SlopeTerrain(Terrain):
    """Plan incliné : h = tan(angle) * x."""
    def __init__(self, angle):
        self.angle = angle

    def height(self, x):
        return np.tan(self.angle) * x

    def slope(self, x, eps=0.03):
        return (self.height(x + eps) - self.height(x - eps)) / (2 * eps)


class StairsTerrain(Terrain):
    """Escalier : marche `rise` tous les `run` mètres, à partir de `start`."""
    def __init__(self, rise, run, start=0.8):
        self.rise, self.run, self.start = rise, run, start

    def height(self, x):
        if self.rise < 0:
            return self.rise * (1 + np.floor((-x - self.start) / self.run + 1e-9))
        return self.rise * (1 + np.floor((x - self.start) / self.run + 1e-9))

    def slope(self, x, eps=0.03):
        # pente discrète : hauteur locale autour de x (marche = mur)
        return (self.height(x + eps) - self.height(x)) / eps


class BumpyTerrain(Terrain):
    """Relief : 3 sinusoïdes déphasées (amplitude totale ~ +-2.4*amp)."""
    def __init__(self, amp, seed=0):
        self.amp = amp
        rng = np.random.default_rng(seed)
        self.phases = rng.uniform(0, 2 * np.pi, 4)

    def height(self, x):
        p = self.phases
        return (self.amp * 1.0 * np.sin(1.7 * x + p[0])
                + self.amp * 0.7 * np.sin(3.1 * x + p[1])
                + self.amp * 0.5 * np.sin(5.3 * x + p[2]))


class PlanarBody:
    """Miku vue de côté : pendule inversé + deux jambes câblées."""

    def __init__(self, with_arms=True, seed=0, terrain=None):
        self.with_arms = with_arms
        self.nj = 10 if with_arms else 6           # joints
        self.rng = np.random.default_rng(seed)
        self.terrain = terrain if terrain is not None else Terrain()
        self.dt = 0.02
        self.reset()

    # ---------- état ----------
    def reset(self, lean=0.0, terrain=None):
        if terrain is not None:
            self.terrain = terrain
        self.x, self.y = 0.0, HANCHE_Y
        self.vx, self.vy = 0.0, 0.0
        self.lean, self.lean_vel = lean, 0.0
        self.q = np.zeros(self.nj)
        self.dq = np.zeros(self.nj)
        self.t = 0.0
        self.fallen = False
        # état préalloué (PERF : zéro allocation par pas, le même buffer est
        # réécrit — les appelants le lisent immédiatement, ils ne le stockent
        # jamais d'un pas à l'autre, cf. _ep_walk / _vécu).
        self._etat = np.zeros(self.dim(), dtype=np.float64)
        # ancre de pied : x du pied AU MOMENT du contact (grip au sol).
        # Un pied posé ne glisse pas : la poussée de marche vient du fait
        # que l'ancre est en avant/areère du bassin (pendule inversé).
        self.anchors = [None, None]

    def dim(self):
        return 22 + (8 if self.with_arms else 0)

    def state(self):
        """État courant — buffer réutilisé à chaque appel (PERF : la boucle
        physique tourne ~35 000 pas/s, allouer un array par pas coûtait
        une grosse part du temps CPU)."""
        e = self._etat
        e[0], e[1], e[2], e[3] = self.x, self.y, self.vx, self.vy
        e[4], e[5] = self.lean, self.lean_vel
        e[6:12] = self.q[:6]
        e[12:18] = self.dq[:6]
        cL, cR, kL, kR = self.foot_contacts()
        e[18], e[19], e[20], e[21] = cL, cR, kL, kR
        if self.with_arms:
            e[22:30] = self.q[6:]
            e[30:38] = self.dq[6:]
        return e

    # ---------- cinématique ----------
    def _leg_fk(self, hip, knee, ank, cote):
        """Cinématique directe planaire d'une jambe. Retourne (genou, cheville,
        pointe). Les angles sont des consignes : hanche depuis la verticale
        (+ = jambe vers l'avant), genou = flexion (jambe recule), cheville."""
        hx = self.x + cote * 0.06        # hanches légèrement décalées
        hy = self.y
        a_t = hip                        # orientation de la cuisse
        gx = hx + L_CUISSE * np.sin(a_t)
        gy = hy - L_CUISSE * np.cos(a_t)
        a_s = a_t - knee                 # orientation du tibia
        chx = gx + L_TIBIA * np.sin(a_s)
        chy = gy - L_TIBIA * np.cos(a_s)
        a_p = a_s + ank                  # orientation du pied
        px = chx + L_PIED * np.sin(a_p + np.pi / 2)
        py = chy - L_PIED * np.cos(a_p + np.pi / 2) * 0  # pointe horizontale
        px = chx + L_PIED * np.cos(a_p)
        py = chy + L_PIED * np.sin(a_p) * 0.25
        return (gx, gy), (chx, chy), (px, py)

    def foot_positions(self):
        """[(fx, fy) gauche, (fx, fy) droit] — pointe du pied."""
        out = []
        for cote, i in ((-1, 3), (1, 0)):   # actions 0..2 = jambe D, 3..5 = L
            _, _, pointe = self._leg_fk(self.q[i], self.q[i + 1], self.q[i + 2], cote)
            out.append(pointe)
        return out  # [gauche, droit]

    def foot_contacts(self):
        """(contact L, contact R, dégagée L, dégagée R) — pénétration vs sol."""
        out_c, out_k = [], []
        for fx, fy in self.foot_positions():
            h = self.terrain.height(fx)
            pen = h - fy
            conn = pen > 0.005
            out_c.append(1.0 if conn else 0.0)
            out_k.append(max(0.0, pen))
        return out_c[0], out_c[1], out_k[0], out_k[1]

    # ---------- dynamique ----------
    def step(self, actions, dt=None):
        if dt is None:
            dt = self.dt
        actions = np.asarray(actions, dtype=np.float64)

        # --- joints : consignes -> premier ordre + vitesse (muscles lisses)
        reaction_hanches = 0.0
        cible_chevilles = []                   # stratégie de cheville
        for j in range(min(len(actions), self.nj)):
            cible = float(np.tanh(actions[j])) * ANGLE_SCALES.get(j, 1.0)
            if j in (1, 4):                     # genoux : flexion seulement
                cible = -abs(cible)
            acc = 90.0 * (cible - self.q[j]) - 12.0 * self.dq[j]
            if j in (2, 5):                     # chevilles mémorisées
                cible_chevilles.append(cible)
            if j in (0, 3):                     # hanches : couple de réaction
                reaction_hanches -= acc         # fouetter la jambe en avant
                                                # => le bassin tourne en arrière
            self.dq[j] += acc * dt
            self.q[j] += self.dq[j] * dt
            lim = BUTEE if j in (1, 4) else np.deg2rad(70)
            self.q[j] = float(np.clip(self.q[j], -lim, lim))
        # bras passifs si non actionnés
        for j in range(min(len(actions), self.nj), self.nj):
            self.dq[j] += (-4.0 * self.q[j] - 2.0 * self.dq[j]) * dt
            self.q[j] += self.dq[j] * dt

        # --- contacts
        positions = self.foot_positions()
        pens = []
        for fx, fy in positions:
            pens.append(max(0.0, self.terrain.height(fx) - fy))

        # soutien vertical : le pied le PLUS enfoncé porte
        max_pen = max(pens)
        f_y = 0.0
        if max_pen > 0:
            damp = DAMP_BASE + DAMP_DEEP * max(0.0, max_pen - 0.05)
            f_y = RAID * max_pen - damp * self.vy * (0.5 if max_pen < 0.02 else 1.0)
            f_y = max(0.0, f_y)
            f_y /= np.sqrt(1.0 + self.terrain.slope(min(0.9, max(-0.9, positions[int(np.argmax(pens))][0]))) ** 2)

        # poussée horizontale : VRAIE marche pendulaire. Un pied planté est
        # ancré au sol (grip) : si l'ancre est en arrière du bassin, le corps
        # tombe en avant = avancer. Si en avant, freine. Allumer/poser les
        # pieds au bon endroit = marcher. C'est ce que le CPG doit APPRENDRE.
        f_x = 0.0
        w_tot = 0.0
        for i, ((fx, fy), pen) in enumerate(zip(positions, pens)):
            if pen > 0.005:
                if self.anchors[i] is None:        # premier contact : on ancre
                    self.anchors[i] = fx
                w = min(1.0, pen / NORM_PEN)
                f_x += w * (60.0 * (self.anchors[i] - self.x) - 3.0 * self.vx)
                w_tot += w
            else:
                self.anchors[i] = None             # décollée : plus de grip
        if w_tot > 0:
            f_x /= w_tot

        # accélération du pelvis
        acc_x = f_x - 1.5 * self.vx
        acc_y = f_y - G
        self.vx += acc_x * dt
        self.vy += acc_y * dt
        self.x += self.vx * dt
        self.y = max(self.y + self.vy * dt,
                     self.terrain.height(self.x) + 0.30)

        # équilibre : pendule + perturbation de pente lissée
        if w_tot > 0:
            fx_sup = positions[int(np.argmax(pens))][0]
            s_dist = np.clip(self.terrain.slope(fx_sup, eps=0.08), -0.5, 0.5)
            lean_acc = K_PENDULE * np.sin(self.lean) - 2.0 * self.lean_vel \
                - 6.0 * s_dist
            # COUPLAGE POUSSÉE->CORPS (physique réelle) : la force horizontale
            # s'applique AU NIVEAU DES PIEDS, sous le centre de masse -> elle
            # crée un couple sur le corps. Sans ce couplage, le lean serait
            # un pendule purement passif IMPOSSIBLE à contrôler sur pente
            # (dérive → chute inévitable, aucun apprentissage possible).
            # C'est ainsi qu'un vrai marcheur se redresse : en poussant sur
            # le sol, il redresse son buste. Elle devra l'APPRENDRE.
            lean_acc -= COUPLE_SOL * f_x
        else:
            lean_acc = K_PENDULE * np.sin(self.lean) - 2.0 * self.lean_vel
            reaction_hanches = 0.0
            cible_chevilles = [0.0]
        # STRATÉGIE DE CHEVILLE (physiologie réelle) : les chevilles en appui
        # font un ressort qui cible le buste sur la consigne d'angle de
        # cheville. C'est LE mécanisme humain d'équilibre : sur pente, elle
        # doit APPRENDRE à presser ses chevilles pour tenir le buste droit.
        lean_acc += K_CHEV * (float(np.mean(cible_chevilles)) - self.lean) \
            - REACTION_HANCHE * reaction_hanches
        self.lean_vel += lean_acc * dt
        self.lean += self.lean_vel * dt

        self.t += dt

        # chute ?
        agl = self.y - self.terrain.height(self.x)
        self.fallen = bool(abs(self.lean) > 0.6 or agl < 0.40 or agl > 1.40)
        return self.fallen
