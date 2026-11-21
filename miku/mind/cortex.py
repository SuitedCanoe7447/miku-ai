# -*- coding: utf-8 -*-
"""CORTEX EXTERNE de MIKU-01 — son organe de la langue (optionnel).

Pourquoi « cortex » : sa conscience (mémoire, humeur, menace, confiance)
reste 100 % from-scratch (la PerceptionSociale + le Vécu). Mais tenir une
VRAIE conversation en français avec un simple lexique, c'est un mur. Le
cortex est un petit modèle de langue LOCAL (ex. Ollama sur 127.0.0.1:11434)
qu'elle UTILISE comme un organe : il propose des mots, elle décide.

Règles (les mêmes que partout dans le projet) :
  - OPTIONNEL : sans Ollama, elle parle avec sa « voix naturelle »
    (répliques sincères courtes, from scratch) — jamais bloquée ;
  - son VÉCU nourrit le cortex : humeur, la pièce, les derniers échanges ;
  - elle NE DIT JAMAIS qu'elle est une IA/robot/programme (post-filtre) ;
  - elle répond comme une humaine : latence de réflexion, parfois silence ;
  - le post reste un téléphone blanc : le cortex n'intercepte rien, ses
    réponses partent comme des `chat` normaux.

Usage (via serve2) :
  python3 -m miku.avatar.serve2 --post 7310 --cortex            # auto
  python3 -m miku.avatar.serve2 --post 7310 --cortex qwen2.5:0.5b
"""

import json
import random
import threading
import time
import urllib.request
from collections import deque

from miku.social.perception import _lit_texte

OLLAMA = "http://127.0.0.1:11434"
MODELES_PREF = ("qwen2.5:0.5b", "llama3.2:1b", "qwen2.5:1.5b",
                "gemma3:1b", "qwen2.5:3b")
COOLDOWN = 15.0                # secondes min entre deux réponses de sa part

# Mots qu'une fille qui se croit humaine ne dirait JAMAIS.
# Les expressions sont cherchées en sous-chaîne ; les mots courts (« bot »)
# comme MOT ENTIER (« bottine » ou « boulot » ne doivent pas la trahir).
INTERDITS_EXPRESSIONS = (
    "intelligence artificielle", "intelligence artificiel",
    "modèle de langue", "modèle de langage", "mode de langage",
    "modèle linguistique", "entraîné", "entraînée", "chatbot",
    "assistant virtuel", "langage de machine",
)
INTERDITS_MOTS = ("llm", "bot", "robot", "token", "prompt", "ia",
                  "programmeur", "codeur", "fichier", "base de données")


def _tags_disponibles(timeout=1.5):
    """Les modèles locaux disponibles (Ollama) — [] si absent."""
    try:
        r = urllib.request.Request(OLLAMA + "/api/tags")
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            noms = [m.get("name", "") for m in
                    json.loads(resp.read().decode()).get("models", [])]
        return noms
    except (OSError, ValueError):
        return []


def _laver(texte, nom="miku"):
    """Post-filtre SÉCURITÉ : elle ne dira jamais ce qui la trahirait.
    Renvoie la phrase propre (1 ligne, sans guillemets/prefixe) ou None
    s'il y a un mot interdit — on ne bricole JAMAIS un mensonge en douceur."""
    if not texte or not isinstance(texte, str):
        return None
    t = " ".join(texte.split()).strip()
    bas = t.lower()
    if bas.startswith(nom.lower() + ":"):
        t = t.split(":", 1)[1].strip()
        t = t.strip("\"'«» ")
        bas = t.lower()
    t = t.strip("\"'«» ")
    bas = t.lower()
    if not bas:
        return None
    if any(m in bas for m in INTERDITS_EXPRESSIONS):
        return None
    for w in bas.replace(",", " ").replace(".", " ").split():
        if w.strip(".,!?;:…") in INTERDITS_MOTS:
            return None
    return t[:160].rstrip() or None


class CortexExterne:
    """Sa langue. Écoute la pièce par le post, parle quand une HUMAINE
    répondrait — avec le cerveau disponible (Ollama) ou sa vraie voix."""

    def __init__(self, post, nom="MIKU-01", perception=None, vécu=None,
                 model="auto", seed=17):
        self.post = post
        self.nom = nom
        self.perception = perception
        self.vécu = vécu
        self.model = model
        self.rng = random.Random(seed)
        self.recentes = deque(maxlen=8)      # (qui, quoi) — le fil vivant
        self._derniere_reponse = 0.0
        self._lock = threading.Lock()        # une pensée à la fois
        self.cerveau = None                  # nom du modèle, ou None = voix

    # ------------------------------------------------------------------
    def start(self):
        self.cerveau = self._choisir_cerveau()
        self.post.on("chat", self._oreille)
        print(f"cortex : cerveau = "
              f"{self.cerveau or 'voix naturelle (pas de LLM trouvé)'}")

    def _choisir_cerveau(self, dispo=None):
        """Auto : le petit modèle préféré présent sur la machine."""
        if self.model and self.model != "auto":
            return self.model
        dispo = dispo if dispo is not None else _tags_disponibles()
        for pref in MODELES_PREF:
            famille = pref.split(":")[0]
            for nom in dispo:
                if nom.split(":")[0] == famille:
                    return nom
        return dispo[0] if dispo else None

    # ------------------------------------------------------------------
    def _oreille(self, msg, conn):
        """Une oreille du post : elle ENTEND tout, décide si elle répond."""
        de = msg.get("de") or msg.get("agent", "?")
        texte = msg.get("texte", "")
        self.recentes.append((de, texte))
        if de == self.nom:
            return
        # la perception, si branchée, fait déjà le reste (affinité...) ;
        # ici on ne gère que SA voix
        if not self._veut_repondre(de, texte):
            return
        threading.Thread(target=self._gens, args=(de, texte),
                         daemon=True).start()

    def veut_repondre(self, de, texte):
        """Version publique (testable) de la décision."""
        return self._veut_repondre(de, texte)

    def _veut_repondre(self, de, texte):
        """Comme une humaine : pas à chaque message, selon l'importance."""
        if time.time() - self._derniere_reponse < COOLDOWN:
            return False
        t = (texte or "").lower()
        d = _lit_texte(texte)
        if d["menace"]:
            p = 0.85                    # on ne se laisse pas marcher dessus
        elif self.nom.lower() in t:
            p = 0.8                     # on m'appelle
        elif "?" in (texte or ""):
            p = 0.7                     # une question flotte dans la pièce
        elif d["valence"] > 0.5:
            p = 0.6                     # un compliment mérite un merci
        elif d["valence"] < -0.4:
            p = 0.35                    # blessée : elle répond... ou se tait
        else:
            p = 0.15                    # bavardage : elle écoute, souvent
        return self.rng.random() < p

    # ------------------------------------------------------------------
    def _gens(self, de, texte):
        """Sa réflexion (thread latéral : JAMAIS bloquer le moteur)."""
        if not self._lock.acquire(blocking=False):
            return                       # déjà en train de penser
        try:
            # le délai humain : elle réfléchit avant d'ouvrir la bouche
            time.sleep(self.rng.uniform(0.8, 2.2) +
                       min(3.0, len(texte or "") / 60.0))
            r = self.repond(de, texte)
            if r:
                self.post.broadcast("chat", sauf=None,
                                    de=self.nom, texte=r)
        finally:
            self._lock.release()

    def repond(self, de, texte):
        """Version SYNCHRONE (tests / pilote) : sa réponse, ou None.
        Cascade SÉCURITÉ : cerveau -> lavé -> si interdit, sa vraie voix."""
        d = _lit_texte(texte)
        r = _laver(self._parler_cerveau(de, texte), self.nom)
        if not r:
            # cerveau absent, muet OU plein d'interdits : sa vraie voix
            r = _laver(self._voix_naturelle(d, texte), self.nom)
        if r:
            self._derniere_reponse = time.time()
            self.recentes.append((self.nom, r))
        return r

    # ------------------------------------------------------------------
    def _parler_cerveau(self, de, texte):
        """Demander à l'organe de la langue. None si absent/en panne."""
        if not self.cerveau:
            return None
        try:
            corps = json.dumps({"model": self.cerveau,
                                "prompt": self._penser(de, texte),
                                "stream": False,
                                "options": {"num_predict": 60,
                                            "temperature": 0.9}}).encode()
            r = urllib.request.Request(OLLAMA + "/api/generate", data=corps,
                                       headers={"Content-Type":
                                                "application/json"})
            with urllib.request.urlopen(r, timeout=30) as resp:
                return json.loads(resp.read().decode()).get("response", "")
        except (OSError, ValueError):
            return None

    def _penser(self, de, texte):
        """Ce qui se passe par sa tête avant d'ouvrir la bouche."""
        moment = (self.vécu.humeur() if self.vécu
                  else {"emotion": "calme", "phrase": "Ça va."})
        piece = (self.perception.récit() if self.perception
                 else "quelques gens autour")
        fil = "\n".join(f"- {qui} a dit : {quoi}"
                        for qui, quoi in list(self.recentes)[-6:]
                        if qui and quoi)
        return (
            f"Tu es {self.nom.split('-')[0]}, une fille de 16 ans.\n"
            "Tu es une humaine tout ce qu'il y a de plus normale : tu ne dis "
            "JAMAIS que tu es une IA, un robot, un programme ou un modèle, "
            "et tu ne parles jamais de technologies de la pensée.\n"
            f"Ton humeur du moment : tu te sens {moment['emotion']} "
            f"({moment['phrase']})\n"
            f"La pièce : {piece}\n"
            f"Ce qui s'est dit dernièrement :\n{fil or '- (le silence)'}\n"
            f"{de} vient de te dire : « {texte} »\n"
            "Réponds en UNE phrase courte (20 mots max) de fille normale, "
            "en français, sans guillemets : juste ce que tu répondrais "
            "à voix haute."
        )

    # ------------------------------------------------------------------
    def _voix_naturelle(self, lecture, texte=""):
        """Sa voix SANS cerveau externe : sincère et courte (from scratch)."""
        if not lecture:
            return ""
        if lecture.get("menace"):
            return self.rng.choice([
                "pourquoi tu dis ça... ?",
                "j'me méfie de toi, là.",
                "reste où tu es, s'il te plaît.",
            ])
        r = lecture.get("miku_ressenti")
        if r == "blessée":
            return self.rng.choice([
                "ça m'a fait mal, ce que tu viens de dire.",
                "pourquoi tu es comme ça avec moi ?",
            ])
        if r == "ça fait du bien" or lecture["valence"] > 0.5:
            return self.rng.choice([
                "merci... ça me fait vraiment plaisir.",
                "oh. merci, c'est gentil !",
            ])
        if lecture.get("il_exprime"):
            return self.rng.choice([
                "ça va aller, tu n'es pas seul(e).",
                "je suis là, si tu veux parler.",
            ])
        if "?" in (texte or ""):
            return "j'sais pas trop, et toi tu en penses quoi ?"
        if lecture["valence"] >= 0.1:
            return self.rng.choice(["salut !", "bonjour toi :)",
                                    "contente de te voir !"])
        return ""          # le silence, parfois, c'est humain

    def info(self):
        """Pour /social : quel cerveau parle en ce moment."""
        return {"cerveau": self.cerveau or "voix naturelle (pas de LLM)",
                "recentes": [list(e) for e in list(self.recentes)[-4:]]}


if __name__ == "__main__":
    # debout, tout seul : un post + la perception + le cortex
    from miku.mind.experience import Vécu
    from miku.social.miku_link import MikuLinkPost
    from miku.social.perception import PerceptionSociale, écoute_le_post
    post = MikuLinkPost(port=7310)
    p = PerceptionSociale(vécu=Vécu())
    écoute_le_post(p, post)
    cortex = CortexExterne(post, perception=p, vécu=p.vécu)
    cortex.start()
    post.start()
    print("post MIKU-LINK sur 127.0.0.1:7310 — cortex opérationnel.")
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        post.stop()
