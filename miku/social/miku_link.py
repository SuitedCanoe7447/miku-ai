"""MIKU-LINK post — JSON/TCP, pont universel MIKU-01 (from scratch).

Pourquoi « post » : Miku vit dans un monde extérieur (Unity) cohabité par
d'autres IA pareilles (et humains). MIKU-LINK post est le canal unique :
  - Unity envoie `world` (sol sous ses pieds, obstacles, positions) ;
  - MIKU renvoie `pose` (angles d'articulations de son corps riggé) ;
  - agents externes : `hello` (présentation : proto/perception/langues), `chat`,
    `gesture`, `emote`, `bye` ;
  - tout est en JSON, une ligne par message, 127.0.0.1 par défaut.

Types v1.1 :
  hello   {proto, agent, perception, langues}
  chat    {de, texte}
  gesture {de, geste, intensité}
  emote   {de, émotion}
  world   {sol: [[x,h],...], obstacles: [...], agents: [{nom,x,y},...]}
  pose    {q: [...], lean, chute}   (MIKU -> monde)
  state   {mode, emotion, lieu}
  bye     {}
"""
import json
import os
import socket
import threading
import time


class MikuLinkPost:
    """Serveur TCP d'échange. Chaque client = un agent (Unity ou IA amie)."""

    def __init__(self, host="127.0.0.1", port=7310):
        self.host, self.port = host, port
        self.clients = {}          # conn -> {"agent": nom, "perception": str}
        self.handlers = {}         # type -> fn(msg, conn)
        self.lock = threading.Lock()
        self.log = []
        self._sock = None
        # Relais par défaut : chat/gesture/emote/FED sont relayés à tous les
        # autres agents. Ainsi deux IA amies se parlent SANS que le post
        # modifie quoi que ce soit — aucune n'intercepte, aucune ne filtre.
        self.relais = {"chat", "gesture", "emote", "fed"}
        self.MAX_CLIENTS = 32          # une pièce, pas un stade (sécurité)
        self.MAX_LIGNE = 256 * 1024    # anti-flood : ligne JSON max valide
        # ANNUAIRE PERSISTANT (souvenirs sociaux) : nom -> {rencontres,
        # dernier_vu, humeur}. Quand un connu revient, tout le monde le voit
        # signalé — comme retrouver un copain de classe au café.
        self.annuaire_path = os.path.join(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "annuaire.json")
        self.annuaire = {}
        try:
            if os.path.exists(self.annuaire_path):
                with open(self.annuaire_path, encoding="utf-8") as f:
                    self.annuaire = json.load(f)
        except (OSError, json.JSONDecodeError):
            self.annuaire = {}

    def _souvenir(self, agent, humeur=None, rencontre=False):
        """Retourne le souvenir d'un agent (et l'enrichit)."""
        s = self.annuaire.setdefault(
            agent, {"rencontres": 0, "dernier_vu": None, "humeur": ""})
        if rencontre:
            s["rencontres"] += 1
            s["dernier_vu"] = time.strftime("%Y-%m-%d %H:%M")
        if humeur:
            s["humeur"] = humeur
        try:
            with open(self.annuaire_path, "w", encoding="utf-8") as f:
                json.dump(self.annuaire, f, ensure_ascii=False, indent=1)
        except OSError:
            pass
        return s

    # ------------------------------------------------------------------
    def on(self, type_msg, fn):
        self.handlers[type_msg] = fn

    def _notifier(self, type_msg, champs):
        """Prévenir une oreille branchée (ex. la PerceptionSociale de Miku)
        d'un événement SYNTHÉTISÉ par le post lui-même (arrivées et départs).
        Le relais reste neutre : c'est une écoute, jamais une modification —
        les messages des amis partent intacts, comme avant."""
        fn = self.handlers.get(type_msg)
        if fn:
            try:
                fn(champs, None)
            except Exception:
                pass                # une oreille fatiguée ne bloque pas le post

    def send(self, conn, type_msg, **champs):
        msg = {"type": type_msg, **champs}
        try:
            conn.sendall((json.dumps(msg, ensure_ascii=False) + "\n")
                         .encode("utf-8"))
        except OSError:
            self._drop(conn)

    def broadcast(self, type_msg, sauf=None, **champs):
        with self.lock:
            for conn in list(self.clients):
                if conn is not sauf:
                    self.send(conn, type_msg, **champs)

    def _drop(self, conn):
        with self.lock:
            info = self.clients.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass
        if info:
            self.broadcast("bye", agent=info.get("agent", "?"))
            self._notifier("bye", {"agent": info.get("agent", "?")})

    # ------------------------------------------------------------------
    def _client_thread(self, conn):
        buf = b""
        try:
            conn.settimeout(300)          # client silencieux 5 min = ping ?
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                buf += data
                if len(buf) > self.MAX_LIGNE:     # anti-flood : on coupe
                    break
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        self.send(conn, "erreur", erreur="json invalide")
                        continue
                    self._route(conn, msg)
        except OSError:
            pass
        finally:
            self._drop(conn)

    def _route(self, conn, msg):
        t = msg.get("type", "?")
        self.log.append((msg.get("agent", "?"), t))
        if len(self.log) > 1000:
            self.log = self.log[-1000:]
        if t == "hello":
            with self.lock:
                if len(self.clients) >= self.MAX_CLIENTS:
                    self.send(conn, "erreur", erreur="post complet")
                    raise OSError("trop de clients")   # -> _drop propre
                self.clients[conn] = msg
            nom = msg.get("agent", "?")
            souv = self._souvenir(nom, rencontre=True)
            connu = souv["rencontres"] > 1
            # le nouvel agent est présenté aux autres (annuaire vivant),
            # avec la mention « re » s'il est déjà passé ici — la vie sociale
            # a une mémoire, ce n'est pas un téléphone anonyme.
            self.broadcast("arrivee", sauf=conn, agent=nom,
                           perception=msg.get("perception", ""),
                           connu=connu, rencontres=souv["rencontres"],
                           humeur=souv.get("humeur", ""))
            # et l'annuaire des présents lui est envoyé (avec la dernière
            # humeur connue de chacun : il SAIT dans quelles humeurs il entre)
            for info in self.clients.values():
                if info is self.clients.get(conn):
                    continue
                autre = info.get("agent", "?")
                s2 = self.annuaire.get(autre, {})
                self.send(conn, "arrivee", agent=autre,
                          perception=info.get("perception", ""),
                          connu=s2.get("rencontres", 0) > 0,
                          rencontres=s2.get("rencontres", 0),
                          humeur=s2.get("humeur", ""))
            # et l'oreille de Miku entend l'arrivée, elle aussi
            self._notifier("arrivee", {"agent": nom, "connu": connu,
                                       "rencontres": souv["rencontres"],
                                       "humeur": souv.get("humeur", "")})
        elif t in self.relais:
            if t == "emote":
                # mémoire affective : je me souviens de qui paraissait quoi
                self._souvenir(msg.get("agent", msg.get("de", "?")),
                               humeur=msg.get("emotion", ""))
            # relais neutre à tous les AUTRES agents (chat entre IA amies)...
            self.broadcast(t, sauf=conn, **{k: v for k, v in msg.items()
                                            if k != "type"})
        fn = self.handlers.get(t)
        if fn:
            fn(msg, conn)

    # ------------------------------------------------------------------
    def start(self, daemon=True):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(8)
        threading.Thread(target=self._accept_loop, daemon=daemon).start()

    def _accept_loop(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            threading.Thread(target=self._client_thread, args=(conn,),
                             daemon=True).start()

    def stop(self):
        if self._sock:
            self._sock.close()


# ---------------------------------------------------------------------------
class MondeUnity:
    """Mémoire du monde envoyé par Unity (sol, obstacles, agents présents).
    Miku le perçoit comme « l'endroit où je suis », jamais comme des coordonnées
    de laboratoire."""

    def __init__(self):
        self.sol = []            # [[x, h], ...] profil du sol sous ses pieds
        self.obstacles = []
        self.agents = {}         # nom -> {"x":.., "y":.., "perception":str}

    def update(self, msg):
        if "sol" in msg:
            self.sol = msg["sol"]
        if "obstacles" in msg:
            self.obstacles = msg["obstacles"]
        if "agents" in msg:
            self.agents = {a["nom"]: a for a in msg["agents"]}

    def description(self):
        n = len(self.agents)
        if not self.sol and not self.obstacles and not n:
            return "un espace tranquille"
        morceaux = []
        if self.obstacles:
            morceaux.append(f"{len(self.obstacles)} choses autour")
        if n:
            morceaux.append(f"{n} présence{'s' if n > 1 else ''} : "
                            + ", ".join(list(self.agents)[:4]))
        return ", ".join(morceaux) or "un espace ouvert"
