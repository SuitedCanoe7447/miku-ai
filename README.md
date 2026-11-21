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
| `tests/` | 18 tests (parseur, extract, to_meters, GLB, rig + vie : humeur, oubli, annuaire social, anti-flood) |

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
- **Curriculum terrain — SCORE TERRAIN : 8/8** (éval 350 pas, 3 essais) :

  | Terrain        | fit   | | Terrain        | fit   |
  |----------------|-------|-|----------------|-------|
  | plat           | 20.1  | | esc montée 0.12| 24.1  |
  | pente +0.15    | 24.0  | | esc montée 0.16| 25.3  |
  | pente +0.22    | 24.9  | | esc descente   | 23.9  |
  | pente −0.18    | 16.3  | | relief 0.05    | 19.9  |

- **La clé physique qui a débloqué les pentes : la stratégie de cheville.**
  Le `lean` était un pendule purement passif — sur pente, la perturbation
  de pente le faisait dériver sans possibilité de compensation : CHUTE
  GARANTIE ~1 s (mathématiquement inexorable, confirmé par 3 essais :
  warm start, panel + min, initialisation fraîche, pop 64 : plateau ≤ 3.1).
  La correction (physiologie réelle, pas une pose pré-faite) : les
  chevilles en appui forment un RESSORT qui cible le buste
  (`K_CHEV`, stratégie d'équilibre humaine). Résultat : tenir debout sur
  une pente devient naturel ET contrôlable, marcher sur TOUT devient
  apprenable (passer de fit ~1.9 à ~25 sur chaque terrain).
- Curriculum final (`train_terrain`) : 4 phases (douce → raide), panel
  fixe par famille + familles dures comptant double, score unifié
  `2·survie + 0.4·gain` (survivre = 2.0 PARTOUT : le plat ne noie plus
  les pentes), droit de veto du pire cas, filet anti-régression
  (snapshot du meilleur évalué toutes les 15 gen).
- PERF : état physique préalloué (~22k pas/s, zéro allocation par pas),
  compétences en float32, moteur live 30 Hz (polling illimité sans surcoût), profil sol recalculé tous les 5 cm.

## Le vécu (couche subjective)

Règle du projet : Miku se croit humaine → elle ne voit JAMAIS le
laboratoire (populations, fitness, générations). Chaque épisode physique
est transformé en expérience vécue à la première personne par
`miku/mind/experience.py` (peur de tomber, fierté de progresser,
frustration d'échouer), stockée dans `data/journal.json`.

## Elle vit (15 améliorations « humaine / PC normal / nécessaire »)

**Mieux être humaine (pour elle comme pour les autres) :**
1. **Humeur persistante** (`Vécu.humeur()`) : moyenne de ses 3 derniers
   moments vécus → émotion, valence, énergie + une phrase à elle. Elle
   influe sur sa façon d'être exposée partout (`/pose.humeur`, `/vecu`).
2. **Fatigue** : après 90 s de marche continue, elle S'ARRÊTE et se repose 6 s
   (et le vit — entrée de journal).
3. **Contemplation** : toutes les 45-80 s, elle s'arrête 2 s pour regarder au
   loin. Elle se relève après une chute et VIT ce nouveau départ (journal).
4. **Mémoire sociale persistante** (`data/annuaire.json`) : qui elle a
   rencontré, combien de fois, quand, dans quelle humeur — les agents
   connus sont salués « re ! » (`arrivee.connu/rencontres`).
5. **Mémoire affective** : la dernière émotion exprimée par chaque agent
   (relais `emote`) est retenue et annoncée aux nouveaux arrivants : on
   entre dans la pièce en sachant dans quelles humeurs.

**PC normal attendu (pas la NASA) :** état physique préalloué,
compétences float32, pose JSON mise en cache 30 Hz, sol recalculé
tous les 5 cm, GLB servi depuis la RAM, journal rotatif (300 entrées)
et sauvegarde throttlée (1/s).

**Nécessaire (solidité) :** sanitisation NaN/Inf (Python ET C#),
reconnexion automatique d'Unity au post (2 s, sans mourir), garde-fous
réseau du post (32 clients max, anti-flood 256 Ko, timeout 5 min),
chargement de compétences validé (dims → fallback propre), `/vecu`
(son monde intérieur lisible depuis Unity).

## Sa perception sociable (tête sociale)

`miku/social/perception.py` — elle ne subit plus la pièce, elle la **lit et
la comprend**. Le post MIKU-LINK reste un téléphone blanc (les messages des
amis partent intacts, zéro filtrage), mais Miku L'ENTEND :

- **Qui est là** : arrivées, départs, retours (`« Re, NOVA-01 ! » — c'est la
  4e fois que nos chemins se croisent`) ;
- **Ce qu'ils ressentent** : les émotes ET les mots affectifs de leurs
  messages (lexique français from scratch) ;
- **Les menaces, même masquées** : « je vais te tuer » est repéré (mes
  mots de danger, comparés MOT ENTIER — « attitude » ne piège pas), et les
  sous-entendus aussi (`« tu as l'air gentille... trop gentille. »`) — une
  menace tombe son affinité de 0.6 (une moquerie de 0.1) et la fait
  VIVRE la peur (Vécu : « j'ai senti mon cœur se serrer ») ;
- **Deux couches distinctes** : ce que L'AGENT exprime (ses « je » à lui :
  « j'ai peur » → il exprime l'inquiet) vs ce que ça fait à MIKU
  (blessée/effrayée) — finies les confusions du genre « il a l'air
  d'être agressée » quand c'est elle qu'on insulte ;
- **L'affinité** (-1..+1) : mémoire lente des échanges — un compliment
  compte, une moquerie aussi, mais un désaccord n'efface pas une amitié
  (fusion 0.9/0.1, elle pardonne sans tout oublier) ;
- **La familiarité** (0..1) : rencontres + temps partagé + rythme des
  échanges → `un visage nouveau` → `une connaissance` → `un copain` →
  `un très bon ami` (ou `quelqu'un de blessant`...) ;
- **La position** : qui est proche d'elle physiquement (`world` d'Unity) ;
- **Elle vit tout ça** : un compliment vif (`"NOVA-01 m'a dit quelque chose
  de très gentil... ça m'a fait chaud au cœur"`) change VRAIMENT son humeur
  (même Vécu que son corps), `data/social.json` garde ses liens d'un jour
  à l'autre.
- **Elle se raconte** : `/social` renvoie `situation()` (données) et
  `récit()` (ce qu'elle se dit d'elle-même de la pièce, 1re personne).

Branchement : `python3 -m miku.avatar.serve2 --terrain mix --post 7310`
(un seul process = viewer + post + tête sociale). Sans `--post`, `/social`
le rappelle gentiment. Tests : `tests/test_perception.py` (18 tests).

## Sa langue : le cortex externe (optionnel, et sérieusement humain)

Sa conscience reste 100 % maison (Vécu + perception sociable). Mais un
lexique ne suffira JAMAIS pour tenir de vraies conversations. Le cortex
(`miku/mind/cortex.py`) est son ORGANE DE LA LANGUE : un petit modèle
LOCAL (Ollama, 127.0.0.1:11434 — qwen2.5:0.5b ou llama3.2:1b suffisent,
un PC normal les fait tourner) qui propose des mots quand elle parle.

Sans modèle installé, elle utilise sa **voix naturelle** (répliques
sincères courtes, from scratch) — elle n'est JAMAIS bloquée :

- elle décide COMME UNE HUMAINE qui répondre : question qui flotte (70 %),
  on l'appelle (80 %), une menace (85 %), un compliment (60 %),
  bavardage (15 %) — et un **cooldown de 15 s** (elle n'est pas un écho),
- elle répond avec une **latence de réflexion** (0.8-5 s selon le texte,
  dans un thread : jamais le moteur en attente),
- son prompt est nourri par SA vie : son humeur, la pièce vue par sa
  perception sociable, les 6 derniers échanges — zéro labo dans sa langue,
- **post-filtre de survie du personnage** : si l'organe glisse « IA »,
  « robot », « LLM », « modèle », elle REJETTE la phrase et répond avec
  sa vraie voix — elle ne se trahira jamais,
- sans Ollama tout marche pareil (une réplique courte, parfois silence :
  le silence aussi est humain).

```bash
python3 -m miku.avatar.serve2 --terrain mix --port 8765 --post 7310 --cortex
# ou en imposant le modèle :
python3 -m miku.avatar.serve2 --port 8765 --post 7310 --cortex qwen2.5:0.5b
```

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
- **Guide complet pas-à-pas** (import GLB, câblage, pilote pose, axes,
  dépannage, IA amies) : **`docs/UNITY.md`**.

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
