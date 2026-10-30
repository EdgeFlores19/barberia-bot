"""Smoke test del flujo completo contra un servidor ya corriendo.

Cubre F2: FAQ, agendar, folio, cancelar (libera el slot) y reagendar.

Uso: python tests/smoke_test.py [http://127.0.0.1:8077] [codigo]
"""
from __future__ import annotations

import re
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8077"
CODIGO = sys.argv[2] if len(sys.argv) > 2 else "ambar"

RE_FOLIO = re.compile(r"AMB-[0-9A-Z]{4}")


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

    def agendar(servicio: str, nombre: str, slot: str | None = None) -> tuple[str, str]:
        """Devuelve (folio, slot elegido)."""
        enviar("agendar")
        r = enviar(servicio)
        opciones = r["respuestas"][0]["opciones"]
        assert opciones, "debe ofrecer horarios libres"
        elegido = slot or opciones[0]["id"]
        enviar(elegido)
        r = enviar(nombre)
        texto = r["respuestas"][0]["texto"]
        m = RE_FOLIO.search(texto)
        assert m, f"la confirmación debe traer folio: {texto}"
        return m.group(0), elegido

    enviar("hola")

    print("\n== agendar ==")
    folio, slot = agendar("manicura", "Edgar")
    assert c.get("/api/citas").json()["citas"], "la cita debe quedar en la base"

    print("\n== no se puede repetir el mismo horario ==")
    enviar("agendar")
    r = enviar("manicura")
    ids = [o["id"] for o in r["respuestas"][0]["opciones"]]
    assert slot not in ids, f"el slot {slot} ya está ocupado y no debe ofrecerse"
    enviar("menu")

    print("\n== FAQ ==")
    enviar("¿cuánto cuesta la manicura con gelish?")
    enviar("¿abren el domingo?")
    enviar("¿tienen estacionamiento?")

    print("\n== consultar folio suelto ==")
    r = enviar(folio)
    assert folio in r["respuestas"][0]["texto"], "el folio debe encontrar la cita"
    labels = [o["label"] for o in r["respuestas"][1]["opciones"]]
    assert "Cancelar la cita" in labels

    print("\n== cancelar libera el slot ==")
    r = enviar("cancelar")
    assert "Cancelada" in r["respuestas"][0]["texto"]
    citas = c.get("/api/citas").json()["citas"]
    mia = next(x for x in citas if x["folio"] == folio)
    assert mia["estado"] == "cancelada", "la cita debe quedar cancelada"

    print("\n== reagendar ==")
    folio2, slot2 = agendar("manicura", "Edgar", slot)
    r = enviar(f"mover {folio2}")
    libres = [o["id"] for o in r["respuestas"][0]["opciones"] if o["id"] != "menu"]
    assert libres, "debe haber otro horario para mover"
    r = enviar(libres[0])
    assert "Movida" in r["respuestas"][0]["texto"], r["respuestas"][0]["texto"]
    citas = c.get("/api/citas").json()["citas"]
    movida = next(x for x in citas if x["folio"] == folio2)
    assert movida["slot"] == libres[0], "el slot debe haber cambiado"
    assert movida["estado"] == "confirmada"

    print("\n== handoff ==")
    r = enviar("quiero hablar con una persona")
    assert r["estado"]["handoff"] is True, "el botón humano debe registrar handoff"

    print("\n== citas finales ==")
    for x in c.get("/api/citas").json()["citas"]:
        print(f"  {x['folio']} · {x['slot']} · {x['estado']} · {x['nombre']}")
    print("\nOK: flujo completo sin fallos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
