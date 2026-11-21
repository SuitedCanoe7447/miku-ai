# -*- coding: utf-8 -*-
"""Serveur d'avatar « vrai modèle » : service web + postures.

Sert :
  - la page viewer (web/index.html, three.js),
  - le modèle GLB construit par miku.avatar.gltf_builder,
  - /rig_map.json (mapping rôle -> os),
  - /pose  : la posture courante (JSON) — calculée côté serveur par la
    politique apprise (miku.body) ou, tant que le moteur n'est pas
    rebranché, par une oscillation de TEST clairement marquée.

Usage : python -m miku.avatar.serve2 [--port 8080] [--model PATH]
"""

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEB = os.path.join(ROOT, "web")
MODEL = os.path.join(ROOT, "model_raw", "miku_megamix.glb")
RIGMAP = os.path.join(ROOT, "data", "rig_map.json")


class PoseSource:
    """Posture courante. Pour l'instant : oscillation de TEST (plomberie).

    Quand le moteur physique sera rebranché, cette classe appellera la
    politique apprise (miku_skill_*.npz) pour produire les angles. C'est
    un simple test du tuyau, pas une pose définitive.
    """

    def __init__(self):
        self.mode = "test-oscillation"
        self.t0 = time.time()

    def get(self):
        t = time.time() - self.t0
        ph = t * 2.0 * np.pi * 1.4  # ~1.4 pas/s de TEST
        swing = float(np.sin(ph))
        lift = max(0.0, float(np.cos(ph)))
        return {
            "t": t,
            "mode": self.mode,
            "hips": {"lift": 0.02 * lift, "lean": 0.06},
            "legs": {
                "L": {"hip": 28.0 * swing, "knee": 42.0 * max(0.0, -swing),
                      "ankle": -12.0 * swing},
                "R": {"hip": -28.0 * swing, "knee": 42.0 * max(0.0, swing),
                      "ankle": 12.0 * swing},
            },
            "arms": {
                "L": {"shoulder": 8.0 * swing, "elbow": -14.0},
                "R": {"shoulder": -8.0 * swing, "elbow": -14.0},
            },
            "spine": {"swing": 2.0 * float(np.sin(ph))},
            "note": "oscillation de TEST — moteur appris à rebrancher",
        }


SOURCE = PoseSource()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html", "/viewer"):
            with open(os.path.join(WEB, "index.html"), "rb") as f:
                return self._send(200, "text/html; charset=utf-8", f.read())
        if p == "/model.glb":
            with open(MODEL, "rb") as f:
                return self._send(200, "model/gltf-binary", f.read())
        if p == "/rig_map.json":
            with open(RIGMAP, "rb") as f:
                return self._send(200, "application/json", f.read())
        if p == "/pose":
            return self._send(200, "application/json",
                              json.dumps(SOURCE.get()).encode())
        if p == "/health":
            return self._send(200, "application/json", b'{"ok":true}')
        self._send(404, "text/plain", b"404")


def serve(port=8080, model=None):
    global MODEL
    if model:
        MODEL = model
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"MIKU avatar : http://127.0.0.1:{port}  (model={MODEL})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--model", default=None)
    a = ap.parse_args()
    serve(a.port, a.model)
