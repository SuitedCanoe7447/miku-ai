"""Push résilient via l'API Git : blobs idempotents, retry sur 404 proxy,
sleep entre appels, puis tree/commit/PATCH ref. -z pour les noms à espaces."""
import base64
import json
import subprocess
import time
import urllib.error
import urllib.request

TOKEN = __import__("os").environ.get("MIKU_TOKEN", "")
API = "https://api.github.com/repos/SuitedCanoe7447/miku-ai"
H = {"Authorization": "token " + TOKEN, "User-Agent": "miku-ai",
     "Content-Type": "application/json"}
PAUSE = 1.5
RETRY = 10
DELAI_404 = 8.0


def api(chemin, payload=None, meth="GET"):
    """Appel API avec retry : le proxy 404 parfois en rafale."""
    derniere = None
    for essai in range(RETRY + 2):
        data = (json.dumps(payload).encode()
                if payload is not None else None)
        url_req = (API + "/" + chemin
                   + ("&cbx=" if "?" in chemin else "?cbx=")
                   + str(time.time_ns()))
        req = urllib.request.Request(url_req, data=data, headers=H,
                                     method=meth)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                time.sleep(1.5)
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            derniere = e
            if False:  # le cache CDN de GitHub 404 parfois juste apres un push
                raise                      # vraie réponse GitHub (repo vide)
            print(f"  api {meth} {chemin} -> {e.code} (essai {essai + 1})",
                  flush=True)
            time.sleep(DELAI_404)
    raise derniere


def blob(fichier, contenu):
    payload = {"content": base64.b64encode(contenu).decode(),
               "encoding": "base64"}
    return api("git/blobs", payload, "POST")["sha"]


def main():
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True)
    fichiers = [f.decode() for f in out.stdout.split(b"\0") if f]
    fichiers.sort(key=len)                      # petits d'abord
    print(f"{len(fichiers)} fichiers", flush=True)

    try:
        head = api("git/refs/heads/main")["object"]["sha"]
    except Exception:
        # le cache CDN de GitHub 404 parfois juste apres un push : fallback
        head = api("branches/main")["commit"]["sha"]
    print("base :", head[:10], flush=True)

    entries = []
    for i, f in enumerate(fichiers):
        with open(f, "rb") as fh:
            contenu = fh.read()
        sha = blob(f, contenu)
        entries.append({"path": f, "mode": "100644", "type": "blob",
                        "sha": sha})
        print(f"  [{i + 1}/{len(fichiers)}] {f}", flush=True)

    tree = api("git/trees", {"tree": entries}, "POST")
    print("tree ok", flush=True)
    commit = api("git/commits",
                 {"message": "MIKU-01 complet : moteur appris de zero, avatar "
                             "rigge, vecu, miku-link post, pont unity",
                  "tree": tree["sha"], "parents": [head]}, "POST")
    print("commit", commit["sha"][:10], flush=True)
    api("git/refs/heads/main", {"sha": commit["sha"], "force": False},
        "PATCH")
    print("PUSH TERMINE", flush=True)


if __name__ == "__main__":
    main()
