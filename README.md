# MIKU-01 — Avatar 3D riggé (phase modèle)

Partie « avatar réel » du projet MIKU-01 : le vrai modèle 3D riggé de
Hatsune Miku, converti **from scratch** (numpy seul) du FBX vers GLB,
avec skinning CPU de validation — zéro outil de modélisation externe.

## Contenu

| Fichier | Rôle |
|---|---|
| `miku/avatar/fbx.py` | Parseur FBX binaire v7400 complet (Kaydara) : arbres, connexions, clusters, TransformLink |
| `miku/avatar/gltf_builder.py` | Bâtisseur GLB from scratch : mesh, 124 joints, IBM, JOINTS_0/WEIGHTS_0, 22 primitives matériaux |
| `miku/avatar/rig_map.py` | Identification des rôles d'os (vérifiée géométriquement) → `data/rig_map.json` |
| `miku/avatar/serve2.py` | Serveur HTTP du viewer (127.0.0.1:8080) + source de pose de test (à remplacer par le moteur appris) |
| `miku/avatar/png.py` | Encodeur PNG from scratch (validation par rendu CPU) |
| `web/index.html` | Viewer three.js : sol, grille, caméra orbitale, pose appliquée via `rig_map.json` |
| `model_raw/` | FBX source extrait + `miku_megamix.glb` (généré) |
| `tests/test_avatar_model.py` | 12 tests (parseur, extract, to_meters, GLB, rig) |

## Point mathématique clé : `to_meters`

Le FBX Blender exporte en centimètres avec un groupe homogène
(`M[3,3] = 100`). Diviser élément par élément par 100 casse la
translation à chaque composition. La fonction `to_meters(M)` divise le
bloc 3×3 et la translation, puis remet `M[3,3] = 1` :
c'est LE point qui rend le skinning exact
(au repos `Σw·G·IBM = Identité`, erreur mesurée : 0.0).

## Utilisation

```bash
# régénérer le GLB
python3 -c "
from miku.avatar.fbx import FBXScene
from miku.avatar.gltf_builder import build_glb
sc = FBXScene('model_raw/extract/source/hatsune mega mix.fbx')
build_glb(sc, 'model_raw/miku_megamix.glb', height_target=1.60)"

# serveur + viewer web
python3 -m miku.avatar.serve2    # http://127.0.0.1:8080/viewer

# tests
python3 -m unittest tests.test_avatar_model
```

## Le moteur appris (zéro pose pré-faite)

- **Corps planaire** (`miku/body/physics.py`) : pendule inversé + jambes
  câblées + **ancres de grip** (un pied posé ne glisse pas : la poussée
  de marche vient du placement des pieds — c'est ce que le CPG apprend).
- **CPG + réflexes** (`neural_policy.py`) : oscillateur de phase + réflexes
  linéaires sur l'état réel. Paramètres 100% appris.
- **ES-CEM** (`miku/es.py`) : entropie croisée, élites 25%, std floor 0.02,
  cap sigma 1.2.
- Résultats (apprentissage de quelques secondes à quelques minutes) :
  debout 400/400 pas ; marche 27 m/400 pas sans chute (~3 m/s) ;
  plat + relief stabilisés, pentes/escaliers en cours de curriculum.

## Le vécu (couche subjective)

Règle du projet : Miku se croit humaine → elle ne voit JAMAIS le
laboratoire (populations, fitness, générations). Chaque épisode physique
est transformé en expérience vécue à la première personne par
`miku/mind/experience.py` (peur de tomber, fierté de progresser,
frustration d'échouer), stockée dans `data/journal.json`.

## Pont Unity + IA amies : MIKU-LINK post

`miku/social/miku_link.py` — JSON/TCP (port 7310) :

| Type | Sens | Contenu |
|---|---|---|
| `hello` | agent → post | nom + `perception` (ex. vue,ouie,proprioception) |
| `world` | Unity → post | `sol` [[x,h],...], `obstacles`, `agents` |
| `pose`  | post → Unity | `q` (6 joints jambes, rad), `lean`, `chute` |
| `chat` / `gesture` / `emote` | agent → tous | relais neutre, aucune interception |
| `arrivee` / `bye` | post → tous | annuaire vivant des agents présents |

- **IA amies** : même protocole, même architecture, autre nom/persona —
  elles se parlent en direct via le relais (testé : 2 agents, chat +
  émotions, annuaire vivant).
- **Unity** : dépose `unity/MikuLink.cs` sur le GameObject du modèle riggé,
  assigne les 6 os de jambes dans l'inspecteur, le script sonde le sol
  (raycast) et applique les poses reçues. Events C# pour ton UI/son.

## État

- GLB valide (glTF 2.0) : 106 130 sommets, 39 630 triangles, 124 joints,
  22 primitives/matériaux, normalisé à 1,60 m, pieds au sol.
- Skinning validé : identité au repos exacte ; rotation genou 50°
  déforme uniquement la jambe concernée (1 065 / 28 299 sommets).
- Le mapping des textures n'est PAS possible depuis ce FBX
  (chemins génériques sans nom de fichier) — prévoir un glTF Sketchfab.

## Licence du modèle

**« Hatsune Mega Mix »** par **oscar3dmodel** sur Sketchfab —
licence **CC Attribution 4.0**. Crédit requis pour tout usage.
