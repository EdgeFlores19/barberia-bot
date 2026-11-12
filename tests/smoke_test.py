"""Smoke test del flujo completo contra un servidor ya corriendo.

Cubre F2: FAQ, agendar, folio, cancelar (libera el slot) y reagendar.

Uso: python tests/smoke_test.py [http://127.0.0.1:8077] [codigo]

GET /api/citas exige la variable de entorno ADMIN_TOKEN (la misma del servidor).
Si el servidor no la tiene configurada, la ruta responde 503 y el script falla.
"""
from __future__ import annotations

import os
import re
import sys

from datetime import datetime, timedelta

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8077"
CODIGO = sys.argv[2] if len(sys.argv) > 2 else "ambar"
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
CABECERAS_ADMIN = {"X-Admin-Token": ADMIN_TOKEN}

RE_FOLIO = re.compile(r"AMB-[0-9A-Z]{4}")

# La cancelación libre vence pocas horas antes de la cita (reglas_agenda del negocio).
# Reservar el primer hueco del día hacía que el smoke dependiera de la hora a la que
# corriera: pasadas las ~14:00 el hueco más cercano ya estaba dentro de la ventana y
# el bot derivaba a la manicurista en vez de cancelar. Reservamos con margen.
MARGEN_CANCELACION = timedelta(hours=3)

# El runner de CI corre en UTC y los horarios que ofrece el bot vienen en la zona
# del negocio: comparar contra la hora del proceso daba 6 h de desfase y ningún
# hueco pasaba el margen. Usamos la misma zona que la app.
try:
    from zoneinfo import ZoneInfo

    TZ_AGENDA = ZoneInfo(os.getenv("CALENDAR_TZ", "America/Mexico_City"))
except Exception:  # Windows sin tzdata: caemos a la hora del proceso
    TZ_AGENDA = None


def mostrar(r: httpx.Response) -> list[dict]:
    r.raise_for_status()
    data = r.json()
    for x in data["respuestas"]:
        print(f"  bot> {x['texto']}")
        for o in x.get("opciones", []):
            print(f"       [{o['id']}] {o['label']}")
    return data["respuestas"]


def _slot_con_margen(opciones: list[dict], ahora: datetime | None = None) -> str:
    """Primer horario que aún se puede cancelar sin que lo cierre la manicurista."""
    if ahora is None:
        ahora = datetime.now(TZ_AGENDA).replace(tzinfo=None)
    limite = ahora + MARGEN_CANCELACION
    for o in opciones:
        try:
            inicio = datetime.fromisoformat(str(o["id"]))
        except (KeyError, ValueError):
            continue
        if inicio.tzinfo is not None:
            inicio = inicio.replace(tzinfo=None)
        if inicio >= limite:
            return str(o["id"])
    raise AssertionError("ningún horario ofrecido queda fuera de la ventana de cancelación")


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

    def horarios_del_dia(dia: str) -> list[dict]:
        """Elige un día y junta los horarios de todas sus páginas ('Más horarios')."""
        r = enviar(dia)
        horarios: list[dict] = []
        for _ in range(5):                      # tope: un día no pasa de 3 páginas
            ops = r["respuestas"][0]["opciones"]
            horarios += [o for o in ops if len(str(o["id"])) == 16]
            if not any(o["id"] == "mas_horarios" for o in ops):
                return horarios
            r = enviar("mas_horarios")
        return horarios

    def agendar(servicio: str, nombre: str, slot: str | None = None) -> tuple[str, str]:
        """Servicio -> día -> horario -> nombre. Devuelve (folio, slot elegido)."""
        enviar("agendar")
        r = enviar(servicio)
        dias = [o["id"] for o in r["respuestas"][0]["opciones"] if len(str(o["id"])) == 10]
        assert dias, "debe ofrecer días con horarios libres"
        elegido = slot
        for dia in dias:
            if slot:
                dia = slot[:10]
            horarios = horarios_del_dia(dia)
            assert horarios, f"el día {dia} debe ofrecer horarios"
            if slot:
                break
            try:
                elegido = _slot_con_margen(horarios)
                break
            except AssertionError:
                enviar("otro_dia")              # ese día ya no tiene un hueco con margen
        assert elegido, "ningún día ofrece un horario con margen de cancelación"
        r = enviar(elegido)
        texto = r["respuestas"][0]["texto"]
        if not RE_FOLIO.search(texto):
            # El bot solo pide el nombre la primera vez; en la misma sesión ya lo recuerda
            # y confirma directo al elegir el horario.
            r = enviar(nombre)
            texto = r["respuestas"][0]["texto"]
        m = RE_FOLIO.search(texto)
        assert m, f"la confirmación debe traer folio: {texto}"
        return m.group(0), elegido

    enviar("hola")

    print("\n== agendar ==")
    folio, slot = agendar("manicura", "Edgar")
    assert c.get("/api/citas", headers=CABECERAS_ADMIN).json()["citas"], "la cita debe quedar en la base"

    print("\n== no se puede repetir el mismo horario ==")
    enviar("agendar")
    enviar("manicura")
    ids = [o["id"] for o in horarios_del_dia(slot[:10])]
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
    citas = c.get("/api/citas", headers=CABECERAS_ADMIN).json()["citas"]
    mia = next(x for x in citas if x["folio"] == folio)
    assert mia["estado"] == "cancelada", "la cita debe quedar cancelada"

    print("\n== reagendar ==")
    folio2, slot2 = agendar("manicura", "Edgar", slot)
    r = enviar(f"mover {folio2}")
    libres = [o["id"] for o in r["respuestas"][0]["opciones"] if o["id"] != "menu"]
    assert libres, "debe haber otro horario para mover"
    r = enviar(libres[0])
    assert "Movida" in r["respuestas"][0]["texto"], r["respuestas"][0]["texto"]
    citas = c.get("/api/citas", headers=CABECERAS_ADMIN).json()["citas"]
    movida = next(x for x in citas if x["folio"] == folio2)
    assert movida["slot"] == libres[0], "el slot debe haber cambiado"
    assert movida["estado"] == "confirmada"

    print("\n== handoff ==")
    r = enviar("quiero hablar con una persona")
    assert r["estado"]["handoff"] is True, "el botón humano debe registrar handoff"

    print("\n== citas finales ==")
    for x in c.get("/api/citas", headers=CABECERAS_ADMIN).json()["citas"]:
        print(f"  {x['folio']} · {x['slot']} · {x['estado']} · {x['nombre']}")
    print("\nOK: flujo completo sin fallos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
