"""Motor conversacional determinista: menú, FAQ, agenda paso a paso.

El LLM no decide horarios ni precios. Solo redacta texto libre (app/llm.py).
"""
from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import config, llm, store

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _norm(t: str) -> str:
    s = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


def cargar_datos() -> dict[str, Any]:
    with open(config.DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class Sesion:
    sid: str
    paso: str = "inicio"
    servicio_id: str | None = None
    slot: str | None = None
    nombre: str = ""
    mensajes: int = 0
    handoff: bool = False
    resuelta: bool = False
    historial: list[dict[str, str]] = field(default_factory=list)


SESIONES: dict[str, Sesion] = {}
USO_DIA = {"fecha": datetime.now().date(), "n": 0}


# ---------- respuestas ----------
def _t(texto: str, **extra: Any) -> dict[str, Any]:
    return {"tipo": "texto", "texto": texto, **extra}


def _b(texto: str, opciones: list[tuple[str, str]], **extra: Any) -> dict[str, Any]:
    return {
        "tipo": "botones",
        "texto": texto,
        "opciones": [{"id": i, "label": l} for i, l in opciones],
        **extra,
    }


MENU = [
    ("agendar", "Agendar cita"),
    ("precios", "Precios y servicios"),
    ("ubicacion", "Horario y ubicación"),
    ("humano", "Hablar con el barbero"),
]

HANDOFF_PALABRAS = {"humano", "persona", "barbero", "asesor", "humana"}


def _es_handoff(txt: str) -> bool:
    """'humano', 'quiero hablar con una persona', 'pásame al barbero'..."""
    palabras = set(txt.split())
    return bool(palabras & HANDOFF_PALABRAS) or "hablar con" in txt


def bienvenida(datos: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _t(f"¡Qué onda! Soy el asistente de {datos['nombre']}."),
        _b("¿Qué necesitas?", MENU),
    ]


# ---------- slots ----------
def _horario_del_dia(datos: dict[str, Any], fecha: datetime) -> tuple[str, str] | None:
    reglas = datos["horario"]
    if fecha.weekday() <= 4:
        d = reglas["lunes_viernes"]
    elif fecha.weekday() == 5:
        d = reglas["sabado"]
    else:
        return None
    return d["abre"], d["cierra"]


def slots_disponibles(datos: dict[str, Any], servicio_id: str, ahora: datetime | None = None) -> list[str]:
    ahora = ahora or datetime.now()
    cfg = datos["reglas_agenda"]
    minimo = ahora + timedelta(hours=cfg["anticipacion_minima_horas"])
    ocupado = store.ocupados()
    duracion = next(s["minutos"] for s in datos["servicios"] if s["id"] == servicio_id)
    paso = timedelta(minutes=cfg["slots_minutos"])
    out: list[str] = []

    for dia in range(cfg["dias_adelante"] + 1):
        fecha = (ahora + timedelta(days=dia)).replace(hour=0, minute=0, second=0, microsecond=0)
        rango = _horario_del_dia(datos, fecha)
        if not rango:
            continue
        abre_h, abre_m = map(int, rango[0].split(":"))
        cierra_h, cierra_m = map(int, rango[1].split(":"))
        t = fecha.replace(hour=abre_h, minute=abre_m)
        fin = fecha.replace(hour=cierra_h, minute=cierra_m)
        while t + timedelta(minutes=duracion) <= fin:
            iso = t.isoformat(timespec="minutes")
            if t >= minimo and iso not in ocupado:
                out.append(iso)
            t += paso
        if len(out) >= 24:
            break
    return out


def _etiqueta_slot(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return f"{DIAS[d.weekday()].capitalize()} {d.day} {MESES[d.month - 1]} · {d:%H:%M}"


def _servicio(datos: dict[str, Any], sid: str) -> dict[str, Any] | None:
    return next((s for s in datos["servicios"] if s["id"] == sid), None)


# ---------- flujo ----------
async def manejar(sid: str, texto: str, datos: dict[str, Any]) -> tuple[list[dict[str, Any]], Sesion]:
    s = SESIONES.get(sid) or Sesion(sid)
    SESIONES[sid] = s
    s.mensajes += 1
    txt = _norm(texto)

    # presupuesto duro
    if USO_DIA["fecha"] != datetime.now().date():
        USO_DIA.update(fecha=datetime.now().date(), n=0)
    USO_DIA["n"] += 1
    if USO_DIA["n"] > config.MAX_MENSAJES_POR_DIA or s.mensajes > config.MAX_MENSAJES_POR_SESION:
        return [_t("La demo llegó a su límite de mensajes por hoy. Escríbeme directo y seguimos: "
                   f"{datos['telefono']}.")], s

    # salidas globales
    if txt in {"menu", "menú", "inicio", "hola", "buenas", "buenos dias", "buenas tardes", "hey", "que tal"}:
        if s.paso != "inicio" or s.historial:
            s.paso = "inicio"
            return bienvenida(datos), s
        s.paso = "inicio"
        return bienvenida(datos), s

    if _es_handoff(txt):
        s.handoff = True
        s.paso = "humano"
        return [_t("Listo, le paso tu conversación al barbero y te contesta en cuanto salga del sillón. "
                   f"Si urge, marca al {datos['telefono']}."),
                _b("¿Algo más mientras?", MENU)], s

    if s.paso in {"fin", "humano"}:
        s.paso = "inicio"
        return await _paso_inicio(s, txt, texto, datos)

    if s.paso == "inicio":
        return await _paso_inicio(s, txt, texto, datos)
    if s.paso == "agendar_servicio":
        return _paso_servicio(s, txt, datos)
    if s.paso == "agendar_slot":
        return _paso_slot(s, txt, texto, datos)
    if s.paso == "agendar_nombre":
        return _paso_nombre(s, texto, datos)
    if s.paso == "humano":
        return [_t("Ya tienes al barbero en la conversación. ¿Te ayudo con algo más?"),
                _b("Menú:", MENU)], s
    s.paso = "inicio"
    return bienvenida(datos), s


async def _paso_inicio(s: Sesion, txt: str, texto: str, datos: dict[str, Any]):
    if txt in {"agendar", "1", "cita", "agenda", "reservar", "quiero cita"}:
        return _ofrecer_servicios(s, datos), s
    if txt in {"precios", "2", "servicios", "cuanto"}:
        s.resuelta = True
        lineas = "\n".join(f"• {x['nombre']} — ${x['precio']} ({x['minutos']} min)" for x in datos["servicios"])
        return [_t(f"Estos son los servicios:\n{lineas}"), _b("¿Agendamos?", [("agendar", "Agendar cita")] + MENU[2:])], s
    if txt in {"ubicacion", "3", "horario", "donde"}:
        s.resuelta = True
        h = datos["horario"]
        return [_t(f"{datos['nombre']}\n{datos['direccion']}\n\n"
                   f"L-V {h['lunes_viernes']['abre']}–{h['lunes_viernes']['cierra']} · "
                   f"Sáb {h['sabado']['abre']}–{h['sabado']['cierra']} · Domingo cerrado"),
                _b("¿Agendamos tu cita?", [("agendar", "Sí, agendar")] + MENU[2:])], s

    # texto libre → LLM anclado
    r = await llm.responder(texto, datos)
    if r["fuente"] == "fallback":
        return [_t(r["texto"]), _b("¿Qué prefieres?", MENU)], s
    s.resuelta = True
    extra = [("agendar", "Agendar cita")] if r["fuente"] == "llm" else []
    return [_t(r["texto"]), _b("¿Algo más?", extra + MENU)], s


def _ofrecer_servicios(s: Sesion, datos: dict[str, Any]):
    s.paso = "agendar_servicio"
    opts = [(x["id"], f"{x['nombre']} · ${x['precio']}") for x in datos["servicios"]]
    return [_b("¿Qué servicio te haces?", opts)]


def _paso_servicio(s: Sesion, txt: str, datos: dict[str, Any]):
    sid = None
    for x in datos["servicios"]:
        if txt == x["id"] or _norm(x["nombre"]) == txt or txt in _norm(x["nombre"]):
            sid = x["id"]
            break
    if txt.isdigit() and 1 <= int(txt) <= len(datos["servicios"]):
        sid = datos["servicios"][int(txt) - 1]["id"]
    if not sid:
        return _ofrecer_servicios(s, datos), s

    s.servicio_id = sid
    s.paso = "agendar_slot"
    libres = slots_disponibles(datos, sid)
    if not libres:
        s.paso = "inicio"
        s.handoff = True
        return [_t("No me quedan huecos en los próximos 7 días. Te paso con el barbero para ver si abre un espacio.")], s
    sv = _servicio(datos, sid)
    return [_b(f"{sv['nombre']} (${sv['precio']}, {sv['minutos']} min).\n¿Qué día te queda bien?",
               [(i, _etiqueta_slot(i)) for i in libres[:8]] + [("humano", "Otra fecha / hablar con el barbero")])], s


def _paso_slot(s: Sesion, txt: str, texto: str, datos: dict[str, Any]):
    libres = slots_disponibles(datos, s.servicio_id or datos["servicios"][0]["id"])
    elegido = next((i for i in libres if _norm(texto) == _norm(_etiqueta_slot(i))), None)
    if elegido is None and txt.isdigit() and 1 <= int(txt) <= len(libres):
        elegido = libres[int(txt) - 1]
    if elegido is None and "-" in texto and "t" in texto.lower():
        elegido = next((i for i in libres if texto.startswith(i)), None)
    if elegido is None:
        return _paso_servicio(s, s.servicio_id or "", datos) if not libres else (
            [_b("Ese horario ya no está. Elige otro:", [(i, _etiqueta_slot(i)) for i in libres[:8]])], s)
    s.slot = elegido
    s.paso = "agendar_nombre"
    return [_t("Apartado. ¿A nombre de quién queda?")], s


def _paso_nombre(s: Sesion, texto: str, datos: dict[str, Any]):
    nombre = texto.strip()[:60]
    if len(nombre) < 2:
        return [_t("Dame un nombre para la cita, por favor.")], s
    s.nombre = nombre
    sv = _servicio(datos, s.servicio_id or "")
    cita = store.crear(s.slot or "", s.servicio_id or "", nombre)
    s.paso = "fin"
    s.resuelta = True
    return [_t(f"Listo, {nombre}. Quedas {_etiqueta_slot(cita['slot'])} para {sv['nombre']} "
               f"(${sv['precio']}). Llega 5 min antes.\n\n"
               f"Puedes cancelar sin costo hasta 2 h antes. Te esperamos en {datos['direccion']}."),
            _b("¿Algo más?", MENU)], s
