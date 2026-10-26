"""Smoke test del flujo completo contra un servidor ya corriendo.

Uso: python tests/smoke_test.py [http://127.0.0.1:8077] [codigo]
"""
from __future__ import annotations

import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8077"
CODIGO = sys.argv[2] if len(sys.argv) > 2 else "navajazo"


def mostrar(r: httpx.Response) -> list[dict]:
    r.raise_for_status()
    data = r.json()
    for x in data["respuestas"]:
        print(f"  bot> {x['texto']}")
        for o in x.get("opciones", []):
            print(f"       [{o['id']}] {o['label']}")
    return data["respuestas"]


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=20)
    print("== health ==", c.get("/health").json())

    print("\n== código inválido ==")
    malo = c.post("/api/sesion", json={"codigo": "nope"})
    print("  status:", malo.status_code)
    assert malo.status_code == 401, "el código inválido debe rechazarse"

    print("\n== nueva sesión ==")
    s = c.post("/api/sesion", json={"codigo": CODIGO}).json()
    sid = s["sid"]
    for x in s["respuestas"]:
        print(f"  bot> {x['texto']}")

    def enviar(texto: str) -> dict:
        print(f"\n  yo> {texto}")
        r = c.post("/api/mensaje", json={"sid": sid, "texto": texto, "codigo": CODIGO})
        mostrar(r)
        return r.json()

    enviar("hola")
    r = enviar("agendar")
    assert r["respuestas"][0]["opciones"], "debe ofrecer servicios"

    r = enviar("corte")
    slots = r["respuestas"][0]["opciones"]
    assert slots, "debe ofrecer horarios libres"
    primer_slot = slots[0]["id"]

    r = enviar("corte_barba")
    r = enviar("corte")
    r = enviar(primer_slot)
    r = enviar("Edgar")
    assert r["estado"]["resuelta"] is True, "agendar debe marcar la conversación como resuelta"

    citas = c.get("/api/citas").json()["citas"]
    print("\n== citas en store ==", citas)
    mias = [x for x in citas if x["slot"] == primer_slot]
    assert len(mias) == 1, f"debe existir exactamente una cita en {primer_slot}"

    enviar("repetir")
    r = enviar("agendar")
    r = enviar("corte")
    ids = [o["id"] for o in r["respuestas"][0]["opciones"]]
    assert primer_slot not in ids, f"el slot {primer_slot} ya está ocupado y no debe ofrecerse"
    enviar("menu")
    enviar("¿cuánto cuesta el corte con barba?")
    enviar("¿abren el domingo?")
    enviar("¿tienen estacionamiento?")
    r = enviar("quiero hablar con una persona")
    assert r["estado"]["handoff"] is True, "el botón humano debe registrar handoff"

    print("\n== citas finales ==", c.get("/api/citas").json()["citas"])
    print("\nOK: flujo completo sin fallos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
