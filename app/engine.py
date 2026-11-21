"""Motor conversacional determinista: menú, FAQ, agendar, cancelar y reagendar.

El LLM no decide horarios ni precios. Solo redacta texto libre (app/llm.py).
La disponibilidad la calcula app/slots.py; las citas viven en app/store.py (SQLite).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import agendas, anticipo, avisos, calendar_sync, config, llm, reloj, store
from . import slots as horarios
from .telefono import t10 as _t10

log = logging.getLogger("ambar.engine")

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# "Cualquiera" en la sesión: la agenda se elige al crear la cita, nunca se guarda así.
CUALQUIERA = "*"

RE_FOLIO = re.compile(r"\bAMB[-\s]?([0-9A-Z]{4})\b")
RE_FOLIO_GEN = re.compile(r"\b([A-Z]{3}-[0-9A-Z]{4})\b")


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
    folio: str | None = None
    mensajes: int = 0
    handoff: bool = False
    resuelta: bool = False
    historial: list[dict[str, str]] = field(default_factory=list)
    canal: str = "web"                      # "web" | "wa"
    telefono: str = ""                      # tal como lo manda el canal
    agenda_id: str = ""                     # clave corta de la agenda elegida ("" = la de siempre)
    agenda_nombre: str = ""                 # nombre con el que se le presenta a la clienta
    hora_sugerida: str | None = None         # hora ya pedida; se conserva al preguntar con quién
    ultima_actividad: datetime = field(default_factory=reloj.ahora)
    handoff_avisado: bool = False           # ya se avisó a la manicurista en este handoff
    sin_avance: int = 0                     # respuestas del LLM que no movieron el paso
    dia_sugerido: int | None = None          # día pedido en el turno anterior
    dia_fecha: str | None = None             # día ya elegido para esta cita (AAAA-MM-DD)
    pagina_horas: int = 0                    # página de horarios que se está viendo
    aviso_extra: str = ""                    # detalle para el aviso de handoff (lo consume _avisar_handoff)


SESIONES: dict[str, Sesion] = {}
USO_DIA = {"fecha": reloj.ahora().date(), "n": 0}


def _purgar_sesiones() -> None:
    """Descarta sesiones tan viejas que ya no pueden estar en conversación.

    El tope respeta el TTL largo de `humano`: una conversación con la
    manicurista sigue viva 12 h aunque el TTL corto sea de 30 min.
    """
    tope = reloj.ahora() - timedelta(
        minutes=max(config.SESION_TTL_MIN * 8, config.SESION_HUMANO_TTL_MIN)
    )
    for sid in [k for k, v in SESIONES.items() if v.ultima_actividad < tope]:
        SESIONES.pop(sid, None)


def recortar_sesiones(maximo: int) -> None:
    """Deja como mucho `maximo` sesiones, desalojando las más inactivas del chat web.

    Las de WhatsApp no se tocan: pueden estar en manos de la manicurista (TTL de 12 h).
    """
    exceso = len(SESIONES) - maximo
    if exceso <= 0:
        return
    web = sorted((x for x in SESIONES.values() if x.canal == "web"), key=lambda x: x.ultima_actividad)
    for x in web[:exceso]:
        SESIONES.pop(x.sid, None)


# ---------- respuestas ----------
def _t(texto: str, **extra: Any) -> dict[str, Any]:
    return {"tipo": "texto", "texto": texto, **extra}


def _b(texto: str, opciones: list[tuple[str, str]], **extra: Any) -> dict[str, Any]:
    vistas: set[str] = set()
    filas: list[dict[str, str]] = []
    for i, l in opciones:
        if i in vistas:
            continue          # Meta rechaza la lista entera: (#131009) Duplicated row id
        vistas.add(i)
        filas.append({"id": i, "label": l})
    return {
        "tipo": "botones",
        "texto": texto,
        "opciones": filas,
        **extra,
    }


MENU = [
    ("agendar", "Agendar cita"),
    ("citas", "Mi cita"),
    ("precios", "Precios y servicios"),
    ("ubicacion", "Horario y ubicación"),
    ("humano", "Hablar con la manicurista"),
]

HANDOFF_PALABRAS = {"humano", "persona", "manicurista", "dueña", "estilista", "humana"}


def _es_handoff(txt: str) -> bool:
    """'humano', 'quiero hablar con una persona', 'pásame a la manicurista'..."""
    palabras = set(txt.split())
    return bool(palabras & HANDOFF_PALABRAS) or "hablar con" in txt


def _es_cancelar(txt: str) -> bool:
    return any(p in txt.split() for p in {"cancelar", "cancela", "anular", "anula"})


def _es_reagendar(txt: str) -> bool:
    return any(p in txt.split() for p in {"reagendar", "mover", "cambiar", "cambio", "reprogramar"})


def _folio_de(texto: str) -> str | None:
    """Acepta 'AMB-7K4Q', 'amb 7k4q' o el folio suelto dentro de una frase.

    El prefijo AMB se busca primero porque un patrón genérico de 3 letras + 4
    caracteres también hace match con palabras normales ('CAN-CELA').
    """
    alto = texto.upper()
    m = RE_FOLIO.search(alto)
    if m:
        return f"{store.PREFIJO_FOLIO}-{m.group(1)}"
    m = RE_FOLIO_GEN.search(alto)
    return m.group(1) if m else None


def bienvenida(datos: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _t(f"¡Qué onda! Soy el asistente de {datos['nombre']}."),
        _b("¿Qué necesitas?", MENU),
    ]


# ---------- disponibilidad ----------
# ---------- agendar en dos pasos: primero el día, luego el horario de ese día ----------
HORAS_POR_PAGINA = 8        # + "más horarios"/"otro día" + "humano" = 10 filas, el tope de WhatsApp
MAX_DIAS_MENU = 9           # + "humano" = 10 filas
MAX_AGENDAS_MENU = 9        # + "Cualquiera" = 10 filas, el tope de la lista de WhatsApp
_DIAS_CORTO = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
_OTRA_FECHA = ("humano", "Otra fecha / hablar con la manicurista")


def _fechas_con_huecos(libres: list[str]) -> list[str]:
    """Días (AAAA-MM-DD) que tienen al menos un hueco, en orden y sin repetir."""
    fechas: list[str] = []
    for i in libres:
        if i[:10] not in fechas:
            fechas.append(i[:10])
        if len(fechas) == MAX_DIAS_MENU:
            break
    return fechas


def _etiqueta_dia(fecha: str) -> str:
    """'Hoy · Mié 11 nov', 'Mañana · Jue 12 nov', 'Vie 13 nov' (cabe en una fila de WhatsApp)."""
    d = date.fromisoformat(fecha)
    base = f"{_DIAS_CORTO[d.weekday()]} {d.day} {MESES[d.month - 1]}"
    hoy = reloj.ahora().date()
    if d == hoy:
        return f"Hoy · {base}"
    if d == hoy + timedelta(days=1):
        return f"Mañana · {base}"
    return base


def _nombre_dia(fecha: str) -> str:
    d = date.fromisoformat(fecha)
    return f"{DIAS[d.weekday()].capitalize()} {d.day} {MESES[d.month - 1]}"


def _menu_dias(s: Sesion, datos: dict[str, Any], sid: str, cabecera: str):
    """Paso 1: ¿qué día? Una opción por día con huecos, sin horarios."""
    fechas = _fechas_con_huecos(slots_disponibles(datos, sid, agenda_id=s.agenda_id))
    s.paso = "agendar_dia"
    s.dia_fecha = None
    s.dia_sugerido = None          # va a elegir el día de nuevo: no arrastrar el anterior
    s.pagina_horas = 0
    return [_b(cabecera, [(f, _etiqueta_dia(f)) for f in fechas] + [_OTRA_FECHA])], s


def _opciones_horas(s: Sesion, del_dia: list[str]) -> list[tuple[str, str]]:
    """Una página de horarios del día elegido, más cómo seguir (más horarios u otro día)."""
    p = s.pagina_horas
    pagina = del_dia[p * HORAS_POR_PAGINA:(p + 1) * HORAS_POR_PAGINA]
    seguir = (
        ("mas_horarios", "Más horarios ▸")
        if (p + 1) * HORAS_POR_PAGINA < len(del_dia)
        else ("otro_dia", "Elegir otro día")
    )
    return [(i, i[11:16]) for i in pagina] + [seguir, _OTRA_FECHA]


def _menu_horas(s: Sesion, datos: dict[str, Any], sid: str, fecha: str, cabecera: str):
    """Paso 2: los horarios del día ya elegido (paginados). Si ese día se llenó, vuelve al paso 1."""
    del_dia = [i for i in slots_disponibles(datos, sid, agenda_id=s.agenda_id) if i[:10] == fecha]
    if not del_dia:
        return _menu_dias(s, datos, sid, "Ese día ya no tiene horarios. ¿Qué otro día te queda bien?")
    ultima = (len(del_dia) - 1) // HORAS_POR_PAGINA
    s.pagina_horas = min(max(s.pagina_horas, 0), ultima)
    s.dia_fecha = fecha
    s.paso = "agendar_slot"
    return [_b(cabecera, _opciones_horas(s, del_dia))], s


async def _fijar_slot(s: Sesion, elegido: str, datos: dict[str, Any]):
    s.slot = elegido
    if s.nombre:
        return await _paso_nombre(s, s.nombre, datos)
    s.paso = "agendar_nombre"
    return [_t("Apartado. ¿A nombre de quién queda?")], s


def _calendario(agenda_id: str) -> str | None:
    """Calendario de Google de esa agenda; None (el de siempre) si todavía no se sabe.

    Con `""` —una sola agenda, como antes— se devuelve None y todo va a GOOGLE_CALENDAR_ID,
    así el modo actual no cambia de comportamiento ni necesita migración.
    """
    if not agenda_id:
        return None
    agenda = store.agenda_por_clave(agenda_id)
    return (agenda or {}).get("calendar_id") or None


def _agenda_de(s: Sesion) -> dict[str, Any]:
    """La agenda elegida en la sesión, tal como está en SQLite; {} en el modo de siempre."""
    if not s.agenda_id:
        return {}
    return store.agenda_por_clave(s.agenda_id) or {}


def _ocupadas(agenda_id: str = "") -> list[dict[str, Any]]:
    """Citas de SQLite de esa agenda más lo anotado a mano en su Calendar (solo caché).

    Sin agenda se ve todo, como siempre: las citas viejas (sin agenda) y el calendario global.
    """
    return store.confirmadas(agenda_id or None) + calendar_sync.bloqueos(_calendario(agenda_id))


def _agendas_activas() -> list[dict[str, Any]]:
    """Las agendas dadas de alta, en el orden en que se le presentan a la clienta."""
    return store.agendas(solo_activas=True)


def _huecos_dia(
    datos: dict[str, Any], servicio_id: str, dia: int, agenda_id: str = ""
) -> list[str]:
    """Todos los huecos del próximo `dia` (0=lunes), sin el tope de 24 de la lista corta."""
    todas = slots_disponibles(datos, servicio_id, agenda_id=agenda_id)
    return [i for i in todas if datetime.fromisoformat(i).weekday() == dia]


_LIMITE_HUECOS = 1000


def slots_disponibles(
    datos: dict[str, Any],
    servicio_id: str,
    ahora: datetime | None = None,
    excluir_folio: str | None = None,
    agenda_id: str = "",
) -> list[str]:
    """Huecos donde cabe el servicio completo, según app/slots.py.

    `agenda_id` acota a las citas de esa agenda; sin él se ven todas, como antes.
    El comodín `CUALQUIERA` devuelve la unión de todas las agendas activas.
    """
    ahora = ahora or reloj.ahora()
    if agenda_id == CUALQUIERA and not excluir_folio:
        activas = _agendas_activas()
        if activas:
            return agendas.horarios_libres(
                datos, servicio_id, activas, ahora, limite=_LIMITE_HUECOS
            )
        agenda_id = ""                      # sin agendas dadas de alta: la de siempre
    citas = store.confirmadas(agenda_id or None)
    if excluir_folio:
        # Al reagendar, su propio horario no debe bloquearse a sí mismo.
        citas = [c for c in citas if c["folio"] != excluir_folio]
    citas = citas + calendar_sync.bloqueos(_calendario(agenda_id))
    # Sin tope práctico: con el límite por defecto (24) solo entraban hoy y mañana y el menú
    # de días nunca mostraba el resto de la ventana. Cada pantalla recorta lo que muestra.
    return horarios.generar_slots(datos, servicio_id, citas, ahora, limite=_LIMITE_HUECOS)


def _candidatas_al_slot(
    datos: dict[str, Any],
    servicio_id: str,
    slot: str,
    agenda_id: str = "",
    telefono: str = "",
) -> list[str]:
    """Agendas donde ese horario sigue libre, en el orden en que se asignaría.

    Con `"cualquiera"` la cita se decide aquí y no al mostrar horarios: si dos clientas
    piden lo mismo, la segunda prueba la siguiente candidata antes de rendirse.
    """
    activas = _agendas_activas()
    if agenda_id != CUALQUIERA or not activas:
        return []
    por_agenda = agendas.candidatas(
        datos, servicio_id, activas, reloj.ahora(), limite=_LIMITE_HUECOS
    )
    citas = store.listar()
    restantes = dict(por_agenda)
    orden: list[str] = []
    while restantes:
        clave = agendas.elegir(slot, restantes, citas, activas, telefono)
        if clave is None:
            break
        orden.append(clave)
        restantes.pop(clave, None)
    return orden


def _validar_slot(
    datos: dict[str, Any], servicio_id: str, slot: str, agenda_id: str = ""
) -> tuple[bool, str]:
    """¿Cabe la cita en esa agenda? Con el comodín, basta con que quepa en alguna."""
    ahora = reloj.ahora()
    if agenda_id != CUALQUIERA:
        return horarios.validar(datos, servicio_id, slot, _ocupadas(agenda_id), ahora)
    activas = _agendas_activas()
    if not activas:
        return horarios.validar(datos, servicio_id, slot, _ocupadas(""), ahora)
    motivo = ""
    for a in activas:
        ok, por_que = horarios.validar(
            datos, servicio_id, slot, _ocupadas(str(a["clave"])), ahora
        )
        if ok:
            return True, ""
        motivo = motivo or por_que
    return False, motivo or "ocupado"


def _crear_cita(
    s: Sesion,
    datos: dict[str, Any],
    servicio_id: str,
    slot: str,
    nombre: str,
    **extra: Any,
) -> dict[str, Any]:
    """Crea la cita en la agenda de la sesión; con el comodín, la asigna y reintenta.

    `store.crear` valida el choque por agenda dentro de su candado: si la agenda elegida
    acaba de ocuparse, se prueba la siguiente candidata y solo si no queda ninguna se
    propaga `SlotOcupado`.
    """
    telefono = s.telefono if s.canal == "wa" else ""
    if s.agenda_id != CUALQUIERA or not _agendas_activas():
        agenda_id = "" if s.agenda_id == CUALQUIERA else s.agenda_id
        return store.crear(
            slot, servicio_id, nombre, telefono, agenda_id=agenda_id,
            agenda_nombre=(_agenda_de(s).get("nombre") or s.agenda_nombre), **extra,
        )
    for clave in _candidatas_al_slot(datos, servicio_id, slot, s.agenda_id, s.telefono):
        agenda = store.agenda_por_clave(clave) or {}
        try:
            return store.crear(
                slot, servicio_id, nombre, telefono, agenda_id=clave,
                agenda_nombre=agenda.get("nombre") or "", **extra,
            )
        except store.SlotOcupado:
            continue                        # se llenó entre la lectura y la escritura
    raise store.SlotOcupado(slot)


def _con_quien(cita: dict[str, Any]) -> str:
    """", con Ana" cuando la cita tiene agenda; nada en el modo de siempre."""
    nombre = (cita.get("agenda_nombre") or "").strip()
    return f" con {nombre}" if nombre else ""



def _etiqueta_slot(iso: str) -> str:
    return horarios.etiqueta(iso)


def _servicio(datos: dict[str, Any], sid: str) -> dict[str, Any] | None:
    return next((s for s in datos["servicios"] if s["id"] == sid), None)


def _elegir_slot(libres: list[str], txt: str, texto: str) -> str | None:
    """Acepta el id ISO (lo que manda un botón), la etiqueta escrita o un número."""
    t = texto.strip()
    if t in libres:
        return t
    for i in libres:
        if _norm(t) == _norm(_etiqueta_slot(i)):
            return i
    if txt.isdigit() and 1 <= int(txt) <= len(libres):
        return libres[int(txt) - 1]
    return None


# ---------- interpretación de texto libre ----------
# El cliente escribe "manicura y pedicura el miércoles a las 6". El menú solo
# entiende ids y números, así que sin esto cada frase caía al LLM y el paso se
# quedaba en `inicio`: el bot repetía la misma pregunta y nunca agendaba.
_STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "y", "o", "para", "con", "por",
    "menores", "quiero", "solo", "sólo", "un", "una", "mi", "me", "favor",
}
_PALABRAS_INFO = {
    "precio", "precios", "cuesta", "cuestan", "cobran", "cuanto", "coste", "vale",
}
_PALABRAS_HORARIO = {
    "horario", "horarios", "abren", "cierran", "abierto", "cerrado", "ubicacion",
    "direccion", "donde", "estacionamiento", "pago", "tarjeta", "efectivo",
}
_DIAS_TOKEN = {
    "lunes": 0, "lun": 0,
    "martes": 1, "mar": 1,
    "miercoles": 2, "mie": 2, "mierc": 2,
    "jueves": 3, "jue": 3,
    "viernes": 4, "vie": 4,
    "sabado": 5, "sab": 5,
    "domingo": 6, "dom": 6,
}
RE_HORA_A = re.compile(
    r"\ba\s+las\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|hrs|h|"
    r"de la tarde|de la noche|de la manana)?"
)
RE_HORA_B = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|hrs)\b")
RE_HORA_C = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _tokens(texto: str) -> set[str]:
    return {
        w for w in re.split(r"[^a-z0-9]+", _norm(texto))
        if len(w) > 2 and w not in _STOPWORDS
    }


def _detectar_servicio(texto: str, datos: dict[str, Any]) -> str | None:
    """Deduce el servicio por nombre o alias: 'gelish', 'manicura y pedicura'…

    Puntúa cuántas palabras del nombre aparecen en el mensaje, así que
    'manicura y pedicura' prefiere el combo sobre la manicura sola.
    """
    dicho = _tokens(texto)
    if not dicho:
        return None
    mejor: str | None = None
    mejor_clave = (0.0, 0.0)
    for sv in datos["servicios"]:
        del_id = {w for w in re.split(r"[^a-z0-9]+", _norm(sv["id"])) if len(w) > 2}
        propio = _tokens(sv["nombre"]) | del_id
        if not propio:
            continue
        comunes = propio & dicho
        if not comunes:
            continue
        # el nombre manda; el id desempata ("pedicura" gana a "manicura_pedicura")
        clave = (len(comunes) / len(propio), len(del_id & dicho) / len(del_id) if del_id else 0.0)
        if clave > mejor_clave:
            mejor, mejor_clave = sv["id"], clave
    return mejor


def _hora_hhmm(texto: str) -> str | None:
    """Extrae '18:00' de 'a las 6 pm', '6:30', '6 de la tarde'…"""
    for m in RE_HORA_A.finditer(texto):
        hh, mm, suf = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "")
        if m.group(3) is None and m.group(2) is None:
            # "a las 6" sin sufijo: en una barbería que abre a las 10 es la tarde
            if 1 <= hh <= 9:
                hh += 12
            else:
                continue
        if suf in {"pm", "de la tarde", "de la noche"} and hh < 12:
            hh += 12
        elif suf == "am" and hh == 12:
            hh = 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
    for m in RE_HORA_B.finditer(texto):
        hh, mm, suf = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if suf == "pm" and hh < 12:
            hh += 12
        elif suf == "am" and hh == 12:
            hh = 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
    for m in RE_HORA_C.finditer(texto):
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
    return None


def _detectar_dia_hora(texto: str) -> tuple[int | None, str | None]:
    """(día de la semana 0-6, 'HH:MM') de un mensaje en lenguaje natural."""
    t = _norm(texto)
    dia = None
    for w in re.split(r"[^a-z0-9]+", t):
        if w in _DIAS_TOKEN:
            dia = _DIAS_TOKEN[w]
            break
    if dia is None:
        hoy = reloj.ahora()
        if re.search(r"\bmanana\b", t):
            dia = (hoy + timedelta(days=1)).weekday()
        elif re.search(r"\bhoy\b", t):
            dia = hoy.weekday()
    return dia, _hora_hhmm(t)


def _preferir_slot(libres: list[str], dia: int | None, hora: str | None) -> str | None:
    """Hueco más cercano a lo que el cliente pidió (día y/o hora ±30 min)."""
    if dia is None and hora is None:
        return None
    objetivo = None
    if hora:
        hh, mm = (int(x) for x in hora.split(":"))
        objetivo = hh * 60 + mm
    candidatos: list[tuple[int, str]] = []
    for iso in libres:
        try:
            d = datetime.fromisoformat(iso)
        except ValueError:
            continue
        if dia is not None and d.weekday() != dia:
            continue
        delta = 0
        if objetivo is not None:
            delta = abs((d.hour * 60 + d.minute) - objetivo)
            if delta > 30:
                continue
        candidatos.append((delta, iso))
    if not candidatos:
        return None
    candidatos.sort()
    return candidatos[0][1]


def _hora_legible(hhmm: str) -> str:
    """'18:00' → '6:00 pm', para hablarle igual que el cliente."""
    try:
        hh, mm = (int(x) for x in hhmm.split(":"))
    except ValueError:
        return hhmm
    suf = "am" if hh < 12 else "pm"
    return f"{hh % 12 or 12}:{mm:02d} {suf}"


def _cercanos(libres: list[str], dia: int | None, hora: str | None, n: int = 6) -> list[str]:
    """Huecos más parecidos a lo pedido, priorizando el mismo día y la hora."""
    objetivo = None
    if hora:
        try:
            hh, mm = (int(x) for x in hora.split(":"))
            objetivo = hh * 60 + mm
        except ValueError:
            objetivo = None

    def dist(iso: str) -> int:
        try:
            d = datetime.fromisoformat(iso)
        except ValueError:
            return 10 ** 6
        dd = abs(d.weekday() - dia) * 1440 if dia is not None else 0
        dt = abs((d.hour * 60 + d.minute) - objetivo) if objetivo is not None else 0
        return dd + dt

    return sorted(libres, key=lambda i: (dist(i), i))[:n]


def _resumen_cita(cita: dict[str, Any], datos: dict[str, Any]) -> str:
    sv = _servicio(datos, cita["servicio_id"])
    nombre_sv = sv["nombre"] if sv else cita["servicio_id"]
    precio = f" (${sv['precio']})" if sv else ""
    return f"Folio {cita['folio']}: {nombre_sv}{precio}\n{_etiqueta_slot(cita['slot'])}"


def _fuera_de_ventana(cita: dict[str, Any], datos: dict[str, Any]) -> bool:
    """True si ya no se puede cancelar libremente (menos de N horas antes)."""
    horas = datos["reglas_agenda"]["cancelacion_libre_hasta_horas"]
    try:
        inicio = datetime.fromisoformat(cita["slot"])
    except ValueError:
        return False
    return inicio - reloj.ahora() < timedelta(hours=horas)


# ---------- flujo ----------
def _hace_una_hora() -> str:
    """Marca ISO de hace una hora, para la ventana del tope por hora."""
    return (reloj.ahora() - timedelta(hours=1)).isoformat(timespec="seconds")


def _hoy_iso() -> str:
    """Marca ISO de la medianoche de hoy (hora del negocio)."""
    return reloj.ahora().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds"
    )


def _limite_wa(s: Sesion, datos: dict[str, Any]) -> list[dict[str, Any]] | None:
    """None si el mensaje puede pasar; si no, la respuesta de bloqueo.

    El mensaje bloqueado no se anota en `wa_uso`: se suma solo al pasar, para
    que el tope no se dispare a sí mismo.
    """
    tel = _t10(s.telefono)
    if config.WA_ALLOWLIST and tel not in config.WA_ALLOWLIST:
        return [_t(f"Por ahora no puedo atender este número. Marca al {datos['telefono']} "
                   "y con gusto te ayudamos.")]
    ahora = reloj.ahora()
    if store.wa_contar_desde(_hace_una_hora(), tel) >= config.WA_MAX_POR_HORA:
        return [_t("Me llegaron muchos mensajes seguidos. Espera unos minutos y seguimos.")]
    if (store.wa_contar_desde(_hoy_iso(), tel) >= config.WA_MAX_POR_DIA
            or store.wa_contar_desde(_hoy_iso()) >= config.WA_MAX_GLOBAL_POR_DIA):
        return [_t("Hoy ya no alcanzo a contestar más por este medio. Escríbeme mañana "
                   f"o marca al {datos['telefono']}.")]
    store.wa_sumar(tel, ahora.isoformat(timespec="seconds"))
    return None


async def manejar(
    sid: str,
    texto: str,
    datos: dict[str, Any],
    canal: str = "web",
    telefono: str = "",
    media: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], Sesion]:
    s = SESIONES.get(sid) or Sesion(sid)
    SESIONES[sid] = s
    if canal != "web":
        s.canal = canal
    if telefono:
        s.telefono = telefono

    ahora = reloj.ahora()
    inactivo = ahora - s.ultima_actividad
    if inactivo > timedelta(minutes=config.SESION_TTL_MIN):
        # Conversación nueva: contador nuevo y, salvo que esté en manos de la
        # manicurista, se retoma desde el menú.
        s.mensajes = 0
        if s.paso not in ("inicio", "humano"):
            s.paso, s.servicio_id, s.slot, s.folio, s.nombre = "inicio", None, None, None, ""
    if s.paso == "humano" and inactivo > timedelta(minutes=config.SESION_HUMANO_TTL_MIN):
        # El TTL corto no reinicia `humano`: el bot no habla encima del barbero.
        s.paso, s.handoff = "inicio", False

    # El flag solo vale mientras la conversación está en manos de la manicurista o en el
    # turno en que se levantó (p. ej. "sin huecos" deja al bot activo con el flag arriba).
    # Al empezar otro turno ya fuera de `humano`, ese handoff quedó atrás.
    if s.handoff and s.paso != "humano":
        s.handoff = False

    s.ultima_actividad = ahora
    s.mensajes += 1
    _purgar_sesiones()
    txt = _norm(texto)

    # 1. Gate de sesión primero: un mensaje bloqueado no gasta nada.
    if s.canal == "wa":
        if s.mensajes > config.MAX_MENSAJES_POR_SESION_WA:
            return [_t("En esta conversación ya van muchos mensajes. Escríbeme mañana y seguimos.")], s
        # 2. Gates del canal WhatsApp: allowlist, por hora, por día y global.
        #    `wa_sumar` solo se llama si el mensaje pasa.
        bloqueo = _limite_wa(s, datos)
        if bloqueo is not None:
            return bloqueo, s
    else:
        if s.mensajes > config.MAX_MENSAJES_POR_SESION:
            # Un visitante que explora FAQ + agendar + cancelar ronda los 25 mensajes.
            return [_t("Esta sesión de demo llegó a su tope de mensajes. Recarga la página para "
                       f"empezar otra, o escríbeme directo: {datos['telefono']}.")], s
        # 3. Recién ahora el presupuesto global: los bloqueados no lo consumen.
        if USO_DIA["fecha"] != ahora.date():
            USO_DIA.update(fecha=ahora.date(), n=0)
        USO_DIA["n"] += 1
        if USO_DIA["n"] > config.MAX_MENSAJES_POR_DIA:
            return [_t("La demo llegó a su límite de mensajes por hoy. Escríbeme directo y seguimos: "
                       f"{datos['telefono']}.")], s

    # ¿Ya estaba en manos de la manicurista antes de este mensaje? Si sí, un handoff que
    # sigue activo es el mismo episodio y no se avisa otra vez; si no, es una petición nueva.
    ya_en_humano = s.handoff and s.paso == "humano"
    if media is not None:
        # Foto, captura o PDF: el comprobante del anticipo. No depende del paso de la sesión
        # (que puede haberse reiniciado): se busca el apartado de esa clienta por su teléfono.
        respuestas, s = await _recibir_media(s, media, datos)
        texto = f"[{media.get('tipo', 'archivo')} enviado]"
    else:
        respuestas, s = await _flujo(s, texto, txt, datos)
    await _avisar_handoff(s, texto, ya_en_humano)
    return respuestas, s


async def _avisar_handoff(s: Sesion, texto: str, ya_en_humano: bool = False) -> None:
    """Registra el handoff y avisa al dueño UNA vez por episodio, venga de donde venga.

    Un episodio termina cuando el cliente vuelve al bot (`paso` deja de ser `humano`). Antes
    el aviso dependía de `handoff_avisado`, que solo se bajaba con `menu`/`hola`: si el
    cliente seguía con el bot por otro camino y pedía una persona de nuevo, el bot le decía
    "ya le pasé tu conversación" sin avisar a nadie.

    Antes solo avisaba la rama de "humano"/"persona". Pasar a la manicurista por una
    cancelación fuera de ventana, por falta de huecos o por la opción 5 le decía al
    cliente "ya le pasé tu conversación" sin avisarle a nadie; y repetir "humano"
    mandaba un Telegram por cada mensaje.
    """
    if not s.handoff:
        s.handoff_avisado = False      # volvió al bot: el siguiente handoff avisa de nuevo
        return
    if ya_en_humano and s.handoff_avisado:
        return                         # mismo episodio: ya se avisó
    s.handoff_avisado = True
    detalle, s.aviso_extra = s.aviso_extra or texto, ""
    store.registrar_handoff(s.sid, detalle)
    # La red va en otro hilo: Telegram no debe bloquear el event loop.
    await asyncio.to_thread(avisos.notificar_handoff, s.sid, s.telefono, detalle)


async def _flujo(
    s: Sesion, texto: str, txt: str, datos: dict[str, Any]
) -> tuple[list[dict[str, Any]], Sesion]:
    """Salidas globales y pasos de la conversación, ya pasados los topes."""
    # salidas globales
    if txt in {"menu", "inicio", "hola", "buenas", "buenos dias", "buenas tardes", "hey", "que tal"}:
        _liberar_apartado(s)
        s.paso = "inicio"
        s.handoff = False
        return bienvenida(datos), s

    if _es_handoff(txt):
        s.handoff = True
        s.paso = "humano"
        # El registro del KPI y el aviso al dueño los hace `manejar` (_avisar_handoff),
        # una sola vez por handoff, sin importar por qué camino se llegó aquí.
        return [_t("Listo, le paso tu conversación a la manicurista y te contesta en cuanto termine con su clienta. "
                   f"Si urge, marca al {datos['telefono']}."),
                _b("¿Algo más mientras?", MENU)], s

    # Silencio del bot mientras la conversación está en manos de la manicurista.
    # Va después de la salida global de "menu" (que está arriba) para que el
    # cliente siempre pueda recuperar al bot.
    if s.canal == "wa" and s.paso == "humano" and txt != "menu":
        return [], s

    # atajos con folio: "cancelar AMB-7K4Q", "mover AMB-7K4Q"
    folio_dicho = _folio_de(texto)
    if folio_dicho and _es_cancelar(txt):
        return await _cancelar_folio(s, folio_dicho, datos)
    if folio_dicho and _es_reagendar(txt):
        return _empezar_reagendar(s, folio_dicho, datos)
    if folio_dicho:
        # Solo el folio, sin verbo: mostrar la cita y sus opciones.
        return _paso_citas_folio(s, folio_dicho, datos)

    if s.paso in {"fin", "humano"}:
        if s.paso == "humano":
            s.handoff = False          # el cliente siguió con el bot: termina el episodio
        s.paso = "inicio"
        return await _paso_inicio(s, txt, texto, datos)

    if s.paso == "anticipo_aceptar":
        return await _paso_anticipo_aceptar(s, txt, datos)
    if s.paso == "comprobante":
        return await _paso_comprobante(s, txt, datos)
    if s.paso == "cancelar_confirmar":
        return await _paso_cancelar_confirmar(s, txt, datos)
    if s.paso == "inicio":
        return await _paso_inicio(s, txt, texto, datos)
    if s.paso == "agendar_servicio":
        return _paso_servicio(s, txt, datos)
    if s.paso == "agendar_agenda":
        return _paso_agenda(s, txt, texto, datos)
    if s.paso == "agendar_dia":
        return await _paso_dia(s, txt, texto, datos)
    if s.paso == "agendar_slot":
        return await _paso_slot(s, txt, texto, datos)
    if s.paso == "agendar_nombre":
        return await _paso_nombre(s, texto, datos)
    if s.paso == "citas_folio":
        return _paso_citas_folio(s, texto, datos)
    if s.paso == "citas_accion":
        return await _paso_citas_accion(s, txt, texto, datos)
    if s.paso == "citas_slot":
        return await _paso_citas_slot(s, txt, texto, datos)

    s.paso = "inicio"
    return bienvenida(datos), s


async def _paso_inicio(s: Sesion, txt: str, texto: str, datos: dict[str, Any]):
    if txt in {"agendar", "1", "cita", "agenda", "reservar", "quiero cita"}:
        return _ofrecer_servicios(s, datos)
    if txt in {"citas", "2", "mi cita", "cancelar", "reagendar", "mover"}:
        s.paso = "citas_folio"
        return [_t("Claro. ¿Me pasas el folio de tu cita? Viene en la confirmación, tipo AMB-7K4Q.")], s
    if txt in {"precios", "3", "servicios", "cuanto"}:
        s.resuelta = True
        lineas = "\n".join(f"• {x['nombre']} — ${x['precio']} ({x['minutos']} min)" for x in datos["servicios"])
        return [_t(f"Estos son los servicios:\n{lineas}"), _b("¿Agendamos?", [("agendar", "Agendar cita")] + MENU[1:])], s
    if txt in {"ubicacion", "4", "horario", "donde"}:
        s.resuelta = True
        h = datos["horario"]
        return [_t(f"{datos['nombre']}\n{datos['direccion']}\n\n"
                   f"L-V {h['lunes_viernes']['abre']}–{h['lunes_viernes']['cierra']} · "
                   f"Sáb {h['sabado']['abre']}–{h['sabado']['cierra']} · Domingo cerrado"),
                _b("¿Agendamos tu cita?", [("agendar", "Sí, agendar")] + MENU[1:])], s
    if txt in {"5"}:
        s.handoff = True
        s.paso = "humano"
        return [_t(f"Listo, le paso tu conversación a la manicurista. Si urge, marca al {datos['telefono']}."),
                _b("¿Algo más mientras?", MENU)], s

    # texto libre: primero intenta cerrar la agenda sin depender del LLM
    palabras = _tokens(texto)
    es_info = bool(palabras & _PALABRAS_INFO) or bool(palabras & _PALABRAS_HORARIO)
    sid = None if es_info else _detectar_servicio(texto, datos)
    dia, hora = _detectar_dia_hora(texto)

    if sid:
        return _agendar_directo(s, sid, dia, hora, datos)
    if not es_info and (dia is not None or hora is not None):
        # habló de día/hora pero no dijo servicio: pregunta solo eso
        s.sin_avance = 0
        if dia is not None:
            s.dia_sugerido = dia      # no se pierde: al elegir servicio se usa
        return _ofrecer_servicios(s, datos)
    if not es_info and s.sin_avance == 0 and (txt in _CORTESIA or (palabras and palabras <= _NO_NOMBRE)):
        # "sí", "gracias", "ok": cortesía, no hace falta el LLM
        s.sin_avance = 0
        return [_t("¡Con gusto! Aquí sigo cuando quieras."),
                _b("¿Algo más?", MENU)], s
    if s.sin_avance >= 1 and not es_info:
        # Ya contestó con prosa y el cliente sigue sin avanzar: corta el bucle.
        s.sin_avance = 0
        s.paso = "agendar_servicio"
        opts = [(x["id"], f"{x['nombre']} · ${x['precio']}") for x in datos["servicios"]]
        return [_t("Para no dar vueltas: elige un servicio y te paso los horarios."),
                _b("¿Qué servicio te haces?", opts)], s

    if not es_info:
        s.sin_avance += 1          # esta respuesta no movió el paso
    r = await llm.responder(texto, datos)
    if r["fuente"] == "fallback":
        return [_t(r["texto"]), _b("¿Qué prefieres?", MENU)], s
    s.resuelta = True
    # MENU ya abre con "Agendar cita": anteponerlo dejaba dos filas con el mismo id.
    return [_t(r["texto"]), _b("¿Algo más?", MENU)], s


_CORTESIA = {
    "si", "sale", "ok", "okay", "va", "dale", "claro", "perfecto", "gracias",
    "muchas gracias", "listo", "bueno", "buenas", "nada", "nada mas",
}


_NO_NOMBRE = {
    "si", "no", "ok", "okay", "va", "dale", "claro", "perfecto", "gracias",
    "esa", "ese", "eso", "la", "el", "las", "los", "de", "del", "por", "favor",
    "mejor", "quiero", "agenda", "agendame", "aparta", "apartame", "cita",
    "hora", "horario", "manana", "hoy", "bueno", "buenas", "hola",
}


def _limpiar_nombre(texto: str, datos: dict[str, Any]) -> str:
    """'Brenda Pérez, manicura, 6 pm' → 'Brenda Pérez'. '' si no parece un nombre."""
    partes = [p.strip() for p in re.split(r"[,\n;]", texto) if p.strip()]
    nombre = partes[0] if len(partes) > 1 and _detectar_servicio(texto, datos) else texto
    nombre = nombre.strip()[:60]
    if _detectar_dia_hora(nombre)[0] is not None or _hora_hhmm(nombre):
        return ""                                  # "sí, la de las 6" no es nombre
    if not [w for w in _tokens(nombre) if w not in _NO_NOMBRE]:
        return ""
    return nombre


def _agendar_directo(
    s: Sesion, sid: str, dia: int | None, hora: str | None, datos: dict[str, Any]
):
    """Arranca la cita desde una frase libre, saltando preguntas ya contestadas."""
    s.sin_avance = 0
    s.servicio_id = sid
    s.dia_fecha = None
    s.pagina_horas = 0
    activas = _agendas_activas()
    if not s.agenda_id and len(activas) > 1:
        s.dia_sugerido = dia if dia is not None else s.dia_sugerido
        s.hora_sugerida = hora            # no se pierde lo que ya pidió
        sv = _servicio(datos, sid) or {"nombre": sid, "precio": 0, "minutos": 30}
        return _menu_agendas(s, datos, sid, f"{sv['nombre']}. ¿Con quién prefieres?")
    if not s.agenda_id and activas:
        a = activas[0]
        s.agenda_id, s.agenda_nombre = str(a["clave"]), str(a.get("nombre") or "")
    s.paso = "agendar_slot"
    libres = slots_disponibles(datos, sid, agenda_id=s.agenda_id)
    if not libres:
        s.paso = "inicio"
        s.handoff = True
        return [_t("No me quedan huecos en los próximos 7 días. Te paso con la manicurista para ver si abre un espacio.")], s
    sv = _servicio(datos, sid) or {"nombre": sid, "precio": 0, "minutos": 30}
    if dia is not None:
        s.dia_sugerido = dia
        del_dia = _huecos_dia(datos, sid, dia, agenda_id=s.agenda_id)
        if del_dia:
            libres = del_dia
    elegido = _preferir_slot(libres, dia, hora) if hora is not None else None
    if elegido:
        s.slot = elegido
        s.paso = "agendar_nombre"
        return [_t(f"{sv['nombre']} · {_etiqueta_slot(elegido)}. ¿A nombre de quién queda?")], s
    if dia is not None or hora is not None:
        pedido = " ".join(
            x for x in (DIAS[dia] if dia is not None else "",
                        f"a las {_hora_legible(hora)}" if hora else "") if x
        )
        cabecera = f"No tengo {pedido}. Lo más cercano:"
        opciones = _cercanos(libres, dia, hora)
        if dia is not None and hora is None and any(
            datetime.fromisoformat(i).weekday() == dia for i in libres
        ):
            primero = next(i for i in libres if datetime.fromisoformat(i).weekday() == dia)
            return _menu_horas(s, datos, sid, primero[:10], f"El {DIAS[dia]} tengo:")
    else:
        d = s.dia_sugerido
        del_dia = _huecos_dia(datos, sid, d, agenda_id=s.agenda_id) if d is not None else []
        if del_dia and d is not None:
            return _menu_horas(
                s, datos, sid, del_dia[0][:10],
                f"{sv['nombre']} (${sv['precio']}, {sv['minutos']} min). El {DIAS[d]} tengo:",
            )
        return _menu_dias(
            s, datos, sid,
            f"{sv['nombre']} (${sv['precio']}, {sv['minutos']} min). ¿Qué día te queda bien?",
        )
    return [_b(cabecera,
               [(i, _etiqueta_slot(i)) for i in opciones]
               + [_OTRA_FECHA])], s


def _opciones_agendas(activas: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Una fila por agenda —su clave corta, nunca el ID del calendario— más "Cualquiera"."""
    if len(activas) > MAX_AGENDAS_MENU:
        log.warning(
            "multiagenda: %d agendas activas; el menú muestra las %d primeras por orden",
            len(activas), MAX_AGENDAS_MENU,
        )
    filas = [(str(a["clave"]), str(a["nombre"])) for a in activas[:MAX_AGENDAS_MENU]]
    return filas + [(CUALQUIERA, store.NOMBRE_RESERVADO)]


def _menu_agendas(s: Sesion, datos: dict[str, Any], sid: str, cabecera: str):
    """Pregunta con quién: las agendas activas y "cualquiera"."""
    s.paso = "agendar_agenda"
    s.dia_fecha = None
    s.pagina_horas = 0
    return [_b(cabecera, _opciones_agendas(_agendas_activas()))], s


def _paso_agenda(s: Sesion, txt: str, texto: str, datos: dict[str, Any]):
    """Elige con quién: por botón/fila, por nombre, por número o "cualquiera"."""
    sid = s.servicio_id or datos["servicios"][0]["id"]
    activas = _agendas_activas()
    sv = _servicio(datos, sid) or {"nombre": sid, "precio": 0, "minutos": 30}
    t = texto.strip().lower()
    elegida: str | None = None
    if not activas:
        return _seguir_agenda(s, datos, sid)
    if txt in {"cualquiera", "*", "quien sea", "la que sea", "me da igual", "cualquiera disponible"}:
        elegida = CUALQUIERA
    elif txt in {str(a["clave"]) for a in activas}:
        elegida = txt
    else:
        elegida = next((str(a["clave"]) for a in activas if _norm(str(a["nombre"])) == txt), None)
        if elegida is None and txt.isdigit() and 1 <= int(txt) <= min(len(activas), MAX_AGENDAS_MENU):
            elegida = str(activas[int(txt) - 1]["clave"])   # el número de la fila que ve
    if elegida is None:
        return [_b(f"{sv['nombre']}. ¿Con quién prefieres?", _opciones_agendas(activas))], s

    if elegida == CUALQUIERA:
        s.agenda_id, s.agenda_nombre = CUALQUIERA, ""
    else:
        a = store.agenda_por_clave(elegida) or {}
        s.agenda_id, s.agenda_nombre = elegida, str(a.get("nombre") or "")
    return _seguir_agenda(s, datos, sid)


def _seguir_agenda(s: Sesion, datos: dict[str, Any], sid: str):
    """Ya se sabe con quién: si había pedido una hora, se va directo a ese horario."""
    hora, dia = s.hora_sugerida, s.dia_sugerido
    s.hora_sugerida = None
    if hora:
        return _agendar_directo(s, sid, dia, hora, datos)
    return _menu_dia_tras_servicio(s, datos, sid)



def _ofrecer_servicios(s: Sesion, datos: dict[str, Any]):
    s.paso = "agendar_servicio"
    s.agenda_id, s.agenda_nombre = "", ""    # cita nueva: se vuelve a preguntar con quién
    s.hora_sugerida = None
    s.dia_fecha = None
    s.pagina_horas = 0
    opts = [(x["id"], f"{x['nombre']} · ${x['precio']}") for x in datos["servicios"]]
    return [_b("¿Qué servicio te haces?", opts)], s


def _paso_servicio(s: Sesion, txt: str, datos: dict[str, Any]):
    sid = None
    for x in datos["servicios"]:
        if txt == x["id"] or _norm(x["nombre"]) == txt or txt in _norm(x["nombre"]):
            sid = x["id"]
            break
    if txt.isdigit() and 1 <= int(txt) <= len(datos["servicios"]):
        sid = datos["servicios"][int(txt) - 1]["id"]
    if not sid:
        # frase libre: "manicura y pedicura el miércoles a las 6"
        sid = _detectar_servicio(txt, datos)
        if sid:
            dia, hora = _detectar_dia_hora(txt)
            return _agendar_directo(s, sid, dia, hora, datos)
        return _ofrecer_servicios(s, datos)

    s.servicio_id = sid
    s.dia_fecha = None
    s.pagina_horas = 0
    sv = _servicio(datos, sid) or {"nombre": sid, "precio": 0, "minutos": 30}
    activas = _agendas_activas()
    if not s.agenda_id and len(activas) > 1:
        return _menu_agendas(
            s, datos, sid,
            f"{sv['nombre']} (${sv['precio']}, {sv['minutos']} min).\n¿Con quién prefieres?",
        )
    if not s.agenda_id and activas:
        a = activas[0]                      # una sola agenda activa: no hay nada que preguntar
        s.agenda_id, s.agenda_nombre = str(a["clave"]), str(a.get("nombre") or "")
    return _menu_dia_tras_servicio(s, datos, sid)


def _menu_dia_tras_servicio(s: Sesion, datos: dict[str, Any], sid: str):
    """Con el servicio y la agenda ya decididos: pide el día (o el que ya había dicho)."""
    s.paso = "agendar_slot"
    libres = slots_disponibles(datos, sid, agenda_id=s.agenda_id)
    if not libres:
        s.paso = "inicio"
        s.handoff = True
        return [_t("No me quedan huecos en los próximos 7 días. Te paso con la manicurista para ver si abre un espacio.")], s
    sv = _servicio(datos, sid) or {"nombre": sid, "precio": 0, "minutos": 30}
    d = s.dia_sugerido
    del_dia = _huecos_dia(datos, sid, d, agenda_id=s.agenda_id) if d is not None else []
    if del_dia and d is not None:
        # Ya había dicho el día antes de elegir el servicio: se salta ese paso.
        return _menu_horas(
            s, datos, sid, del_dia[0][:10],
            f"{sv['nombre']} (${sv['precio']}, {sv['minutos']} min). El {DIAS[d]} tengo:",
        )
    return _menu_dias(
        s, datos, sid,
        f"{sv['nombre']} (${sv['precio']}, {sv['minutos']} min).\n¿Qué día te queda bien?",
    )


def _slot_exacto(libres: list[str], texto: str) -> str | None:
    """El id ISO de un horario libre, o su etiqueta escrita ('Miércoles 11 nov · 17:00')."""
    t = texto.strip()
    if t in libres:
        return t
    return next((i for i in libres if _norm(t) == _norm(_etiqueta_slot(i))), None)


def _nombre_en_texto(texto: str, datos: dict[str, Any]) -> str | None:
    """¿En vez de contestar dio su nombre? Se guarda y se sigue pidiendo lo que falta."""
    posible = _limpiar_nombre(texto, datos)
    if posible and len(posible.split()) <= 3 and all(w.isalpha() for w in posible.split()):
        return posible
    return None


async def _paso_dia(s: Sesion, txt: str, texto: str, datos: dict[str, Any]):
    """Paso 1 de la cita: elegir el día. Acepta el botón, un número, la etiqueta o el nombre
    del día; si ya trae día y hora, o solo la hora, lo resuelve el paso del horario."""
    sid = s.servicio_id or datos["servicios"][0]["id"]
    libres = slots_disponibles(datos, sid, agenda_id=s.agenda_id)
    if not libres:
        s.paso = "inicio"
        s.handoff = True
        return [_t("No me quedan huecos en los próximos 7 días. Te paso con la manicurista para ver si abre un espacio.")], s

    # un horario exacto (la API, o alguien que ya lo conoce) salta los dos pasos
    elegido = _slot_exacto(libres, texto)
    if elegido:
        return await _fijar_slot(s, elegido, datos)

    fechas = _fechas_con_huecos(libres)
    t = texto.strip()
    fecha = t if t in fechas else None
    if fecha is None and txt.isdigit() and 1 <= int(txt) <= len(fechas):
        fecha = fechas[int(txt) - 1]                    # el número es del día que ve en pantalla
    if fecha is None:
        fecha = next((f for f in fechas if _norm(_etiqueta_dia(f)) == txt), None)

    dia, hora = _detectar_dia_hora(texto)
    if fecha is None and dia is not None and hora is None:
        fecha = next((f for f in fechas if date.fromisoformat(f).weekday() == dia), None)
        if fecha is None:
            return _menu_dias(s, datos, sid, f"El {DIAS[dia]} no tengo horarios. ¿Qué otro día te queda bien?")
    if fecha is not None:
        s.pagina_horas = 0
        return _menu_horas(s, datos, sid, fecha, f"{_nombre_dia(fecha)}. ¿A qué hora te queda bien?")

    if dia is not None or hora is not None:             # "el viernes a las 5" / "a las 5"
        s.paso = "agendar_slot"
        return await _paso_slot(s, txt, texto, datos)

    otro = _detectar_servicio(texto, datos)             # cambió de idea sobre el servicio
    if otro and otro != sid:
        return _agendar_directo(s, otro, None, None, datos)

    posible = _nombre_en_texto(texto, datos)
    if posible:
        s.nombre = posible
        return _menu_dias(s, datos, sid, f"Va, {posible}. Nada más elige el día:")
    return _menu_dias(s, datos, sid, "¿Qué día te queda bien?")


async def _paso_slot(s: Sesion, txt: str, texto: str, datos: dict[str, Any]):
    """Paso 2 de la cita: elegir el horario del día ya escogido (o de lo que pidió por texto)."""
    sid = s.servicio_id or datos["servicios"][0]["id"]
    libres = slots_disponibles(datos, sid, agenda_id=s.agenda_id)
    t = texto.strip()
    if s.dia_fecha and not any(i[:10] == s.dia_fecha for i in libres):
        s.dia_fecha = None                              # ese día se llenó mientras tanto
    if s.dia_fecha and t == "mas_horarios":
        s.pagina_horas += 1
        return _menu_horas(s, datos, sid, s.dia_fecha, f"{_nombre_dia(s.dia_fecha)}. Más horarios:")
    if t == "otro_dia" and libres:
        return _menu_dias(s, datos, sid, "¿Qué día te queda bien?")

    dia, hora = _detectar_dia_hora(texto)
    if dia is not None:
        s.dia_sugerido = dia
        if hora is None:
            # "mejor el sábado": cambia de día y enseña los horarios de ese día
            primero = next((i for i in libres if datetime.fromisoformat(i).weekday() == dia), None)
            if primero:
                s.pagina_horas = 0
                return _menu_horas(s, datos, sid, primero[:10], f"El {DIAS[dia]} tengo:")
        s.dia_fecha = None

    # lo que el cliente ve es lo que puede escribir como número
    por_fecha = bool(s.dia_fecha) and dia is None
    if por_fecha:
        del_dia = [i for i in libres if i[:10] == s.dia_fecha]
        base, dia_pedido = del_dia, None
        p = s.pagina_horas
        visibles = (
            del_dia[p * HORAS_POR_PAGINA:(p + 1) * HORAS_POR_PAGINA]
            if hora is None else _cercanos(base, None, hora)
        )
    else:
        dia_pedido = dia if dia is not None else s.dia_sugerido
        del_dia = _huecos_dia(datos, sid, dia_pedido, agenda_id=s.agenda_id) if dia_pedido is not None else []
        base = del_dia or libres
        if del_dia and hora is None:
            visibles = base[:8]
        elif dia is not None or hora is not None or (del_dia and dia_pedido is not None):
            visibles = _cercanos(base, dia_pedido, hora)
        else:
            visibles = []                               # sin día de referencia: se pregunta el día

    def opciones() -> list[tuple[str, str]]:
        if por_fecha and hora is None:
            return _opciones_horas(s, del_dia)
        return [(i, _etiqueta_slot(i)) for i in visibles] + [_OTRA_FECHA]

    elegido = _elegir_slot(visibles, txt, texto)
    if elegido is None:
        # Un botón o la API mandan el id exacto (o la etiqueta escrita) de cualquier hueco
        # libre, aunque no esté en pantalla o sea de otro día (fila vieja de un menú). El
        # número escrito sigue mapeando solo a lo visible.
        elegido = _slot_exacto(libres, texto)
    if elegido is None and hora is not None:
        elegido = _preferir_slot(base, dia_pedido, hora)
    if elegido is not None:
        return await _fijar_slot(s, elegido, datos)

    if not libres:
        return _paso_servicio(s, sid, datos)
    # preguntó precio en vez de un horario: contesta y deja el menú
    if any(k in txt for k in ("cuanto", "cuesta", "precio", "vale", "cobran")):
        sv = _servicio(datos, sid) or {"nombre": sid, "precio": 0, "minutos": 30}
        precio = f"{sv['nombre']} cuesta ${sv['precio']} ({sv['minutos']} min). "
        if not por_fecha and not visibles:
            return _menu_dias(s, datos, sid, precio + "¿Qué día te queda bien?")
        return [_b(precio + "¿Qué horario te aparto?", opciones())], s
    # nombró otro servicio en vez de un horario: cambia y vuelve a ofrecer
    otro = _detectar_servicio(texto, datos)
    if otro and otro != sid:
        return _agendar_directo(s, otro, dia, hora, datos)

    if dia is not None and hora is None:
        aviso = f"El {DIAS[dia]} tengo:"
    elif dia is not None or hora is not None:
        pedido = " ".join(
            x for x in (DIAS[dia] if dia is not None else "",
                        f"a las {_hora_legible(hora)}" if hora else "") if x
        )
        aviso = f"No tengo {pedido}. Lo más cercano:"
    elif del_dia and dia_pedido is not None:
        aviso = f"El {DIAS[dia_pedido]} tengo:"
    else:
        aviso = "Elige el horario que te acomode:"
    # ¿en vez de un horario dio su nombre? Guárdalo y pide solo lo que falta
    posible = _nombre_en_texto(texto, datos)
    if posible:
        s.nombre = posible
    if not por_fecha and not visibles:
        return _menu_dias(
            s, datos, sid,
            f"Va, {posible}. Nada más elige el día:" if posible else "¿Qué día te queda bien?",
        )
    if posible:
        aviso = f"Va, {posible}. Nada más elige el horario:"
    return [_b(aviso, opciones())], s


async def _paso_nombre(s: Sesion, texto: str, datos: dict[str, Any]):
    nombre = _limpiar_nombre(texto, datos)
    if len(nombre) < 2:
        return [_t("Dame un nombre para la cita, por favor.")], s
    sid = s.servicio_id or ""
    slot = s.slot or ""

    ok, motivo = _validar_slot(datos, sid, slot, s.agenda_id)
    if not ok:
        s.pagina_horas = 0
        return _menu_horas(s, datos, sid, slot[:10], f"Ese horario se acaba de ocupar ({motivo}). Elige otro:")

    if anticipo.activo(datos):
        return _pedir_aceptacion(s, nombre, datos)

    try:
        # En WhatsApp la cita queda ligada al teléfono: sin esto, el candado de folio
        # (`_folio_autorizado`) no deja al propio cliente cancelar ni mover su cita.
        cita = _crear_cita(s, datos, sid, slot, nombre)
    except store.SlotOcupado:
        s.pagina_horas = 0
        return _menu_horas(s, datos, sid, slot[:10], "Justo alguien más tomó ese horario. Elige otro:")

    event_id = await asyncio.to_thread(
        calendar_sync.crear, cita, datos,
        calendar_id=_calendario(cita.get("agenda_id") or ""),
    )
    if event_id:
        store.set_evento(cita["folio"], event_id)

    s.nombre = nombre
    s.folio = cita["folio"]
    s.paso = "fin"
    s.dia_sugerido = None          # el día pedido era para esta cita; no arrastrarlo
    s.dia_fecha = None
    s.pagina_horas = 0
    s.resuelta = True
    sv = _servicio(datos, sid) or {"nombre": sid, "precio": 0}
    horas = datos["reglas_agenda"]["cancelacion_libre_hasta_horas"]
    return [_t(f"Listo, {nombre}. Quedas {_etiqueta_slot(cita['slot'])}{_con_quien(cita)} "
               f"para {sv['nombre']} (${sv['precio']}). Llega 5 min antes.\n\n"
               f"Tu folio es {cita['folio']}. Puedes cancelar o mover sin costo hasta {horas} h antes.\n"
               f"Te esperamos en {datos['direccion']}."),
            _b("¿Algo más?", MENU)], s


# ---------- anticipo ----------
ACEPTA_ANTICIPO = {"acepto_anticipo", "acepto", "si", "ok", "de acuerdo", "va", "dale", "claro",
                   "estoy de acuerdo", "acepto el anticipo", "sii"}
RECHAZA_ANTICIPO = {"rechazo_anticipo", "no", "mejor no", "no gracias", "no acepto", "nel", "nop"}
SI_CANCELAR = {"cancelar_si", "si", "sii", "confirmo", "confirmar", "si cancelar", "si, cancelar"}
NO_CANCELAR = {"cancelar_no", "no", "nop", "conservar", "menu", "conservar mi cita", "nel"}


def _pedir_aceptacion(s: Sesion, nombre: str, datos: dict[str, Any]):
    """Antes de dar la cuenta: la clienta ve las condiciones y las acepta (queda registrado)."""
    s.nombre = nombre
    if s.canal != "wa":
        s.paso = "inicio"
        return [_t("Para apartar tu cita se pide un anticipo con comprobante, y eso se hace por WhatsApp. "
                   f"Escríbenos al {datos['telefono']} y la agendamos ahí."),
                _b("¿Algo más?", MENU)], s
    sv = _servicio(datos, s.servicio_id or "") or {"nombre": s.servicio_id, "precio": 0}
    s.paso = "anticipo_aceptar"
    return [_t(f"Perfecto, {nombre}. Tu cita sería {_etiqueta_slot(s.slot or '')} para {sv['nombre']} (${sv['precio']})."),
            _b(anticipo.texto_aviso(anticipo.cfg(datos), sv["precio"]),
               [("acepto_anticipo", "Acepto"), ("rechazo_anticipo", "Mejor no")])], s


def _liberar_apartado(s: Sesion) -> None:
    """Si la clienta se va del pago, el horario apartado vuelve a estar libre."""
    if s.paso != "comprobante" or not s.folio:
        return
    cita = store.obtener(s.folio)
    if cita and cita["estado"] == "pendiente":
        store.cancelar(cita["folio"])
    s.folio = None


async def _paso_anticipo_aceptar(s: Sesion, txt: str, datos: dict[str, Any]):
    sv = _servicio(datos, s.servicio_id or "") or {"nombre": s.servicio_id, "precio": 0}
    c = anticipo.cfg(datos)
    if txt in RECHAZA_ANTICIPO:
        s.paso = "inicio"
        return [_t("Sin problema. Cuando quieras apartar tu cita, aquí estoy."), _b("¿Algo más?", MENU)], s
    if txt not in ACEPTA_ANTICIPO:
        return [_b(anticipo.texto_aviso(c, sv["precio"]),
                   [("acepto_anticipo", "Acepto"), ("rechazo_anticipo", "Mejor no")])], s

    problemas = anticipo.problemas_de_pago()
    if problemas:
        log.error("anticipo: datos de pago incompletos (%s)", "; ".join(problemas))
        s.handoff, s.paso = True, "humano"
        s.aviso_extra = "Una clienta quiso apartar con anticipo pero los datos de pago no están configurados."
        return [_t("Te paso con la manicurista para darte los datos del anticipo y apartar tu horario.")], s

    sid, slot, nombre = s.servicio_id or "", s.slot or "", s.nombre
    # Una clienta, un apartado: el anterior se libera (si no, ella misma se taparía el horario).
    previo = store.apartado_reciente(s.telefono, gracia_horas=0)
    if previo and previo["estado"] == "pendiente":
        store.cancelar(previo["folio"])
    ok, motivo = _validar_slot(datos, sid, slot, s.agenda_id)
    if not ok:
        s.pagina_horas = 0
        return _menu_horas(s, datos, sid, slot[:10], f"Ese horario se acaba de ocupar ({motivo}). Elige otro:")

    ahora = reloj.ahora()
    ahora_iso = ahora.isoformat(timespec="seconds")
    minutos = anticipo.apartado_minutos(c, slot, ahora)
    vence = (ahora + timedelta(minutes=minutos)).isoformat(timespec="seconds")
    try:
        cita = _crear_cita(s, datos, sid, slot, nombre, pendiente_hasta=vence,
                           anticipo=anticipo.monto_para(c, sv["precio"]), aceptado_en=ahora_iso,
                           ahora=ahora_iso)
    except store.SlotOcupado:
        s.pagina_horas = 0
        return _menu_horas(s, datos, sid, slot[:10], "Justo alguien más tomó ese horario. Elige otro:")

    s.folio = cita["folio"]
    s.paso = "comprobante"
    s.dia_sugerido = None
    s.dia_fecha = None
    s.pagina_horas = 0
    return [_t(anticipo.texto_pago(c, cita["folio"], minutos, config.ANTICIPO_BANCO,
                                   config.ANTICIPO_CLABE, config.ANTICIPO_TITULAR, sv["precio"]))], s


async def _paso_comprobante(s: Sesion, txt: str, datos: dict[str, Any]):
    """Ya dio la cuenta: aquí solo se espera el archivo (llega por `manejar(media=...)`)."""
    cita = store.obtener(s.folio or "")
    if cita is None or cita["estado"] != "pendiente":
        s.paso, s.folio = "inicio", None
        return [_t("Ese apartado ya no está activo."), _b("¿Qué hacemos?", MENU)], s
    ahora_iso = reloj.ahora().isoformat(timespec="seconds")
    if cita["vence"] <= ahora_iso:
        store.expirar_vencidas(ahora_iso)
        s.paso, s.folio = "inicio", None
        return [_t("Se acabó el tiempo del apartado y liberé el horario. Si ya hiciste la transferencia, "
                   "mándame aquí tu comprobante y la manicurista te ayuda; o elige otro horario."),
                _b("¿Qué hacemos?", MENU)], s
    if _es_cancelar(txt) or txt in {"ya no", "no quiero", "olvidalo"}:
        store.cancelar(cita["folio"])
        s.paso, s.folio = "inicio", None
        return [_t("Listo, liberé el horario. Cuando quieras apartar otro, aquí estoy."), _b("¿Algo más?", MENU)], s
    if txt in {"agendar", "1", "cita", "agenda", "reservar", "quiero cita"}:
        # Quiere otro horario: se libera el apartado sin pagar y se empieza de nuevo.
        store.cancelar(cita["folio"])
        s.paso, s.folio = "inicio", None
        return _ofrecer_servicios(s, datos)
    hasta = datetime.fromisoformat(cita["vence"]).strftime("%H:%M")
    return [_t("Estoy esperando tu comprobante (foto, captura o PDF) para agendar tu cita. "
               f"Tienes hasta las {hasta}. Si ya no quieres apartarla, escribe *cancelar*.")], s


def _comprobante_sin_horario(s: Sesion, cita: dict[str, Any], datos: dict[str, Any]):
    """Llegó el pago pero el horario ya no es de ella: lo resuelve la manicurista, no el bot."""
    s.handoff, s.paso, s.handoff_avisado = True, "humano", False
    s.aviso_extra = (f"Llegó un comprobante de anticipo (folio {cita['folio']}, {cita['nombre']}, "
                     f"{_etiqueta_slot(cita['slot'])}) pero el apartado venció y el horario ya no está "
                     "disponible. Revisa el pago y contacta a la clienta.")
    return [_t("Recibí tu comprobante, pero el tiempo del apartado venció y ese horario ya no está disponible. "
               "Ya le avisé a la manicurista para que revise tu pago y te contacte.")], s


async def _recibir_media(s: Sesion, media: dict[str, str], datos: dict[str, Any]):
    if s.canal != "wa" or not anticipo.activo(datos):
        return [_t("Por ahora solo entiendo mensajes de texto. Escribe *menú* para ver las opciones.")], s
    tipo, mime = media.get("tipo", ""), (media.get("mime") or "").lower()
    if tipo not in {"image", "document"} or (tipo == "document" and not (
            mime == "application/pdf" or mime.startswith("image/"))):
        return [_t("Para el anticipo necesito una foto, captura o PDF del comprobante. ¿Me lo mandas así?")], s

    ahora = reloj.ahora()
    ahora_iso = ahora.isoformat(timespec="seconds")
    cita = store.apartado_reciente(s.telefono, ahora=ahora_iso)
    if cita is None:
        s.handoff, s.paso, s.handoff_avisado = True, "humano", False
        s.aviso_extra = "Envió un archivo (posible comprobante) pero no tiene un apartado de anticipo pendiente."
        return [_t("Recibí tu archivo, pero no tengo una cita pendiente de anticipo a tu nombre. "
                   "Ya le pasé tu conversación a la manicurista para que lo revise.")], s

    tardio = not (cita["estado"] == "pendiente" and cita["vence"] > ahora_iso)
    if tardio:
        ok, _motivo = horarios.validar(datos, cita["servicio_id"], cita["slot"], _ocupadas(cita.get("agenda_id") or ""), ahora)
        if not ok:
            return _comprobante_sin_horario(s, cita, datos)
    try:
        cita = store.registrar_comprobante(cita["folio"], tipo, media.get("id", ""), ahora_iso)
    except (store.SlotOcupado, store.EstadoInvalido):
        return _comprobante_sin_horario(s, cita, datos)

    event_id = await asyncio.to_thread(
        calendar_sync.crear, cita, datos,
        calendar_id=_calendario(cita.get("agenda_id") or ""),
    )
    if event_id:
        store.set_evento(cita["folio"], event_id)

    c = anticipo.cfg(datos)
    sv = _servicio(datos, cita["servicio_id"]) or {"nombre": cita["servicio_id"], "precio": 0}
    etiqueta = _etiqueta_slot(cita["slot"])
    urgente = anticipo.es_urgente(c, cita["slot"], ahora)
    if urgente or tardio or not c["resumen_diario"]:
        await asyncio.to_thread(
            avisos.notificar_anticipo, cita=cita, servicio=sv["nombre"], etiqueta=etiqueta,
            restante=anticipo.restante(sv["precio"], c), telefono=s.telefono, urgente=urgente, tardio=tardio)

    s.folio, s.nombre, s.paso, s.resuelta = cita["folio"], cita["nombre"], "fin", True
    return [_t(anticipo.texto_comprobante_ok(c, sv["nombre"], etiqueta, cita["folio"], sv["precio"])),
            _b("¿Algo más?", MENU)], s


# ---------- cancelar / reagendar ----------
def _folio_autorizado(s: Sesion, cita: dict[str, Any] | None) -> bool:
    """En WhatsApp el folio solo abre la cita del mismo teléfono."""
    if s.canal != "wa" or not cita:
        return True
    return _t10(cita.get("telefono", "")) == _t10(s.telefono)


def _paso_citas_folio(s: Sesion, texto: str, datos: dict[str, Any]):
    respuesta = _norm(texto)
    # Cualquier negativa ("no", "no sé", "no la tengo") o un "olvidé el folio" saca del
    # paso. Antes se tomaban por un folio inválido y el cliente quedaba en bucle, sin
    # saber que la salida era escribir "menu". Con folio escrito no se llega aquí:
    # _flujo lo extrae y llama con texto = folio.
    palabras = set(re.sub(r"[^\w ]", " ", respuesta).split())
    negativo = bool(palabras & {"no", "nop", "nel"}) or "olvid" in respuesta or "perdi" in respuesta
    if negativo or respuesta in {"volver", "regresar", "salir"}:
        s.paso = "inicio"
        s.folio = None
        return bienvenida(datos), s

    folio = _folio_de(texto) or (texto.strip().upper() if texto.strip() else "")
    cita = store.obtener(folio) if folio else None
    if not _folio_autorizado(s, cita):
        cita = None            # a partir de aquí se comporta como "no existe"
    if not cita:
        return [_t("No encuentro ninguna cita con ese folio. Revísalo en tu confirmación "
                   "(tipo AMB-7K4Q), o escribe *menú* si no la tienes a mano.")], s
    if cita["estado"] == "cancelada":
        s.paso = "inicio"
        return [_t(f"La cita {cita['folio']} ya está cancelada. Si quieres, agendamos otra."),
                _b("¿Qué hacemos?", MENU)], s
    if cita["estado"] == "expirada":
        s.paso = "inicio"
        return [_t(f"El apartado {cita['folio']} venció y el horario se liberó. Si ya hiciste la transferencia, "
                   "mándame tu comprobante y la manicurista te ayuda."), _b("¿Qué hacemos?", MENU)], s
    if cita["estado"] == "pendiente":
        s.folio, s.paso = cita["folio"], "comprobante"
        return [_t(f"Tu cita {cita['folio']} está apartada: falta el comprobante de tu anticipo. "
                   "Envíamelo aquí (foto, captura o PDF) o escribe *cancelar* para liberar el horario.")], s

    s.folio = cita["folio"]
    s.servicio_id = cita["servicio_id"]
    s.paso = "citas_accion"
    aviso = ""
    if _fuera_de_ventana(cita, datos) and not cita.get("anticipo_monto"):
        aviso = "\n\nOjo: falta menos de 2 h, así que el cambio lo confirma la manicurista."
    return [_t(f"Encontré tu cita.\n\n{_resumen_cita(cita, datos)}{aviso}"),
            _b("¿Qué quieres hacer?", [("reagendar", "Mover a otro horario"),
                                       ("cancelar", "Cancelar la cita"),
                                       ("menu", "Nada, gracias")])], s


async def _paso_citas_accion(s: Sesion, txt: str, texto: str, datos: dict[str, Any]):
    if txt in {"cancelar", "cancela", "anular", "anula"} or _es_cancelar(txt):
        return await _cancelar_folio(s, s.folio or "", datos)
    if txt in {"reagendar", "mover", "cambiar"} or _es_reagendar(txt):
        return _empezar_reagendar(s, s.folio or "", datos)
    if txt in {"menu", "nada", "no"}:
        s.paso = "inicio"
        return bienvenida(datos), s
    return [_b("¿Qué quieres hacer?", [("reagendar", "Mover a otro horario"),
                                       ("cancelar", "Cancelar la cita"),
                                       ("menu", "Nada, gracias")])], s


async def _cancelar_folio(s: Sesion, folio: str, datos: dict[str, Any], confirmado: bool = False):
    cita = store.obtener(folio)
    if not _folio_autorizado(s, cita):
        cita = None            # a partir de aquí se comporta como "no existe"
    if not cita or cita["estado"] in ("cancelada", "expirada"):
        s.paso = "inicio"
        return [_t("Ese folio no tiene una cita activa. ¿Te ayudo con otra cosa?"),
                _b("Menú:", MENU)], s
    if cita["estado"] == "pendiente":
        # Apartado sin pagar: no hay anticipo que perder.
        store.cancelar(cita["folio"])
        s.folio, s.paso = None, "fin"
        return [_t(f"Listo, liberé el horario que tenías apartado ({_etiqueta_slot(cita['slot'])})."),
                _b("¿Algo más?", MENU)], s

    con_anticipo = bool(cita.get("anticipo_monto"))
    devuelve = False
    if con_anticipo:
        # Con anticipo manda la política del negocio (no la ventana de 2 h): se puede cancelar
        # siempre, pero la clienta debe saber qué pasa con su dinero antes de confirmar.
        c = anticipo.cfg(datos)
        devuelve = anticipo.cancelacion_devuelve(c, anticipo.horas_para(cita["slot"], reloj.ahora()))
        if not confirmado:
            s.folio, s.paso = cita["folio"], "cancelar_confirmar"
            consecuencia = ("Tu anticipo se te devuelve (la manicurista te contactará)." if devuelve
                            else f"Si cancelas pierdes el anticipo de ${cita['anticipo_monto']:,}.")
            return [_b(f"{consecuencia} ¿Confirmas que quieres cancelar la cita {cita['folio']} "
                       f"del {_etiqueta_slot(cita['slot'])}?",
                       [("cancelar_si", "Sí, cancelar"), ("cancelar_no", "Conservar mi cita")])], s
    elif _fuera_de_ventana(cita, datos):
        s.handoff = True
        s.paso = "humano"
        return [_t(f"Falta menos de {datos['reglas_agenda']['cancelacion_libre_hasta_horas']} h para tu cita "
                   f"del {_etiqueta_slot(cita['slot'])}, así que la cancelación la cierra la manicurista. "
                   "Ya le pasé tu conversación. "
                   f"Si urge, marca al {datos['telefono']}.")], s

    cita = store.cancelar(folio)
    await asyncio.to_thread(
        calendar_sync.borrar, cita.get("calendar_event_id"),
        calendar_id=_calendario(cita.get("agenda_id") or ""),
    )
    store.set_evento(cita["folio"], None)
    s.folio = None
    s.paso = "fin"
    s.resuelta = True
    if con_anticipo:
        sv = _servicio(datos, cita["servicio_id"]) or {"nombre": cita["servicio_id"]}
        await asyncio.to_thread(
            avisos.notificar_cancelacion_anticipo, cita=cita, servicio=sv["nombre"],
            etiqueta=_etiqueta_slot(cita["slot"]), telefono=cita.get("telefono") or s.telefono, devuelve=devuelve)
        destino = (f"Tu anticipo de ${cita['anticipo_monto']:,} se te devuelve: la manicurista te contactará para hacerlo."
                   if devuelve else f"El anticipo de ${cita['anticipo_monto']:,} no se devuelve, como quedamos al agendar.")
        return [_t(f"Cancelada la cita {cita['folio']} del {_etiqueta_slot(cita['slot'])}. {destino}"),
                _b("¿Algo más?", MENU)], s
    return [_t(f"Cancelada la cita {cita['folio']} del {_etiqueta_slot(cita['slot'])}. "
               "El horario ya quedó libre.\n\nSi quieres, agendamos otro día."),
            _b("¿Algo más?", MENU)], s


async def _paso_cancelar_confirmar(s: Sesion, txt: str, datos: dict[str, Any]):
    if txt in SI_CANCELAR:
        return await _cancelar_folio(s, s.folio or "", datos, confirmado=True)
    if txt in NO_CANCELAR:
        s.paso = "inicio"
        return [_t("Va, tu cita se queda como está."), _b("¿Algo más?", MENU)], s
    return await _cancelar_folio(s, s.folio or "", datos)          # no entendió: vuelve a preguntar


def _no_se_puede_mover(s: Sesion, cita: dict[str, Any], c: dict[str, Any], motivo: str, horas: float):
    """Con anticipo, mover tiene reglas. Se explica y se ofrecen las dos salidas honestas."""
    r = c["reagendar"]
    if motivo == "max_veces":
        veces = int(cita.get("reagendadas") or 0)
        razon = f"Esta cita ya se movió {veces} {'vez' if veces == 1 else 'veces'} y no se puede mover otra vez."
    elif motivo == "poco_tiempo":
        razon = (f"Faltan menos de {int(r['horas_minimas'])} h para tu cita y ya no se puede mover "
                 "sin perder el anticipo.")
    else:
        razon = "Esta cita no se puede mover."
    devuelve = anticipo.cancelacion_devuelve(c, horas)
    salida = ("Si ya no puedes asistir, puedes cancelarla y tu anticipo se te devuelve." if devuelve
              else f"Si ya no puedes asistir, puedes cancelarla (se pierde el anticipo de ${cita['anticipo_monto']:,}).")
    s.folio, s.paso = cita["folio"], "citas_accion"
    return [_b(f"{razon}\n{salida}", [("cancelar", "Cancelar mi cita"), ("menu", "Conservar mi cita")])], s


def _empezar_reagendar(s: Sesion, folio: str, datos: dict[str, Any]):
    cita = store.obtener(folio)
    if not _folio_autorizado(s, cita):
        cita = None            # a partir de aquí se comporta como "no existe"
    if not cita or cita["estado"] in ("cancelada", "expirada"):
        s.paso = "inicio"
        return [_t("Ese folio no tiene una cita activa. ¿Te ayudo con otra cosa?"),
                _b("Menú:", MENU)], s
    if cita["estado"] == "pendiente":
        s.folio, s.paso = cita["folio"], "comprobante"
        return [_t("Esa cita todavía está apartada: falta el comprobante de tu anticipo. "
                   "Si prefieres otro horario, escribe *cancelar* y agendamos de nuevo.")], s
    if cita.get("anticipo_monto"):
        c = anticipo.cfg(datos)
        horas = anticipo.horas_para(cita["slot"], reloj.ahora())
        permitido, motivo = anticipo.reagendar_permitido(c, int(cita.get("reagendadas") or 0), horas)
        if not permitido:
            return _no_se_puede_mover(s, cita, c, motivo, horas)
    elif _fuera_de_ventana(cita, datos):
        s.handoff = True
        s.paso = "humano"
        return [_t(f"Falta menos de {datos['reglas_agenda']['cancelacion_libre_hasta_horas']} h para tu cita, "
                   "así que el cambio lo ve la manicurista. Ya le pasé tu conversación. "
                   f"Si urge, marca al {datos['telefono']}.")], s

    s.folio = cita["folio"]
    s.servicio_id = cita["servicio_id"]
    libres = [x for x in slots_disponibles(datos, cita["servicio_id"], excluir_folio=cita["folio"],
                                          agenda_id=cita.get("agenda_id") or "")
              if x != cita["slot"]]
    if not libres:
        s.paso = "inicio"
        s.handoff = True
        return [_t("No hay otros huecos en los próximos 7 días. Te paso con la manicurista para moverlo.")], s
    s.paso = "citas_slot"
    return [_b(f"Tu cita es {_etiqueta_slot(cita['slot'])}.\n¿A qué horario la movemos?",
               [(i, _etiqueta_slot(i)) for i in libres[:8]] +
               [("menu", "Mejor la dejo así")])], s


async def _paso_citas_slot(s: Sesion, txt: str, texto: str, datos: dict[str, Any]):
    folio = s.folio or ""
    cita = store.obtener(folio)
    if not _folio_autorizado(s, cita):
        cita = None            # volver a comprobar antes de modificar la cita
    if not cita or cita["estado"] in ("cancelada", "expirada"):
        s.paso = "inicio"
        return [_t("Esa cita ya no está activa."), _b("Menú:", MENU)], s
    sid = cita["servicio_id"]
    libres = [x for x in slots_disponibles(datos, sid, excluir_folio=folio,
                                              agenda_id=cita.get("agenda_id") or "") if x != cita["slot"]]

    if txt in {"menu", "no", "asi", "dejar"}:
        s.paso = "inicio"
        return [_t(f"Va, la dejo como estaba: {_etiqueta_slot(cita['slot'])}."),
                _b("¿Algo más?", MENU)], s

    elegido = _elegir_slot(libres, txt, texto)
    if elegido is None:
        if not libres:
            s.paso = "inicio"
            return [_t("Ya no hay otros huecos disponibles."), _b("Menú:", MENU)], s
        return [_b("Ese horario no está disponible. Elige otro:",
                   [(i, _etiqueta_slot(i)) for i in libres[:8]])], s

    try:
        nueva = store.reagendar(folio, elegido)
    except store.SlotOcupado:
        libres = [x for x in slots_disponibles(datos, sid, excluir_folio=folio,
                                              agenda_id=cita.get("agenda_id") or "") if x != cita["slot"]]
        return [_b("Justo se ocupó. Elige otro:",
                   [(i, _etiqueta_slot(i)) for i in libres[:8]])], s
    except store.FolioNoEncontrado:
        s.paso = "inicio"
        return [_t("Esa cita ya no está activa."), _b("Menú:", MENU)], s

    event_id = await asyncio.to_thread(
        calendar_sync.mover, cita.get("calendar_event_id"), nueva, datos,
        calendar_id=_calendario(cita.get("agenda_id") or ""),
    )
    if event_id and event_id != cita.get("calendar_event_id"):
        store.set_evento(folio, event_id)

    s.paso = "fin"
    s.resuelta = True
    conserva = " Conservas tu anticipo." if nueva.get("anticipo_monto") else ""
    return [_t(f"Movida. Tu cita {nueva['folio']} ahora es {_etiqueta_slot(nueva['slot'])}.{conserva} "
               f"Te esperamos en {datos['direccion']}."),
            _b("¿Algo más?", MENU)], s
