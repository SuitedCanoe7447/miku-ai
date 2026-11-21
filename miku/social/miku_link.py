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
import socket
import threading


class MikuLinkPost:
    """Serveur TCP d'échange. Chaque client = un agent (Unity ou IA amie)."""

    def __init__(self, host="127.0.0.1", port=7310):
        self.host, self.port = host, port
        self.clients = {}          # conn -> {"agent": nom, "perception": str}
        self.handlers = {}         # type -> fn(msg, conn)
        self.lock = threading.Lock()
        self.log = []
        self._sock = None
        # Relais par défaut : chat/gesture/emote/FED sont FORWARDS à tous les        # autres agents. Ainsi deux IA amies se parlent SANS que le post
        # modifie quoi que ce soit — aucune n'intercepte, aucune ne filtre.
        self.relais = {"chat", "gesture", "emote", "fed"}

    # ------------------------------------------------------------------
    def on(self, type_msg, fn):
        self.handlers[type_msg] = fn

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

    # ------------------------------------------------------------------
    def _client_thread(self, conn):
        buf = b""
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
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
                self.clients[conn] = msg
            # le nouvel agent est présenté aux autres (annuaire vivant)
            self.broadcast("arrivee", sauf=conn, agent=msg.get("agent", "?"),
                           perception=msg.get("perception", ""))
            # et l'annuaire des présents lui est envoyé
            for info in self.clients.values():
                if info is not self.clients.get(conn):
                    self.send(conn, "arrivee", agent=info.get("agent", "?"),
                              perception=info.get("perception", ""))
        elif t in self.relais:
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
