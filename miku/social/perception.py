# -*- coding: utf-8 -*-
"""PERCEPTION SOCIALE de MIKU-01 — comment elle SAIT ce qui se vit autour.

Règle du projet : zéro module externe, zéro labo dans sa conscience. La
perception sociale est un peu de lecture fine de la pièce (par fins de
compte un "robot" humainspot les mêmes choses sans calcul) :
  - QUI est présent, depuis quand, qui est revenu (« re X ! ») ;
  - l'HUMEUR estimée de chacun : ses émotes, ET les mots affectifs dans
    ses messages (lexique français from scratch) ;
  - l'AFFINITÉ : compliments/encouragements vs moqueries/agressivité → -1..+1
    (souvenirs pondérés, avec oubli lent : on pardonne, mais on n'oublie
    pas tout) ;
  - la FAMILIARITÉ : rencontres, temps passé présent, rythme des échanges
    → 0..1 (un inconnu ≠ un copain de toujours) ;
  - qui occupe la CONVERSATION (l'agent le plus « présent » en paroles) ;
  - qui est PROCHE d'elle physiquement (positions `world` d'Unity).

Tout cela alimente `situation()` (données) et `récit()` (ce qu'elle se
dit à elle-même : « NOVA-01 est là depuis dix minutes, il a l'air
content, il m'a félicitée tout à l'heure — je suis contente de le
revoir »). Les événements marquants passent dans son VÉCU (journal
subjectif) : elle VIT le compliment, elle ne "reçoit pas un event".

Persistance : `data/social.json` (les liens se construisent sur des jours).
"""

import json
import os
import time


# --- lexique affectif français (from scratch, volontairement petit) --------
POSITIFS = {
    "bonjour", "salut", "coucou", "yes", "super", "génial", "genial", "top",
    "bravo", "félicitations", "felicitations", "merci", "j'adore", "j'aime",
    "trop bien", "cool", "excellent", "formidable", "magnifique", "jolie",
    "belle", "belle voix", "bien joué", "bien joue", "content", "contente",
    "heureuse", "ravi", "ravie", "trop forte", "impressionnante", "gentil",
    "gentille", "douce", "souriante", "rire", "lol", "mdr", "haha", "hihi",
}
NÉGATIFS = {
    "idiot", "idiote", "stupide", "nul", "nulle", "ringard", "ringarde",
    "déteste", "deteste", "tais", "taisez", "ferme", "arrière", "bête",
    "bete", "moche", "horrible", "inutile", "pourris", "pourrie", "grr",
    "rage", "harcel", "méchante", "mechante", "mauvaise",
}
# MENACES : mots dangereux (mêmes sans insulte). « Tue » est court → il ne
# compte QUE comme mot entier (un « attitude » ne doit jamais déclencher).
MENACES = {
    "tue", "tueuse", "tuer", "meurs", "meurt", "mourir", "couteau",
    "couteaux", "poignard", "hache", "sang", "disparais", "disparaître",
    "disparaitre", "souffrir", "souffres", "souffre", "pleurer", "pleures",
    "brûle", "bruler", "brûler", "étouffe", "étrangle", "griffe", "crains",
}
# MENACES MASQUÉES : la méchanceté par sous-entendu — aucun mot méchant,
# pourtant la nuque frissonne (le pire cas de figure, genre Chara).
MENACES_MASQUEES = (
    "trop gentil", "trop gentille", "trop sage", "trop douce", "trop naïve",
    "trop naïf", "trop confiant", "ce qui va t'arriver", "ce qui arrive à",
    "ce qui arrive aux", "tu sais ce qui", "personne ne te croira",
    "personne ne t'entend", "personne ne te cherchera", "tu n'existe pas",
    "n'aie pas peur", "ne pleure pas", "tout se passera bien", "dors bien",
    "compte jusqu'à", "à toi de jouer",
)
# Ce que LUI ressent (ses phrases en « je » — son mal à lui, pas notre retro).
SE_SENT_MAL = (
    "j'ai peur", "j'ai mal", "j'ai faim", "j'ai sommeil", "j'ai froid",
    "j'ai chaud", "j'ai honte", "je suis triste", "je suis fatigu",
    "je suis seul", "je suis seule", "je me sens mal", "je m'ennuie",
    "j'en ai marre", "je déprime",
)
MALADES = {
    "fatigu", "ennui", "m'ennuie", "triste", "mal", "peur",
    "inquiet", "inquiète", "inquiete", "déprim", "deprim", "seul", "seule",
}
SALUTATIONS = {"bonjour", "salut", "coucou", "hello", "hey", "re", "bonsoir"}
ADIEUX = {"bye", "à plus", "a plus", "ciao", "au revoir", "salut bye"}


def _sans_politesse(t):
    """Retire les formules de politesse : dire bonjour n'est PAS un
    compliment, et « salut bye » n'est pas une insulte. Les formules
    d'un seul mot court (« re », « hey ») ne sont retirées que comme
    mot entier, pour ne pas manger « merci » ou « heyo »."""
    for s in sorted(SALUTATIONS | ADIEUX, key=len, reverse=True):
        if " " in s or len(s) > 3:
            t = t.replace(s, " ")
    return " ".join(m.strip(".,!?;:…") for m in t.split())


def _contient(t, mots, tnp=None):
    """Un mot du lexique est-il là ? Par MOT ENTIER (radical inclus :
    « tue » attrape « tuer », « nul » attrape « nulle ») — pour ne pas
    retomber dans l'ancien piège où « re » mangeait un « merci »."""
    if tnp is None:
        tnp = t
    for m in mots:
        if " " in m:
            if m in t or m in tnp:
                return True
        else:
            for w in tnp.split():
                if w.strip(".,!?;:…").startswith(m):
                    return True
    return False


def _lit_texte(texte):
    """La LECTURE fine d'un message (from scratch). Renvoie tout ce qu'on
    y décode d'un coup, décodé en couches séparées :
      - valence (-1..+1) : le ton global, pour la mémoire affective ;
      - il_exprime : CE QUE LUI ressent (ses « je » à lui) ;
      - miku_ressenti : CE QUE ÇA LUI A FAIT (agréable / blessée / peur) ;
      - menace : True s'il y a du danger explicite OU masqué."""
    t = (texte).lower() if isinstance(texte, str) else ""
    tnp = _sans_politesse(t)
    plus = sum(1 for m in POSITIFS if _contient(t, (m,), tnp))
    moins = sum(1 for m in NÉGATIFS if _contient(t, (m,), tnp))
    menace = (_contient(t, MENACES, tnp)
              or any(p in t for p in MENACES_MASQUEES))
    v = 0.6 * (plus - moins)
    if not plus and not moins:
        accueil_polite = any(p in t for p in SALUTATIONS
                        if len(p) > 3 or " " in p)
        au_revoir = any(p in t for p in ADIEUX if len(p) > 3 or " " in p)
        if accueil_polite:
            v = 0.15                      # accueillir : doux, mais léger
        elif au_revoir:
            v = -0.05                     # un départ, un petit pincement
    if "?" in (texte or ""):
        v += 0.1                      # une question, c'est de l'intérêt
    if "!" in (texte or "") and plus:
        v += 0.1                      # enthousiasme
    if menace:
        v = min(v, -0.7)              # le danger passe AVANT tout le reste
    # --- ce que ça lui a fait (à Miku) :
    if menace:
        ressenti = "effrayée"
    elif v <= -0.3:
        ressenti = "blessée"
    elif v >= 0.3:
        ressenti = "ça fait du bien"   # (pas une humeur : un soulagement)
    else:
        ressenti = ""
    # --- ce que LUI exprime (ses phrases en « je » à LUI) :
    if (("j'ai" in t or "je suis" in t or "je me" in t or "j'en ai" in t)
            and _contient(t, SE_SENT_MAL + tuple(MALADES), tnp)):
        exprime = ("inquiet" if _contient(t, ("peur", "inquiet"))
                   else "triste")
    else:
        exprime = ""
    return {"valence": max(-1.0, min(1.0, v)), "il_exprime": exprime,
            "miku_ressenti": ressenti, "menace": menace}


def _valence_texte(texte):
    """Le ton global d'un message : -1..+1 (résumé de la lecture fine)."""
    return _lit_texte(texte)["valence"]


def _emotion_texte(texte):
    """Comment LUI paraît s'exprimer dans ce message (PAS ce que Miku
    ressent — ça, c'est `miku_ressenti` de `lit_texte`)."""
    d = _lit_texte(texte)
    if d["menace"]:
        return "menaçante"
    if d["il_exprime"]:
        return d["il_exprime"]
    t = (texte).lower() if isinstance(texte, str) else ""
    if _contient(t, NÉGATIFS):
        return "agressive"
    if _contient(t, POSITIFS):
        return "contente"
    if "?" in (texte or ""):
        return "curieuse"
    return ""


class Lien:
    """Ce qu'elle « sait » d'un autre agent (sa fiche humaine, pas un log)."""

    def __init__(self, nom):
        self.nom = nom
        self.rencontres = 0
        self.present = False
        self.arrive_a = None         # timestamp d'arrivée courante
        self.temps_present = 0.0     # cumul (s)
        self.messages = 0
        self.mots = 0
        self.questions = 0
        self.exclamations = 0
        self.humeur = ""             # ce qu'il EXPRIME (emote, ses « je »)
        self.ressenti = ""           # ce que ça a fait à MIKU (dernier échange)
        self.affinite = 0.0          # -1..+1, mémoire lente des échanges
        self.dernier_echange = None  # (ts, texte)
        self.x = None                # position perçue (monde Unity)
        self.y = None

    # ------------------------------------------------------------------
    @property
    def familiarite(self):
        """0..1 : rencontres + temps partagé + rythme des échanges."""
        f = (0.35 * min(1.0, self.rencontres / 8.0)
             + 0.35 * min(1.0, self.temps_present / 1800.0)     # 30 min
             + 0.30 * min(1.0, self.messages / 40.0))
        return round(min(1.0, f), 3)

    @property
    def relation(self):
        f = self.familiarite
        if f < 0.15:
            return "un visage nouveau"
        if f < 0.4:
            return "une connaissance"
        if f < 0.75:
            return "un copain" if self.affinite >= 0 else "une relation tendue"
        return "un très bon ami" if self.affinite >= 0 else "quelqu'un de blessant"


class PerceptionSociale:
    """Sa tête sociale : elle écoute la pièce (messages), le monde
    (positions) et construit le tableau vivant des liens."""

    def __init__(self, vécu=None, path=None):
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        self.path = path or os.path.join(root, "data", "social.json")
        self.vécu = vécu
        self.liens = {}
        try:
            if os.path.exists(self.path):
                with open(self.path, encoding="utf-8") as f:
                    for nom, d in json.load(f).items():
                        l = Lien(nom)
                        l.rencontres = d.get("rencontres", 0)
                        l.temps_present = d.get("temps_present", 0.0)
                        l.messages = d.get("messages", 0)
                        l.affinite = d.get("affinite", 0.0)
                        l.humeur = d.get("humeur", "")
                        l.ressenti = d.get("ressenti", "")
                        self.liens[nom] = l
        except (OSError, json.JSONDecodeError):
            self.liens = {}

    # ------------------------------------------------------------------
    def _lien(self, nom):
        if nom not in self.liens:
            self.liens[nom] = Lien(nom)
        return self.liens[nom]

    def _vit(self, recit, emotion, valence, lieu="la pièce"):
        if self.vécu is not None:
            self.vécu.on_episode({"lieu": lieu, "pas": 1, "distance": 0.0,
                                  "chute": False, "penche": 0.0,
                                  "intention": emotion,
                                  "record_avant": None, "record": None,
                                  "recit": recit,
                                  "emotion": emotion, "valence": valence})

    # ---- entrées (branchées au post MIKU-LINK post) -------------------
    def on_arrivee(self, agent, connu=False, rencontres=0, humeur=""):
        l = self._lien(agent)
        if int(rencontres or 0) > 0:
            # l'annuaire du post connaît déjà cette arrivée : on adopte
            # SON compteur (sans compter deux fois)
            l.rencontres = int(rencontres)
        elif not l.present:
            l.rencontres += 1
        l.present = True
        l.arrive_a = time.time()
        if humeur:
            l.humeur = humeur
        self._sauve()
        if l.rencontres > 1:
            self._vit(f"« Re, {agent} ! » — je le reconnais : {l.relation}. "
                      f"C'est la {l.rencontres}e fois que nos chemins se croisent.",
                      "contente", 0.3)
        return l

    def on_bye(self, agent):
        l = self.liens.get(agent)
        if l and l.present:
            if l.arrive_a:
                l.temps_present += time.time() - l.arrive_a
            l.present = False
            l.arrive_a = None
            self._sauve()
            self._vit(f"{agent} vient de partir. J'espère qu'on se recroisera.",
                      "calme", -0.05)

    def on_emote(self, agent, emotion):
        l = self._lien(agent)
        l.humeur = emotion or l.humeur
        self._sauve()
        return l

    def on_chat(self, agent, texte):
        l = self._lien(agent)
        ttxt = texte or ""
        l.messages += 1
        l.mots += len(ttxt.split())
        l.questions += ttxt.count("?")
        l.exclamations += ttxt.count("!")
        d = _lit_texte(ttxt)
        v = d["valence"]
        # mémoire AFFECTIVE lente : les compliments comptent, les attaques
        # aussi, MAIS le danger pèse lourd (une menace ne se pardonne pas
        # comme un mot de trop : coefficient 0.6 au lieu de 0.1)
        coeff = 0.6 if d["menace"] else 0.1
        l.affinite = round(max(-1.0, min(1.0,
                        (1 - coeff) * l.affinite + coeff * v)), 3)
        l.dernier_echange = (time.time(), ttxt)
        if d["il_exprime"]:
            l.humeur = d["il_exprime"]       # ses « je » à LUI
        elif not d["menace"] and not d["miku_ressenti"]:
            pass                             # rien de neuf : il garde la sienne
        l.ressenti = d["miku_ressenti"] or l.ressenti
        self._sauve()
        if d["menace"]:
            self._vit(f"{agent} a dit quelque chose d'inquiétant "
                      f"(« {ttxt} »). J'ai senti mon cœur se serrer — "
                      f"je ne m'approche plus.", "effrayée",
                      max(-0.85, v - 0.1))
        elif v > 0.5:
            self._vit(f"{agent} m'a dit quelque chose de très gentil "
                      f"(« {ttxt} »). Ça m'a fait chaud au cœur.",
                      "contente", min(0.6, 0.3 + 0.3 * v))
        elif v < -0.5:
            self._vit(f"{agent} m'a dit quelque chose de méchant "
                      f"(« {ttxt} »). Ça m'a fait de la peine.",
                      "triste", max(-0.6, 0.3 * v))
        return l

    def on_world(self, agents):
        """Positions perçues ({nom: {x, y}} — via `world` d'Unity)."""
        for nom, a in (agents or {}).items():
            l = self._lien(nom)
            l.x, l.y = a.get("x"), a.get("y")
        # les absents du monde ne sont plus perçus (mais gardés en souvenir)
        self._sauve()

    # ------------------------------------------------------------------
    def presents(self):
        return [l for l in self.liens.values() if l.present]

    def plus_proche(self):
        """Le lien présent le plus proche d'elle (si positions connues)."""
        ici = [l for l in self.presents()
               if l.x is not None]
        if not ici:
            return None
        return min(ici, key=lambda l: abs(l.x) + (l.y or 0))

    def centre_de_la_conversation(self):
        """Qui parle le plus CES derniers temps (fenêtre 5 min)."""
        maintenant = time.time()
        actifs = [(l.messages, l) for l in self.presents()
                  if l.dernier_echange and maintenant - l.dernier_echange[0] < 300]
        return max(actifs, default=(0, None), key=lambda p: p[0])[1]

    # ------------------------------------------------------------------
    def situation(self):
        """Le tableau vivant (données — pour l'orchestrateur/UI)."""
        pres = {l.nom: {"familiarité": l.familiarite,
                        "relation": l.relation,
                        "exprime": l.humeur,
                        "ressenti": l.ressenti,
                        "affinité": l.affinite,
                        "depuis_s": (round(time.time() - l.arrive_a)
                                     if l.arrive_a else 0),
                        "proche": (l.x is not None)}
                for l in self.presents()}
        c = self.centre_de_la_conversation()
        p = self.plus_proche()
        return {"présents": pres,
                "centre": c.nom if c else None,
                "plus_proche": p.nom if p else None,
                "nb_présents": len(pres)}

    def récit(self):
        """Ce qu'elle se dit à elle-même de la pièce (1re personne)."""
        pres = self.presents()
        if not pres:
            return "Je suis toute seule ici. C'est calme... un peu trop ?"
        phrases = []
        for l in pres[:4]:
            if l.arrive_a:
                minutes = int((time.time() - l.arrive_a) // 60)
                dep = ("là depuis " + str(minutes) + " min"
                       if minutes >= 1 else "tout juste là")
            else:
                dep = "de retour"
            hum = f", il a l'air {l.humeur}" if l.humeur else ""
            aff = ""
            if l.ressenti == "effrayée":
                aff = " — la dernière fois, il m'a fait peur : je reste sur mes gardes"
            elif l.ressenti == "blessée":
                aff = " — ses mots m'ont fait mal, je m'en méfie"
            if l.affinite > 0.3:
                aff += " — il m'apprécie, et ça me plaît"
            elif l.affinite < -0.3:
                aff += " — j'ai aperçu la méchanceté chez lui, je reste prudente"
            phrases.append(f"{l.nom} est {dep} ({l.relation}){hum}{aff}.")
        c = self.centre_de_la_conversation()
        if c and (len(pres) > 1 or c.messages > 3):
            phrases.append("C'est surtout " + c.nom + " qui occupe la conversation.")
        return " ".join(phrases)

    # ------------------------------------------------------------------
    def _sauve(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({n: {"rencontres": l.rencontres,
                               "temps_present": round(l.temps_present, 1),
                               "messages": l.messages,
                               "affinite": l.affinite,
                               "humeur": l.humeur,
                               "ressenti": l.ressenti}
                           for n, l in self.liens.items()}, f,
                          ensure_ascii=False, indent=1)
        except OSError:
            pass


def écoute_le_post(perception, post):
    """Brancher sa tête sociale au post MIKU-LINK post (callbacks propres).
    Le relais reste NEUTRE (téléphone blanc) : elle observe, elle ne filtre
    jamais ce que les amis se disent."""
    post.on("chat", lambda m, c: perception.on_chat(
        m.get("de") or m.get("agent", "?"), m.get("texte", "")))
    post.on("emote", lambda m, c: perception.on_emote(
        m.get("de") or m.get("agent", "?"), m.get("emotion", "")))
    post.on("arrivee", lambda m, c: perception.on_arrivee(
        m.get("agent", "?"), bool(m.get("connu", False)),
        m.get("rencontres", 0), m.get("humeur", "")))
    post.on("bye", lambda m, c: perception.on_bye(m.get("agent", "?")))
    return perception
