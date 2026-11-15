"""Dispara el deploy en Render del commit ya empujado y espera a que quede live.

Uso: python scripts/deploy_render.py [service_id]
Imprime estado y commit servido, nunca la API key.
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = "https://api.render.com/v1"
SVC = sys.argv[1] if len(sys.argv) > 1 else "srv-dbg3vpmc6qcc73e930og"
KEY_FILE = Path.home() / "Desktop" / "Importantes" / "render_api_key.txt"


def key() -> str:
    if os.environ.get("RENDER_API_KEY", "").strip():
        return os.environ["RENDER_API_KEY"].strip()
    if KEY_FILE.exists():
        return KEY_FILE.read_text(encoding="utf-8").strip()
    sys.exit("sin API key")


def call(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Authorization": "Bearer " + key(), "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


if __name__ == "__main__":
    d = call(f"/services/{SVC}/deploys", "POST", {"clearCache": "do_not_clear"})
    # La API responde el deploy tal cual (a veces envuelto en {"deploy": ...}).
    dep = d.get("deploy") if isinstance(d.get("deploy"), dict) else d
    dep_id = dep.get("id")
    print("deploy:", dep_id)
    for _ in range(60):
        time.sleep(10)
        r = call(f"/services/{SVC}/deploys/{dep_id}")
        dep = r.get("deploy") if isinstance(r.get("deploy"), dict) else r
        estado = dep.get("status")
        print(f"  {estado}", end="\r", flush=True)
        if estado in {"live", "build_failed", "canceled", "deactivated", "update_failed"}:
            print(f"\nestado final: {estado}")
            print("commit:", (dep.get("commit") or {}).get("id"))
            sys.exit(0 if estado == "live" else 1)
    print("\ntiempo agotado esperando el deploy")
    sys.exit(2)
