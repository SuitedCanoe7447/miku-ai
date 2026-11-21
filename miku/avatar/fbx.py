# -*- coding: utf-8 -*-
"""Parseur FBX binaire, écrit from scratch (aucune dépendance externe).

Supporte le format « Kaydara FBX Binary » versions 7.x (7400 testé),
celui qu'exporte Blender et la plupart des logiciels 3D. Le fichier est
une arborescence de nœuds `<nom> <propriétés> <enfants>` ; on la lit
récursivement puis on expose des aides d'accès (propriétés typées,
recherche par chemin, listes filtrées).

Livre « FBX binary file structure » :
  - en-tête 23 octets : "Kaydara FBX Binary  " + 2 x 0x1A 0x00 + version u32
  - ensuite des enregistrements (format v>=7500 : u64 au lieu de u32) :
        end_offset  u32   (position absolue de la fin du nœud+récursion)
        n_props     u32
        props_len   u32   (taille cumulée de la liste de propriétés)
        name_len    u8
        name        name_len octets
        props       props_len octets
        enfants     (jusqu'à end_offset) ; si vide : null record de 13 octets
  - un nœud se termine par un enregistrement nul (13 octets à zéro)

Formats de propriété (1 octet code puis payload u32 little-endian) :
  Y:i16  C:u8(bool)  I:i32  F:f32  D:f64  L:i64
  f,d,l,i,b : listes brutes   (u32 n, puis n éléments)
  c : liste de bool         (u32 n, octets packés bit à bit)
  S:string, R:raw           (u32 len, puis octets)
  listes compressées (ex 'D' + flags 0x0E ?) : deflated
        u32 array_len, u32 encoding, u32 comp_len, payload
"""

import struct
import zlib
import numpy as np


# ---------------------------------------------------------------------------
# Lecture bas niveau
# ---------------------------------------------------------------------------

class FBXError(Exception):
    """Erreur de lecture d'un fichier FBX binaire."""


def _read_prop(data, off):
    """Lit une propriété à `off`, renvoie (valeur, nouvel_offset).

    Les listes numériques sont directement converties en numpy (float64 /
    int32 / int64 selon le code) pour un accès vectorisé ultérieur ; les
    scalaires restent en types Python simples.
    """
    code = data[off]
    hoff = off + 1
    simple = {
        0x59: ("<h", 2, int),    # Y i16
        0x43: ("<B", 1, int),    # C bool
        0x49: ("<i", 4, int),    # I i32
        0x46: ("<f", 4, float),  # F f32
        0x44: ("<d", 8, float),  # D f64
        0x4C: ("<q", 8, int),    # L i64
    }
    if code in simple:
        fmt, size, cast = simple[code]
        v = cast(struct.unpack_from(fmt, data, hoff)[0])
        return v, hoff + size

    if code in (0x53, 0x52):  # S string / R raw
        n = struct.unpack_from("<I", data, hoff)[0]
        raw = data[hoff + 4:hoff + 4 + n]
        if code == 0x53:
            try:
                return raw.decode("utf-8", errors="replace"), hoff + 4 + n
            except Exception:
                return raw, hoff + 4 + n
        return bytes(raw), hoff + 4 + n

    # listes
    array_codes = {
        0x66: ("<f", 4, np.float32),  # f
        0x64: ("<d", 8, np.float64),  # d
        0x6C: ("<q", 8, np.int64),    # l
        0x69: ("<i", 4, np.int32),    # i
        0x62: ("<B", 1, np.uint8),    # b
        0x63: ("<B", 1, np.uint8),    # c bool packé (traité à part)
    }
    if code in array_codes:
        n, enc, comp = struct.unpack_from("<III", data, hoff)
        body = hoff + 12
        if enc:
            raw = zlib.decompress(data[body:body + comp])
        else:
            raw = data[body:body + comp]
        if code == 0x63:
            # bits packés : déballage (n booléens)
            bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))
            return bits[:n].astype(np.uint8), body + comp
        fmt, size, dtype = array_codes[code]
        if enc:
            expected = n * size
            if len(raw) < expected:  # sécurité
                raw = raw + b"\x00" * (expected - len(raw))
        else:
            expected = n * size
            if len(raw) < expected:
                raw = raw + b"\x00" * (expected - len(raw))
        dmap = {0x66: np.float32, 0x64: np.float64,
                0x6C: np.int64, 0x69: np.int32, 0x62: np.uint8}
        arr = np.frombuffer(raw, dtype=dmap[code], count=n)
        return arr, body + comp

    raise FBXError(f"Code de propriété inconnu 0x{code:02X} à l'offset {off}")


class Node:
    """Nœud FBX : nom + propriétés+hierarchie d'enfants (vieux format d'arbre sans dict)."""

    __slots__ = ("name", "props", "children")

    def __init__(self, name, props=None):
        self.name = name
        self.props = props or []
        self.children = []

    # -- accès rapide -------------------------------------------------------
    def p(self, i):
        """Propriété numéro i (ou None)."""
        return self.props[i] if i < len(self.props) else None

    def get(self, name, default=None):
        """Premier enfant portant ce nom."""
        for c in self.children:
            if c.name == name:
                return c
        return default

    def all(self, name):
        """Tous les enfants portant ce nom."""
        return [c for c in self.children if c.name == name]

    def prop_of(self, name, idx=0, default=None):
        """Valeur de la propriété idx du premier enfant nommé `name`."""
        c = self.get(name)
        if c is None:
            return default
        return c.p(idx) if idx < len(c.props) else default

    # 70 == liste "Properties70", dont les entrées P sont :
    #   [name, type, label, flag, ...valeurs...] (1 à 3 valeurs scalaires)
    def props70(self):
        """Renvoie {nom: valeur} de la liste Properties70 (plat).

        Les valeurs multi-composantes (vectors, par ex. Lcl Translation)
        sont renvoyées en np.ndarray 1D ; les scalaires en valeur brute.
        """
        out = {}
        p70 = self.get("Properties70")
        if p70 is None:
            return out
        for p in p70.all("P"):
            vals = p.props[4:]
            if len(vals) >= 2:
                out[p.props[0]] = np.asarray(vals, dtype=np.float64)
            elif len(vals) == 1:
                out[p.props[0]] = vals[0]
        return out

    def __repr__(self):
        return f"<Node {self.name} props={len(self.props)} child={len(self.children)}>"


# ---------------------------------------------------------------------------
# Lecture d'arbre
# ---------------------------------------------------------------------------

def _read_node(data, off, ver):
    """Lit un nœud à `off`. Renvoie (Node|None, nouvel_offset).

    Renvoie (None, off) sur enregistrement nul (fin de liste d'enfants).
    """
    # en-tête : end_offset, n_props, props_len puis name_len sur 1 octet.
    # v>=7500 : champs 64 bits, en-tête 25 octets, null = 25 zéros.
    # v<7500  : champs 32 bits, en-tête 13 octets, null = 13 zéros.
    if ver >= 7500:
        end_pos, n_props, props_len = struct.unpack_from("<QQQ", data, off)
        name_len = data[off + 24]
        hsize = 25
    else:
        end_pos, n_props, props_len = struct.unpack_from("<III", data, off)
        name_len = data[off + 12]
        hsize = 13
    if end_pos == 0 and n_props == 0 and props_len == 0 and name_len == 0:
        return None, off + hsize
    name = data[off + hsize:off + hsize + name_len].decode("utf-8", errors="replace")
    node = Node(name)
    poff = off + hsize + name_len
    for _ in range(n_props):
        v, poff = _read_prop(data, poff)
        node.props.append(v)
    # enfants : lisibles uniquement dans la fenêtre props_fin -> end_pos ;
    # la region se termine par un enregistrement nul.
    end = end_pos
    coff = poff
    if coff < end:
        while coff < end - hsize:
            child, coff = _read_node(data, coff, ver)
            if child is None:
                break
            node.children.append(child)
    return node, end


def load(src):
    """Charge un FBX binaire (chemin ou bytes) et renvoie (arbre_racine, version)."""
    if isinstance(src, bytes):
        data = src
    else:
        with open(src, "rb") as f:
            data = f.read()
    if not data.startswith(b"Kaydara FBX Binary"):
        raise FBXError("Ce fichier n'est pas un FBX binaire.")
    ver = struct.unpack_from("<I", data, 23)[0]
    root = Node("root")
    off = 27
    while off < len(data) - 12:
        child, off = _read_node(data, off, ver)
        if child is None:
            break
        root.children.append(child)
    return root, ver


# ---------------------------------------------------------------------------
# Aides de haut niveau : decomposer l'arbre en objets exploitables
# ---------------------------------------------------------------------------

def _vec(props70, key, default=(0.0, 0.0, 0.0)):
    v = props70.get(key)
    if v is None:
        return np.array(default, dtype=np.float64)
    if isinstance(v, np.ndarray) and v.size >= 3:
        return np.asarray(v, dtype=np.float64).reshape(-1)[:3].copy()
    return np.array(default, dtype=np.float64)


def _quat_from_euler(rx, ry, rz, order="XYZ"):
    """Quaternion (x,y,z,w) depuis des angles Euler en DEGRÉS (ordre Blender XYZ).

    Convention Blender exportée par FBX : R = Rz @ Ry @ Rx (appliqué en
    XYZ intrinsèque). On suit le comportement de l'importeur three.js
    pour un ordre 'XYZ' : q = qx * qy * qz... Non : three.js Euler 'XYZ'
    intrinsèque => R = Rx @ Ry @ Rz. FBX eEulerXYZBlender correspond déjà
    à l'ordre XYZ standard ; on applique R = Rx @ Ry @ Rz.
    """
    # demi-angles (un quaternion de rotation utilise la demi-rotation !)
    rx, ry, rz = np.radians(rx) / 2, np.radians(ry) / 2, np.radians(rz) / 2
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    qx = np.array([sx, 0.0, 0.0, cx])
    qy = np.array([0.0, sy, 0.0, cy])
    qz = np.array([0.0, 0.0, sz, cz])
    # ordre XYZ (Blender/FBX eEulerXYZ) : R = Rx @ Ry @ Rz  <=>  q = qx @ qy @ qz
    return _quat_mul(_quat_mul(qx, qy), qz)


def _rot_mat_from_quat(q):
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=np.float64)


def _tr_compat(T, S, R, e):
    """Matrice homogène 4x4 depuis translation/scaling/rotation(quat)."""
    M = np.eye(4, dtype=np.float64)
    M[:3, :3] = _rot_mat_from_quat(R) * S
    M[:3, 3] = T
    return M


class FBXScene:
    """Représentation simplifiée d'une scène FBX : nœuds, meshes, mat, skin."""

    def __init__(self, path):
        self.root, self.version = load(path)
        objects = self.root.get("Objects")
        if objects is None:
            raise FBXError("Pas de section Objects dans le FBX.")
        self.objects = objects
        connections = self.root.get("Connections")
        self.connections = connections if connections is not None else Node("Connections")

        # 1) Models (os + meshes) ------------------------------------------------
        self.models = {}          # uid -> Node
        self.model_attrs = {}     # uid -> NodeAttribute (TypeFlags LimbNode/Null/Mesh)
        for m in objects.all("Model"):
            self.models[m.p(0)] = m
        for na in objects.all("NodeAttribute"):
            self.model_attrs[na.p(0)] = na

        # 2) Géométrie -----------------------------------------------------------
        self.geometries = {g.p(0): g for g in objects.all("Geometry")}

        # 3) Connexions OO -------------------------------------------------------
        self.parent_of = {}       # uid enfant -> uid parent
        self.children_of = {}
        self.mesh_geoms = {}      # Model mesh -> Geometry uid
        self.mesh_bones = {}      # Model mesh -> [uid os]
        self.geom_deformers = {}  # Geometry -> [Deformer skin]
        self.bone_cluster = {}    # os uid -> Deformer SubDeformer cluster
        self.geo_materials = {}   # Geometry -> [Material uid]
        self.tex_video = {}       # Texture -> Video uid
        self.mat_textures = {}    # Material -> {DiffuseColor: Texture uid}
        self.resolve_connections()

    # ------------------------------------------------------------------
    # Toutes les connexions OO/OP (une seule passe), routées par types.
    #   - "OO a b" : liaison a -> b (parent/enfant, os/deformer, etc.)
    #   - "OP a b <prop>" : a -> b via une propriété (matériau -> tex)
    #   Pour Blender : l'enfant est le 1er argument, le parent le 2e.
    # ------------------------------------------------------------------
    def resolve_connections(self):
        objects = self.objects
        model_ids = set(self.models)
        geom_ids = set(self.geometries)

        # Blender nomme « Deformer » les skins ET les clusters :
        # on les distingue par le sous-type (props[2]) : "Skin" / "Cluster".
        skin_ids = set()
        cluster_ids = set()
        for d in objects.all("Deformer"):
            subtype = d.p(2) or ""
            if subtype == "Cluster":
                cluster_ids.add(d.p(0))
            else:
                skin_ids.add(d.p(0))
        material_ids = {m.p(0): m for m in objects.all("Material")}
        texture_ids = {t.p(0): t for t in objects.all("Texture")}
        video_ids = {v.p(0): v for v in objects.all("Video")}

        parent_of = {}
        children_of = {}
        mesh_geoms = {}
        geom_deformers = {}      # geometry -> [skin]
        cluster_of = {}          # cluster -> os (cluster connecté au LimbNode)
        clusters_of_skin = {}    # skin -> [cluster]
        geo_materials = {}
        mat_textures = {}        # matériau -> {"DiffuseColor": tex|...}
        tex_video = {}

        for c in self.connections.all("C"):
            props = c.props
            if len(props) < 3:
                continue
            # format : (kind, uid_enfant_source, uid_parent_cible [, propriété])
            kind = props[0]
            a, b = props[1], props[2]
            if kind not in ("OO", "OP"):
                continue
            rel = props[3] if len(props) > 3 and isinstance(props[3], str) else ""
            if a in model_ids and b in model_ids:
                parent_of[a] = b
                children_of.setdefault(b, []).append(a)
            elif a in geom_ids and b in model_ids:
                mesh_geoms[b] = a
            elif a in model_ids and b in geom_ids:  # sens inversé (rare)
                mesh_geoms[a] = b
            elif a in cluster_ids and b in skin_ids:
                clusters_of_skin.setdefault(b, []).append(a)
            elif b in cluster_ids and a in skin_ids:
                clusters_of_skin.setdefault(a, []).append(b)
            elif a in cluster_ids and b in model_ids:
                cluster_of[a] = b
            elif b in cluster_ids and a in model_ids:
                cluster_of[b] = a
            elif a in skin_ids and b in geom_ids:
                geom_deformers.setdefault(b, []).append(a)
            elif b in skin_ids and a in geom_ids:
                geom_deformers.setdefault(a, []).append(b)
            elif a in material_ids and b in texture_ids:
                mat_textures.setdefault(a, {}).setdefault(rel if rel else "DiffuseColor", b)
            elif a in texture_ids and b in material_ids:
                mat_textures.setdefault(b, {})[rel if rel in ("DiffuseColor", "NormalMap", "Bump", "SpecularColor") else "DiffuseColor"] = a
            elif a in material_ids and b in model_ids:
                # Blender : le matériau est connecté au node mesh (ordre = index)
                geo_materials.setdefault(b, []).append(a)
            elif a in model_ids and b in material_ids:
                geo_materials.setdefault(a, []).append(b)
            elif a in texture_ids and b in video_ids:
                tex_video[a] = b
            elif a in video_ids and b in texture_ids:
                tex_video[b] = a
            elif a in material_ids and b in geom_ids:
                geo_materials.setdefault(b, []).append(a)  # matériau -> geometry (rare)
            # autres cas ignorés (animation, poses...)

        self.geo_materials = geo_materials
        self.mat_textures = mat_textures
        self.tex_video = tex_video
        self.cluster_of = cluster_of
        self.clusters_of_skin = clusters_of_skin
        self.parent_of = parent_of
        self.children_of = children_of
        self.geom_deformers = geom_deformers

        # os = Models marqués LimbNode ou cibles d'un cluster
        bone_uids = set(cluster_of.values())
        for uid, na in self.model_attrs.items():
            tf = na.prop_of("TypeFlags", 0, "") or ""
            if tf == "LimbNode":
                bone_uids.add(uid)
        self.bone_uids = bone_uids
        self.bones = {uid: self.models[uid] for uid in bone_uids if uid in self.models}
        self.mesh_geoms = {
            m: g for m, g in mesh_geoms.items()
            if m not in bone_uids
        }

    # -- extraction utilitaires ---------------------------------------------

    def bone_transforms(self, uid):
        """(T, R_quat, S) locaux d'un Model/os depuis ses Properties70."""
        p = self.models[uid].props70()
        t = _vec(p, "Lcl Translation")
        r = _vec(p, "Lcl Rotation")
        s = _vec(p, "Lcl Scaling", (1.0, 1.0, 1.0))
        pre = _vec(p, "PreRotation")
        R = _quat_from_euler(*r)
        if np.any(np.abs(pre) > 1e-9):
            qpre = _quat_from_euler(*pre)
            # post-multiplication : R = R_pre * R_local (three.js fait pareil)
            R = _quat_mul(qpre, R)
        return t, R, s

    def global_matrix(self, uid, cache=None):
        """Matrice monde du nœud (récursif vers le parent)."""
        if cache is None:
            cache = {}
        if uid in cache:
            return cache[uid]
        t, R, s = self.bone_transforms(uid)
        M = _tr_compat(t, s, R, None)
        par = self.parent_of.get(uid)
        if par is not None and par in self.models:
            M = self.global_matrix(par, cache) @ M
        cache[uid] = M
        return M

    # -- dumper texte du squelette (debug) ----------------------------------

    def skeleton_text(self):
        """Texte indente de la hiérarchie des os avec positions monde."""
        cache = {}
        bones = set(self.bones)
        # racines = os sans parent os
        roots = [b for b in bones if self.parent_of.get(b) not in bones]
        lines = []

        def walk(uid, depth):
            M = self.global_matrix(uid, cache)
            name = clean_name(self.models[uid].p(1))
            lines.append(
                "  " * depth + f"{name:<22} pos=({M[0,3]:8.3f},{M[1,3]:8.3f},{M[2,3]:8.3f})"
            )
            for ch in self.children_of.get(uid, []):
                if ch in bones:
                    walk(ch, depth + 1)

        for r in roots:
            walk(r, 0)
        return "\n".join(lines)



def clean_name(raw):
    """Nettoie un nom FBX Blender : 'bone11\x00\x01Model' -> 'bone11'."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return raw.split("\x00\x01")[0]


def _quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ], dtype=np.float64)
