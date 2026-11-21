# -*- coding: utf-8 -*-
"""Construction d'un GLB (glTF 2.0 binaire) riggé, from scratch.

Entrée : un FBXScene (voir fbx.py) issu d'un export Blender/MMD.
Sortie : un .glb avec :
  - le squelette complet (chaque os = un node, en pose de bind),
  - le maillage séparé par matériau (primitives), avec normales et UV,
  - le skinning (JOINTS_0 / WEIGHTS_0, 4 influences max par sommet),
  - les matrices inverses de bind (inverse des TransformLink du FBX),
  - les textures PNG optionnelles (mapping matériau -> fichier).

Choix de cohérence (validé empiriquement sur « Hatsune Mega Mix ») :
  - TransformLink FBX (column-major) = matrice monde de l'os en pose de
    bind. Preuve : TL @ TR == matrice monde du mesh Model (~1e-5).
  - Les sommets sont « cuits » dans l'espace monde-bind (matrix du node
    mesh Applied), le node mesh reste identité ; à la pose de bind,
    Σ(w_i · G_i · IBM_i) = I, donc le rendu == vertices cuits.
  - Une matrice racine D (échelle + offset Y) normalise le modèle (pieds
    au sol, hauteur cible) au-dessus des os uniquement.
  - glTF est en mètres, Y-up : on applique MatrixMesh/100 (elle contient
    déjà la conversion Z-up cm -> Y-up de Blender, échelle 100 annulée).
"""

import json
import struct

import numpy as np

from .fbx import clean_name

GLB_MAGIC = 0x46546C67          # 'glTF'
CHUNK_JSON = 0x4E4F534A         # 'JSON'
CHUNK_BIN = 0x004E4942          # 'BIN\0'


def to_meters(M):
    """Normalise une matrice FBX (cm, échelle 100) -> mètres, SANS casser
    le groupe homogène : bloc linéaire /100, translation /100, M[3,3] = 1.
    (diviser élément par élément donnerait M[3,3] = 0.01 et fausserait
    toutes les compositions : translations ×100.)"""
    M = np.asarray(M, dtype=np.float64)
    Out = np.eye(4)
    Out[:3, :3] = M[:3, :3] / 100.0
    Out[:3, 3] = M[:3, 3] / 100.0
    return Out


def _pad4(n):
    return (-n) % 4


class _Buffer:
    """Tampon binaire glTF, avec helper add(bytes, alignement 4)."""

    def __init__(self):
        self.parts = []
        self.size = 0

    def add(self, data):
        pad = _pad4(self.size)
        if pad:
            self.parts.append(b"\x00" * pad)
            self.size += pad
        off = self.size
        self.parts.append(bytes(data))
        self.size += len(data)
        return off, len(data)

    def finalize(self):
        pad = _pad4(self.size)
        if pad:
            self.parts.append(b"\x00" * pad)
            self.size += pad
        return b"".join(self.parts)


def _node_array(parent, name, dtype=np.float64):
    """Tableau brut stocké comme (unique) propriété du premier enfant nommé."""
    ch = parent.get(name)
    if ch is None or not ch.props:
        return None
    return np.asarray(ch.p(0), dtype=dtype)


# ---------------------------------------------------------------------------
# extraction du contenu utile d'une FBXScene -> structures intermédiaires
# ---------------------------------------------------------------------------

def extract(scene):
    """Renvoie un dict : geometry, skin, os triés hiérarchiquement."""
    gid, geo = next(iter(scene.geometries.items()))

    Vraw = _node_array(geo, "Vertices").reshape(-1, 3)
    Iraw = _node_array(geo, "PolygonVertexIndex", np.int64)
    idx = np.where(Iraw < 0, -Iraw - 1, Iraw).reshape(-1, 3)[:, [0, 2, 1]]

    lay_n = geo.get("LayerElementNormal")
    N = _node_array(lay_n, "Normals").reshape(-1, 3)          # par coin (Direct)
    lay_uv = geo.get("LayerElementUV")
    UV = _node_array(lay_uv, "UV").reshape(-1, 2)
    UVi = _node_array(lay_uv, "UVIndex", np.int64)            # par coin
    lay_m = geo.get("LayerElementMaterial")
    mat_per_poly = _node_array(lay_m, "Materials", np.int64)   # par polygone

    mesh_model_uid = next(iter(scene.mesh_geoms))
    mats_fb = scene.objects.all("Material")
    mid_order = {m.p(0): i for i, m in enumerate(mats_fb)}
    mat_uids = sorted(scene.geo_materials.get(mesh_model_uid, []),
                      key=lambda u: mid_order.get(u, 10**9))

    # ---- os : ordre hiérarchique DFS ----
    parent = scene.parent_of
    roots = [b for b in scene.bones if parent.get(b) not in scene.bones]
    roots.sort(key=lambda b: scene.models[b].p(0))
    ordered = []

    def walk(u):
        ordered.append(u)
        for ch in scene.children_of.get(u, []):
            if ch in scene.bones:
                walk(ch)
    for r in roots:
        walk(r)

    # ---- matrices de bind monde (brutes FBX, échelle incluse) ----
    TL = {}
    defo = {d.p(0): d for d in scene.objects.all("Deformer")}
    for sub, bone in scene.cluster_of.items():
        tl = _node_array(defo[sub], "TransformLink")
        TL[bone] = tl.reshape(4, 4).T

    # ---- influences par sommet de contrôle ----
    infl = {}
    for sub, bone in scene.cluster_of.items():
        ix = _node_array(defo[sub], "Indexes", np.int64)
        wt = _node_array(defo[sub], "Weights")
        for i_, w_ in zip(ix.tolist(), wt.tolist()):
            infl.setdefault(i_, {}).setdefault(bone, 0.0)
            infl[i_][bone] += float(w_)

    return {
        "V": Vraw, "tri": idx, "N": N, "UV": UV, "UVi": UVi,
        "mat_per_poly": mat_per_poly, "mat_uids": mat_uids,
        "mesh_model_uid": mesh_model_uid,
        "bones_ordered": ordered, "TL": TL, "infl": infl,
    }


# ---------------------------------------------------------------------------
# GLB
# ---------------------------------------------------------------------------

def build_glb(scene, out_path, height_target=1.60, tex_dir=None, tex_map=None,
              verbose=False):
    """Écrit un GLB riggé..tex_dir : dossier des PNG ; tex_map : {index mat: nom fichier}.

    Renvoie des statistiques.
    """
    ext = extract(scene)
    Vraw, tri = ext["V"], ext["tri"]
    N, UV, UVi = ext["N"], ext["UV"], ext["UVi"]
    mat_per_poly, mat_uids = ext["mat_per_poly"], ext["mat_uids"]
    ordered, TL = ext["bones_ordered"], ext["TL"]
    infl = ext["infl"]

    A_mesh = scene.global_matrix(ext["mesh_model_uid"], {})

    # normalisation D : pieds au sol (y=min), hauteur cible, centré en x —
    # mesurée dans le MÊME espace meters que les os (to_meters)
    pts = (to_meters(A_mesh)[:3, :3] @ Vraw.T).T
    s = 1.0
    dy = 0.0
    dx = 0.0
    if height_target:
        ymin, ymax = pts[:, 1].min(), pts[:, 1].max()
        s = height_target / (ymax - ymin)
        dy = -s * ymin
        dx = -s * (pts[:, 0].min() + pts[:, 0].max()) / 2.0
    D = np.diag([s, s, s, 1.0])
    D[:3, 3] = (dx, dy, 0.0)

    A_g = D @ to_meters(A_mesh)                   # mesh raw -> monde glTF

    def G(b):                                      # monde glTF de l'os b
        return D @ to_meters(TL[b])

    # ---------------- sommets fusionnés ----------------
    key_map = {}
    pos, nor, uv = [], [], []
    ctrl_of = []
    corner = np.empty(tri.size, dtype=np.int64)
    for k, c in enumerate(tri.reshape(-1)):
        u = UV[UVi[k]]
        n = N[k]
        key = (int(c), n.tobytes(), u.tobytes())
        j = key_map.get(key)
        if j is None:
            j = len(pos)
            key_map[key] = j
            pos.append(Vraw[c])
            nor.append(n)
            uv.append(u)
            ctrl_of.append(c)
        corner[k] = j

    P = np.asarray(pos)
    Pw = (A_g[:3, :3] @ P.T).T + A_g[:3, 3]
    Nw = (A_g[:3, :3] @ np.asarray(nor).T).T
    Nw /= np.maximum(np.linalg.norm(Nw, axis=1, keepdims=True), 1e-12)
    UVw = np.asarray(uv)
    ctrl_v = np.asarray(ctrl_of, dtype=np.int64)

    # ---------------- influences ----------------
    bone_index = {b: i for i, b in enumerate(ordered)}
    J = np.zeros((len(ctrl_v), 4), dtype=np.int32)
    W = np.zeros((len(ctrl_v), 4))
    for k, c in enumerate(ctrl_v.tolist()):
        d = infl.get(c, {})
        top = sorted(d.items(), key=lambda kv: -kv[1])[:4]
        tot = sum(v for _, v in top) or 1.0
        for j, (b, w) in enumerate(top):
            J[k, j] = bone_index[b]
            W[k, j] = w
        W[k] /= W[k].sum()

    # ---------------- buffer + accessors ----------------
    buf = _Buffer()
    jd = {
        "asset": {"version": "2.0",
                  "generator": "miku-ai fbx2glb (from scratch)",
                  "copyright": "Modèle : Hatsune Mega Mix par oscar3dmodel (CC-BY 4.0)"},
        "scene": 0,
        "scenes": [{"nodes": []}],
        "nodes": [], "skins": [], "materials": [], "meshes": [],
        "textures": [], "samplers": [{"magFilter": 9729, "minFilter": 9987,
                                      "wrapS": 10497, "wrapT": 10497}],
        "images": [], "accessors": [], "bufferViews": [],
    }

    def add_view(data, target):
        off, ln = buf.add(data)
        jd["bufferViews"].append({"buffer": 0, "byteOffset": off,
                                  "byteLength": ln, "target": target})
        return len(jd["bufferViews"]) - 1

    def add_acc(view, ctype, count, ncomp, minmax=None):
        acc_ = {"bufferView": view, "componentType": ctype, "count": count,
                "type": {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4",
                         16: "MAT4"}[ncomp]}
        if minmax is not None and ctype == 5126 and ncomp in (2, 3):
            mn = minmax.min(axis=0).tolist()
            mx = minmax.max(axis=0).tolist()
            acc_["min"], acc_["max"] = mn, mx
        jd["accessors"].append(acc_)
        return len(jd["accessors"]) - 1

    v_pos = add_view(np.asarray(Pw, "<f4").tobytes(), 34962)
    a_pos = add_acc(v_pos, 5126, len(Pw), 3, Pw)
    v_nor = add_view(np.asarray(Nw, "<f4").tobytes(), 34962)
    a_nor = add_acc(v_nor, 5126, len(Nw), 3)
    v_uv = add_view(np.asarray(UVw, "<f4").tobytes(), 34962)
    a_uv = add_acc(v_uv, 5126, len(UVw), 2)
    if J.max() <= 255:
        v_j = add_view(J.astype(np.uint8).tobytes(), 34962)
        a_jnt = add_acc(v_j, 5121, len(J), 4)
    else:
        v_j = add_view(J.astype("<u2").tobytes(), 34962)
        a_jnt = add_acc(v_j, 5123, len(J), 4)
    v_w = add_view(np.asarray(W, "<f4").tobytes(), 34962)
    a_wgt = add_acc(v_w, 5126, len(W), 4)
    v_i = add_view(tri.reshape(-1).astype("<u4").tobytes(), 34963)
    a_idx = add_acc(v_i, 5125, tri.size, 1)

    # ---------------- textures ----------------
    used_tex = []
    img_holder = {}

    def add_texture(png_bytes):
        import hashlib
        h = hashlib.sha1(png_bytes).hexdigest()
        if h in img_holder:
            return img_holder[h]
        off, ln = buf.add(png_bytes)
        jd["bufferViews"].append({"buffer": 0, "byteOffset": off, "byteLength": ln})
        jd["images"].append({"bufferView": len(jd["bufferViews"]) - 1,
                             "mimeType": "image/png"})
        jd["textures"].append({"sampler": 0,
                               "source": len(jd["images"]) - 1})
        img_holder[h] = len(jd["textures"]) - 1
        return img_holder[h]

    # ---------------- os -> nodes ----------------
    node_of = {}
    for i, b in enumerate(ordered):
        p = scene.parent_of.get(b)
        Gb = G(b)
        loc = np.linalg.inv(G(p)) @ Gb if p in scene.bones else Gb  # groupe homogène OK
        ni = len(jd["nodes"])
        jd["nodes"].append({
            "name": clean_name(scene.models[b].p(1)) if b in scene.models else f"bone{i}",
            "matrix": [float(loc[r, c]) for r in range(4) for c in range(4)],
        })
        node_of[b] = ni
    for b in ordered:
        p = scene.parent_of.get(b)
        if p in scene.bones:
            jd["nodes"][node_of[p]].setdefault("children", []).append(node_of[b])

    v_ibm = add_view(np.stack([np.linalg.inv(G(b)) for b in ordered])
                     .astype("<f4").tobytes(), None)
    a_ibm = add_acc(v_ibm, 5126, len(ordered), 16)

    # ---------------- primitives par matériau ----------------
    prims = []
    mats_used = []
    for k, mu in enumerate(mat_uids):
        sel = mat_per_poly == k
        if not sel.any():
            continue
        name = "mat"
        for m in scene.objects.all("Material"):
            if m.p(0) == mu:
                name = clean_name(m.p(1))
                break
        tri_sel = tri[sel]
        vi = add_view(tri_sel.reshape(-1).astype("<u4").tobytes(), 34963)
        ai = add_acc(vi, 5125, tri_sel.size, 1)
        prim = {"attributes": {"POSITION": a_pos, "NORMAL": a_nor,
                               "TEXCOORD_0": a_uv, "JOINTS_0": a_jnt,
                               "WEIGHTS_0": a_wgt},
                "indices": ai, "material": len(mats_used), "mode": 4}
        # texture diffuse si(tex_dir et tex_map) ou inhérent
        texf = None
        if tex_map is not None:
            texf = tex_map.get(k)
        if texf is not None and tex_dir:
            import os
            pth = os.path.join(tex_dir, texf)
            with open(pth, "rb") as f:
                tex_i = add_texture(f.read())
            wh, hasAlpha, _ = None, None, None
            raw = open(pth, "rb").read(26)
            ctype = raw[25]
            prim["material"] = len(mats_used)
        else:
            tex_i = None
            ctype = 2
        mat = {"name": name[:48],
               "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1.0],
                                        "metallicFactor": 0.0,
                                        "roughnessFactor": 0.9},
               "doubleSided": True,
               "alphaMode": "BLEND" if ctype in (4, 6) else "OPAQUE"}
        if tex_i is not None:
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex_i}
        jd["materials"].append(mat)
        mats_used.append(mu)
        prims.append(prim)

    jd["meshes"].append({"name": "hatsune_megamix", "primitives": prims})
    skin = {"inverseBindMatrices": a_ibm,
            "joints": [node_of[b] for b in ordered]}
    jd["skins"].append(skin)

    mesh_node = len(jd["nodes"])
    jd["nodes"].append({"name": "hatsune_megamix", "mesh": 0, "skin": 0})
    jd["scenes"][0]["nodes"] = [node_of[b] for b in ordered
                                if scene.parent_of.get(b) not in scene.bones] + [mesh_node]

    jstr = json.dumps(jd, ensure_ascii=False).encode("utf-8")
    jstr += b" " * _pad4(len(jstr))
    bindata = buf.finalize()
    total = 12 + 8 + len(jstr) + 8 + len(bindata)
    with open(out_path, "wb") as f:
        f.write(struct.pack("<III", GLB_MAGIC, 2, total))
        f.write(struct.pack("<II", len(jstr), CHUNK_JSON))
        f.write(jstr)
        f.write(struct.pack("<II", len(bindata), CHUNK_BIN))
        f.write(bindata)

    stats = {"vertices": len(Pw), "triangles": len(tri), "bones": len(ordered),
             "primitives": len(prims), "octets": total, "scale": s,
             "hauteur_source": float(pts[:, 1].max() - pts[:, 1].min())}
    if verbose:
        print(f"GLB : {out_path} — {stats}")
    return stats
