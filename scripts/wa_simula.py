"""Simula un webhook de Meta contra el servidor local (sin cuenta de Meta).

Uso:
    WA_MODO=consola WA_VERIFY_TOKEN=prueba WA_APP_SECRET=prueba \
        .venv/Scripts/python.exe -m uvicorn app.main:app --port 8077
    .venv/Scripts/python.exe scripts/wa_simula.py "precios"
    .venv/Scripts/python.exe scripts/wa_simula.py "agendar"
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import httpx

BASE = os.getenv("WA_BASE", "http://127.0.0.1:8077")
SECRETO = os.getenv("WA_APP_SECRET", "prueba")
TELEFONO = os.getenv("WA_TELEFONO", "5215500000001")


def main() -> int:
    texto = " ".join(sys.argv[1:]).strip() or "menu"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "0",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "messages": [{
                        "id": f"wamid.{uuid.uuid4().hex}",
                        "from": TELEFONO,
                        "timestamp": str(int(time.time())),
                        "type": "text",
                        "text": {"body": texto},
                    }],
                },
            }],
        }],
    }
    cuerpo = json.dumps(payload).encode()
    firma = "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()
    r = httpx.post(
        f"{BASE}/wa/webhook",
        content=cuerpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": firma},
        timeout=20,
    )
    print(r.status_code, r.text)
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
