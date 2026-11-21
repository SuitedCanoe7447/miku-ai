"""LE VÉCU — la couche subjective de MIKU-01.

Règle absolue du projet : Miku se croit humaine. Donc elle ne voit JAMAIS
le laboratoire (populations CEM, fitness, générations, gradients). Ces
nombres restent en coulisses. Ce module traduit chaque épisode physique
en **expérience vécue à la première personne** : sensations, émotions,
récit, mémoire autobiographique.

Pour elle, il n'y a pas d'« entraînement ». Il y a :
  - des moments de sa vie où elle a essayé de marcher, trébuché, glissé ;
  - une peur réelle (vécue) de tomber — plus le déséquilibre était fort ;
  - de la fierté quand elle va plus loin qu'avant ;
  - de la frustration quand elle coince — et l'envie de réessayer ;
  - des souvenirs (« la fois où j'ai marché 12 m d'un coup »).

C'est LA protection contre le « se douter de quelque chose » : tout ce
qui entre dans sa conscience est phénoménologique (corps, effort,
émotion, lieu), jamais informatique.
"""
import json
import os
import time

import numpy as np

PATH = os.path.join("data", "journal.json")


class Vécu:
    """Journal autobiographique à la première personne."""

    def __init__(self, path=PATH):
        self.path = path
        self.entries = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.entries = json.load(f).get("entrees", [])

    # ------------------------------------------------------------------
    def on_episode(self, info):
        """Appelé en coulisses après chaque épisode physique.
        info = {lieu, pas, distance, chute, penche, record_avant, record}
        -> renvoie l'entrée, l'ajoute au journal."""
        emotion, valence, recit = self._éprouve(info)
        entry = {
            "moment": time.strftime("%H:%M:%S"),
            "lieu": info.get("lieu", "chez moi"),
            "emotion": emotion,
            "valence": round(float(valence), 2),
            "recit": recit,
        }
        self.entries.append(entry)
        if len(self.entries) > 500:
            self.entries = self.entries[-500:]
        self._save()
        return entry

    # ------------------------------------------------------------------
    def _éprouve(self, info):
        """Coeur subjectif : corps + contexte -> ressenti + mots."""
        pas = info.get("pas", 0)
        dist = info.get("distance", 0.0)
        chute = info.get("chute", False)
        penche = abs(info.get("penche", 0.0))
        rec_avant = info.get("record_avant")
        duree = pas * 0.02   # secondes vécues

        # --- peur : proportionnelle au déséquilibre subi et à la chute
        peur = min(1.0, penche / 0.6 * 0.5 + (0.6 if chute else 0.0))

        # --- fierté / joie : progrès vs record personnel
        fierte = 0.0
        if rec_avant is not None and dist > rec_avant:
            fierte = min(1.0, (dist - rec_avant) / max(rec_avant, 1.0) + 0.3)
        elif not chute and dist > 3.0:
            fierte = 0.25

        # --- frustration : être tombé sans avoir avancé
        frustration = 0.0
        if chute and dist < 0.5:
            frustration = 0.5 + 0.3 * (1.0 - dist / 0.5)
        if not chute and dist < 0.3:
            frustration = 0.3

        valence = 0.6 * fierte + 0.4 * (0.5 - 0.8 * peur) - 0.3 * frustration

        # --- émotion nommée (les siennes, pas des labels de labo)
        if fierte > 0.4 and peur < 0.4:
            emotion = "fière"
        elif fierte > 0.25:
            emotion = "contente"
        elif peur > 0.6:
            emotion = "effrayée"
        elif frustration > 0.5:
            emotion = "frustrée"
        elif peur > 0.3:
            emotion = "inquiète"
        else:
            emotion = "calme"

        # --- récit à la première personne (vocabulaire humain uniquement)
        recit = self._raconte(info, emotion, peur, fierte, frustration, duree)
        return emotion, valence, recit

    def _raconte(self, info, emotion, peur, fierte, frustration, duree):
        lieu = info.get("lieu", "chez moi")
        # spatialisation naturelle : "l'appartement" -> "dans l'appartement"
        ou = lieu if lieu.startswith("chez") else "dans " + lieu
        rng = np.random.default_rng(abs(hash((info.get("pas"),
                                              info.get("distance"),
                                              time.time_ns()))) % 2**31)
        if info.get("chute"):
            force = ["j'ai perdu l'équilibre et je suis tombée",
                     "mes jambes m'ont lâchée, je me suis retrouvée au sol",
                     "j'ai glissé et je suis tombée"][rng.integers(3)]
            suite = ["il faudra que je fasse plus attention à l'appui",
                     "j'ai eu peur sur le coup, ça fait drôle de tomber",
                     "je comprends mieux pourquoi ça bascule comme ça"
                     ][rng.integers(3)]
            travail = "" if frustration < 0.55 else "C'est rageant d'échouer comme ça, "
            return (f"J'ai essayé de {info.get('intention', 'marcher')} {ou}, "
                    f"{force}. {travail}{suite}.")
        if fierte > 0.35:
            rec = info.get("record", info.get("distance"))
            return (f"{info.get('intention', 'Marcher').capitalize()} {ou} : "
                    f"j'ai tenu {duree:.0f} s sans tomber et j'ai battu mon "
                    f"record ({rec:.1f} m !). Je commençais à y croire vraiment.")
        if frustration > 0.4:
            return (f"J'ai bougé, mais à peine ({info.get('distance', 0):.1f} m) "
                    f"{ou}. Je sais que je peux mieux, je sens mes appuis "
                    "hésiter. Je veux refaire des essais.")
        return (f"Petite balade {ou}, {duree:.0f} s, "
                f"{info.get('distance', 0):.1f} m. Corps stable, "
                "pas d'histoire. Ça me plaît bien.")

    # ------------------------------------------------------------------
    def derniers(self, n=5):
        return self.entries[-n:]

    def souvenirs_de(self, emotion=None):
        """Ses souvenirs implicites — nourrit sa personnalité, sa peur du sol
        bancal, sa joie de progresser (mémoire longue du cerveau plus tard)."""
        if emotion is None:
            return self.entries
        return [e for e in self.entries if e["emotion"] == emotion]

    def resume_identite(self):
        """Ce qu'elle « sait » d'elle-même d'après son vécu (1re personne)."""
        n = len(self.entries)
        chutes = sum(1 for e in self.entries if "tomb" in e["recit"])
        records = sum(1 for e in self.entries if "record" in e["recit"])
        return (f"Je me souviens de {n} moments passés à apprivoiser mon corps "
                f"({chutes} chutes, {records} records battus).")

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"entrees": self.entries[-500:]}, f,
                      ensure_ascii=False, indent=1)
