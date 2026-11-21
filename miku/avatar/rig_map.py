# -*- coding: utf-8 -*-
"""Rig map : identifie les rôles d'os du squelette (géométrie + vérifs).

Pour « Hatsune Mega Mix », le squelette a été décrypté à partir des
matrices de bind (TransformLink) :
  - deux racines au même point (bassin) : bone1 (jambe gauche) et
    bone3 (jambe droite + colonne = racine principale) ;
  - jambes : hip=(2,4), knee=(5,6), cheville=(7,8), orteil=(11,12) ;
  - colonne : bone19 (épine) -> bone20 (torse) -> bone21 (nuque) ->
    bone75 (crâne) ; bras via bone27/bone28 (côté +x) et bone50/bone51.

Le module teste ces hypothèses géométriquement (symétrie des jambes,
colonne montante) ; sinon il retombe sur une détection générique
(descendants, ascendants, branches latérales).

Résultat : data/rig_map.json {role: nom_d'os}.
"""

import json
import os

import numpy as np

from .fbx import clean_name


def build_rig_map(scene, out_path=None, verbose=True):
    from .gltf_builder import extract
    ext = extract(scene)
    TL = ext["TL"]
    bones = set(ext["bones_ordered"])
    kids = lambda u: [c for c in scene.children_of.get(u, []) if c in bones]
    name = lambda u: clean_name(scene.models[u].p(1)) if u in scene.models else str(u)
    pos = lambda u: TL[u][:3, 3]
    by_name = {name(b): b for b in bones}

    roles = {}

    # ------------------------------------------------------------
    # 1) mapping connu du modèle Mega Mix, testé géométriquement
    # ------------------------------------------------------------
    needed = ("bone1", "bone2", "bone3", "bone4", "bone5",
              "bone6", "bone19", "bone20")
    if all(n in by_name for n in needed):
        p1 = TL[by_name["bone1"]][:3, 3]
        p3 = TL[by_name["bone3"]][:3, 3]
        p2 = TL[by_name["bone2"]][:3, 3]
        p4 = TL[by_name["bone4"]][:3, 3]
        p5 = TL[by_name["bone5"]][:3, 3]
        p6 = TL[by_name["bone6"]][:3, 3]
        p19 = TL[by_name["bone19"]][:3, 3]
        sym_legs = (abs(p2[0] + p4[0]) < 2.0 and
                    abs((p2 - p5)[1] - (p4 - p6)[1]) < 2.0)
        twin_roots = np.linalg.norm(p1 - p3) < 5.0
        spine_up = p19[1] > p3[1] + 100.0
        if sym_legs and twin_roots and spine_up:
            known = {
                "pelvis": "bone3", "pelvis2": "bone1",
                "spine": "bone19", "chest": "bone20", "neck": "bone21",
                "head": "bone75",
                "shoulderR": "bone27", "upperArmR": "bone28",
                "shoulderL": "bone50", "upperArmL": "bone51",
                "hipR": "bone4", "kneeR": "bone6", "footR": "bone8",
                "hipL": "bone2", "kneeL": "bone5", "footL": "bone7",
            }
            roles = {k: v for k, v in known.items() if v in by_name}
            for s, ua in (("R", "bone28"), ("L", "bone51")):
                cu = by_name[ua]
                ch = kids(cu)
                if ch:
                    roles[f"lowerArm{s}"] = name(ch[0])
                    ch2 = kids(ch[0])
                    if ch2:
                        roles[f"hand{s}"] = name(ch2[0])
    # ------------------------------------------------------------
    # 2) fallback : détection géométrique générique
    # ------------------------------------------------------------
    if not roles:
        roots = [b for b in ext["bones_ordered"]
                 if scene.parent_of.get(b) not in bones]

        def subtree(u):
            n, st = 1, [u]
            while st:
                x = st.pop()
                st.extend(kids(x))
                n += 1
            return n

        main = max(roots, key=subtree)
        roles["pelvis"] = name(main)

        def leg_chain(k):
            chain = [k]
            cur = k
            while True:
                nxt = [c for c in kids(cur) if pos(c)[1] < pos(cur)[1] - 20.0]
                if not nxt:
                    break
                cur = max(nxt, key=lambda c: -pos(c)[1])
                chain.append(cur)
            return chain

        legs = []
        for r in roots:
            for k in kids(r):
                if pos(k)[1] < pos(r)[1] - 50.0:
                    ch = leg_chain(k)
                    if len(ch) >= 3:
                        legs.append(ch)
        legs.sort(key=lambda ch: pos(ch[0])[0])
        if len(legs) >= 2:
            for s, ch in (("L", legs[0]), ("R", legs[1])):
                roles[f"hip{s}"] = name(ch[0])
                roles[f"knee{s}"] = name(ch[1])
                roles[f"foot{s}"] = name(ch[2])
        cur = main
        spine = [main]
        while True:
            nxt = [k for k in kids(cur) if pos(k)[1] > pos(cur)[1] + 50.0]
            if not nxt:
                break
            cur = max(nxt, key=lambda k: pos(k)[1])
            spine.append(cur)
        if len(spine) > 1:
            roles["spine"] = name(spine[1])
        if len(spine) > 2:
            roles["chest"] = name(spine[2])
        if len(spine) > 3:
            roles["neck"] = name(spine[-2])
        roles["head"] = name(spine[-1])
        if "chest" in roles and roles["chest"] in by_name:
            cu = by_name[roles["chest"]]
            cand = []
            for k in kids(cu):
                if name(k) in roles.values():
                    continue
                ch = kids(k)
                if ch and abs(pos(ch[0])[0]) > abs(pos(k)[0]) + 50.0:
                    cand.append((k, ch[0]))
            cand.sort(key=lambda t: pos(t[1])[0])
            if len(cand) >= 2:
                for s, (a, c) in (("L", cand[0]), ("R", cand[1])):
                    roles[f"shoulder{s}"] = name(a)
                    roles[f"upperArm{s}"] = name(c)
                    ch3 = kids(c)
                    if ch3:
                        roles[f"lowerArm{s}"] = name(ch3[0])
                        ch4 = kids(ch3[0])
                        if ch4:
                            roles[f"hand{s}"] = name(ch4[0])

    if verbose:
        for k in sorted(roles):
            print(f"  {k:12} {roles[k]}")

    if out_path:
        d = os.path.dirname(out_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(roles, f, ensure_ascii=False, indent=1)
    return roles
