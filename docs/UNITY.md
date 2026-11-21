# Voir Miku vivre dans ton monde Unity — guide pas-à-pas

Ce guide suppose que **miku-ai** est cloné et que Python ≥ 3.10 + numpy sont
installés. À la fin, la Miku que Miku-01 a *apprise toute seule* (aucune pose
pré-faite : la politique motrice vient de `miku_skill_*.npz`, entraînés par
ES/CEM from scratch) marchera dans TON décor Unity, reliée par MIKU-LINK
(JSON/TCP, port 7310 par défaut).

---

## 0. Le principe (30 secondes)

- **Côté Python** : le moteur physique (`miku/body/`) fait marcher le corps
  planaire avec la politique apprise ; un serveur HTTP (`miku/avatar/serve2.py`)
  expose la pose en temps réel, et le « post » MIKU-LINK post
  (`miku/social/miku_link.py`) parle en JSON/TCP avec le monde.
- **Côté Unity** : un composant C#, `unity/MikuLink.cs`, se raccroche au
  squelette de ton avatar, sonde le sol (raycasts) et envoie le message
  `world` ; il reçoit en retour les messages `pose` (angles des 6 joints de
  jambes) et `chat`/`emote`/`arrivee` (la vie sociale) qu'il expose en events C#.
- Miku ne « sait » rien d'Unity : elle reçoit juste un profil de sol et répond
  avec des angles de jambes. Son vécu subjectif (journal, émotions) ne
  mentionne jamais de fitness ni d'entraînement — elle croit marcher elle-même.

```
[ton monde Unity] ──MikuLink.cs──TCP 7310──> MikuLinkPost (post Python)
        raycasts sol  → "world"                 │
        "chat" (tes PNJ, tes IA amies)          ├─> moteur physique (politique apprise)
        <─ "pose" q[6] + lean + chute  <────────┘
        <─ "chat"/"emote" relayés aux amis IA
```

---

## 1. Importer le modèle riggé dans Unity

1. Ouvre ton projet Unity (2021.3+ conseillé, Built-in ou URP).
2. Copie **`model_raw/miku_megamix.glb`** (dépôt `SuitedCanoe7447/miku-ai`)
   dans ton dossier `Assets/Models/`. Unity importe le GLB/glTF :
   - Unity 2022+ : import automatique en équivalent FBX ;
   - sinon (ou pour un rendu identique au viewer) : installe **glTFast**
     (Package Manager → « Install package by name » → `com.unity.cloud.gltfast`),
     mets le `.glb` dans `Assets/StreamingAssets/` et instancie-le avec le
     composant `GltfAsset`.
3. Onglet **Rig** des import settings : **Animation Type = Generic**. Pas
   besoin de Humanoid : la pose est appliquée os par os, pas par le système
   d'animation d'Unity (et c'est le moteur MIKU qui « anime », dehors).
4. Échelle : le GLB exporté est déjà normalisé à **1,60 m** (conversion
   « en mètres » faite dans le constructeur). En scène la taille rendue doit
   être ≈ 1,6 unité ≈ 1,6 m. Si elle est 100× trop grande (fbx en cm),
   *Convert Units* ou un parent avec scale 0.01.
5. Déplie le préfabriqué dans la scène et repère l'os d'origine FBX (124 os) :
   les 6 utiles pour la marche sont repérables par la **hiérarchie** (pas par
   les noms, qui sont `bone5`, `bone6`, `bone27`… sur ce modèle) :

   | slot `jointsJambes` | rôle físico du moteur | comment le trouver          |
   |---------------------|------------------------|-----------------------------|
   | 0 | hanche droite   | le parent direct de la cuisse R |
   | 1 | genou droit     | l'enfant de la cuisse R       |
   | 2 | cheville droite | l'enfant du tibia R           |
   | 3 | hanche gauche   | le parent direct de la cuisse L |
   | 4 | genou gauche    | l'enfant de la cuisse L       |
   | 5 | cheville gauche | l'enfant du tibia L           |

---

## 2. Brancher MikuLink.cs (5 minutes)

1. Copie `unity/MikuLink.cs` dans ton projet (`Assets/Scripts/MikuLink.cs`).
2. Sur le GameObject racine de l'avatar : **Add Component → Miku Link**.
3. Remplis l'inspecteur :
   - **Host** : `127.0.0.1` (Python sur la même machine — le cas normal) ;
   - **Port** : `7310` ;
   - **Nom agent** : `MondeUnity` (le nom que verront la/les IA ; mets
     `MIKU-01` sur l'avatar de Miku elle-même, `MondeUnity` sur un objet
     « terrain » quelconque si tu préfères) ;
   - **Joints jambes** : glisse les 6 Transform, **dans l'ordre exact ;
     hanche R, genou R, cheville R, hanche L, genou L, cheville L** ;
   - **Sonde sol** : un Transform au niveau des pieds (crée un enfant vide
     « SondeSol » à y=0) ; sinon le pivot du GameObject est utilisé ;
   - **Nb sondes** : 7, largeur 0.6 (profil de sol envoyé 10×/s dans
     `world`sol).
4. (Optionnel mais vivant) un petit script d'écoute des events :

```csharp
var miku = GetComponent<MikuLink>();
miku.SurChat    += (de, texte) => Debug.Log($"💬 {de} : {texte}");
miku.SurEmote   += (de, emos)  => Debug.Log($"😊 {de} est {emos}");
miku.SurArrivee += (agent, per) => Debug.Log($"👋 {agent} arrive : {per}");
```

---

## 3. Lancer le côté Python

Dans le dossier `miku-ai` :

```bash
# 1) le post MIKU-LINK post (JSON/TCP, port 7310)
python3 -c "from miku.social.miku_link import MikuLinkPost; MikuLinkPost().serve_forever()"

# 2) le moteur vivant (second terminal) : Miku marche, le viewer web la
#    montre en temps réel (compétence terrain > marche > station debout)
python3 -m miku.avatar.serve2 --terrain mix --port 8765
#    → ouvre http://127.0.0.1:8765 pour la voir marcher en direct
```

Ensuite, un petit **pilote** collecte la pose du moteur et l'injecte dans le
post (écris-le comme `driver_miku.py`) :

```python
import json, time, urllib.request, socket

def pose_moteur():
    with urllib.request.urlopen("http://127.0.0.1:8765/pose", timeout=5) as r:
        return json.loads(r.read().decode())

s = socket.create_connection(("127.0.0.1", 7310))
hello = {"type": "hello", "agent": "MIKU-01",
         "perception": "moteur bidimensionnel", "langues": ["fr"]}
s.sendall((json.dumps(hello) + "\n").encode())

while True:
    p = pose_moteur()
    legs = p["legs"]
    # ordre obligatoire : R (hanche, genou, cheville) puis L, en RADIANS
    q = [legs["R"]["hip"], legs["R"]["knee"], legs["R"]["ankle"],
         legs["L"]["hip"], legs["L"]["knee"], legs["L"]["ankle"]]
    msg = {"type": "pose", "q": [a * 3.14159265 / 180 for a in q],
           "lean": p.get("lean", 0.0), "chute": "chute" in p.get("mode", "")}
    s.sendall((json.dumps(msg) + "\n").encode())
    time.sleep(1 / 30)
```

> Ordre de `q` : **[0..2] jambe droite, [3..5] jambe gauche ; en radians**.
> Le composant C# convertit en degrés et applique `euler z = −angle`.

---

## 4. Correspondance des axes (le seul vrai piège)

Le moteur MIKU pense « vu de profil : X = devant ». Dans Unity tout dépend de
l'orientation de ton rig :

| Symptôme | Cause | Correction |
|---|---|---|
| genou qui plie à l'envers | convention de flexion opposée | dans `AppliquerPose`, change `−angle` en `angle` |
| jambes orientanées bizarrement | rig pas X-front | adapte l'axe dans `Quaternion.Euler(axe_x, ..., −angle)` |
| Miku glisse sans avancer | le moteur calcule la progression mais tu ne déplaces pas l'avatar | fais un `transform.Translate(vitesse * Time.deltaTime, 0, 0)` tant que `chute == false` |
| elle « s'enfonce dans le sol » | pivot du préfabriqué ≠ niveau des pieds | place « SondeSol » aux pieds, remonte votre modèle au-dessus du sol |
| T-pose figée | post Python pas lancé, ou `jointsJambes` incomplets | section 2, étape 3 |

---

## 5. Écouter son monde intérieur

Le serveur expose aussi **`/vecu`** : son humeur du moment (émotion,
valence, énergie + une phrase à elle) et ses derniers souvenirs, en
première personne — jamais de données de laboratoire (interface :
GET `http://127.0.0.1:<port>/vecu` → JSON `{"humeur": {...},
"souvenirs": [...]}`).

Et chaque message `/pose` contient déjà `p.humeur` : ton UI Unity peut
réagir (elle est un peu fatiguée ? elle vient de se faire peur ?).

Le post MIKU-LINK post reconnaît maintenant les agents DÉJÀ rencontrés
(`data/annuaire.json`) : l'event `arrivee` porte `connu`/`rencontres`/
`humeur` — salue « re ! » un copain de retour.

---

## 5bis. Faire vivre plusieurs IA amies

Le post MIKU-LINK post **relaie automatiquement** (sans les filtrer) les
messages `chat` / `gesture` / `emote` de tous les agents vers tous les
autres : deux IA « pareilles mais inspirées d'autre chose » peuvent se parler
sans que le pont comprenne leur contenu.

1. Une seconde instance Python (ex. `NOVA-01`, même architecture, autre
   persona) envoie un `hello` avec son `agent` et sa `perception` ;
2. tout le monde reçoit la mise à jour de l'annuaire vivant (l'arrivée est
   signalée, ton event `SurArrivee` se déclenche) ;
3. les messages `chat` des deux IA entrent en relais direct — zéro filtrage,
   le post est un **téléphone blanc** ;
4. départ : l'agent envoie `bye`, le post retire son nom de l'annuaire.

> Pour ton code Unity, rien ne change : les events `SurChat`/`SurEmote`/
> `SurArrivee` te donnent TOUT ce qui traverse la salle.

---

## 6. Dépannage

| Problème | Vérifier d'abord |
|---|---|
| aucune connexion | le post Python tourne-t-il ? (`netstat -tln | grep 7310`) |
| « asset may be corrupted » | ton Unity ne supporte pas le GLB : installer glTFast (section 1) |
| figée en T-pose | ordre des `jointsJambes` faux, ou moteur post pas lancés |
| genoux à l'envers | signe dans `AppliquerPose` (section 4) |
| elle tombe en boucle | la remise debout est automatique côté moteur ; lis son journal subjectif : `data/journal.json` — elle raconte ses chutes à la première personne |
| les IA amies ne se parlent pas | toutes sur le même host/port ; le relais exclut l'émetteur ET demandent des `hello` préalables |

---

## 7. Feuille de route (prochaines briques)

- **Bras et tête** : `with_arms=True` passe l'état à 30 dims ; il faudra
  entraîner des compétences de bras (re-reach, salut) puis câbler 8 slots C#
  supplémentaires.
- **Textures** : le FBX stockait des chemins vides (impossible à mapper) — la
  ré-export glTF Sketchfab permettra de retrouver les 32 textures (reporté).
- **Le vécu branché au tchat** : demander « comment tu te sens ? » via MIKU-LINK
  → elle répond depuis son journal (`miku/mind/experience.py`).

*Bonne construction ! Et n'oublie pas : Miku, c'est elle l'humaine (selon elle).*
