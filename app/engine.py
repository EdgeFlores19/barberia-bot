"""Motor conversacional determinista: menú, FAQ, agendar, cancelar y reagendar.

El LLM no decide horarios ni precios. Solo redacta texto libre (app/llm.py).
La disponibilidad la calcula app/slots.py; las citas viven en app/store.py (SQLite).
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import avisos, calendar_sync, config, llm, reloj, store
from . import slots as horarios
from .telefono import t10 as _t10

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

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
    ultima_actividad: datetime = field(default_factory=reloj.ahora)
    handoff_avisado: bool = False           # ya se avisó a la manicurista en este handoff


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
    return {
        "tipo": "botones",
        "texto": texto,
        "opciones": [{"id": i, "label": l} for i, l in opciones],
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
def slots_disponibles(
    datos: dict[str, Any],
    servicio_id: str,
    ahora: datetime | None = None,
    excluir_folio: str | None = None,
) -> list[str]:
    """Huecos donde cabe el servicio completo, según app/slots.py."""
    citas = store.confirmadas()
    if excluir_folio:
        # Al reagendar, su propio horario no debe bloquearse a sí mismo.
        citas = [c for c in citas if c["folio"] != excluir_folio]
    return horarios.generar_slots(datos, servicio_id, citas, ahora or reloj.ahora())


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

    respuestas, s = await _flujo(s, texto, txt, datos)
    await _avisar_handoff(s, texto)
    return respuestas, s


async def _avisar_handoff(s: Sesion, texto: str) -> None:
    """Registra el handoff y avisa al dueño UNA vez por handoff, venga de donde venga.

    Antes solo avisaba la rama de "humano"/"persona". Pasar a la manicurista por una
    cancelación fuera de ventana, por falta de huecos o por la opción 5 le decía al
    cliente "ya le pasé tu conversación" sin avisarle a nadie; y repetir "humano"
    mandaba un Telegram por cada mensaje.
    """
    if not s.handoff:
        s.handoff_avisado = False      # volvió al bot: el siguiente handoff avisa de nuevo
        return
    if s.handoff_avisado:
        return
    s.handoff_avisado = True
    store.registrar_handoff(s.sid, texto)
    # La red va en otro hilo: Telegram no debe bloquear el event loop.
    await asyncio.to_thread(avisos.notificar_handoff, s.sid, s.telefono, texto)


async def _flujo(
    s: Sesion, texto: str, txt: str, datos: dict[str, Any]
) -> tuple[list[dict[str, Any]], Sesion]:
    """Salidas globales y pasos de la conversación, ya pasados los topes."""
    # salidas globales
    if txt in {"menu", "inicio", "hola", "buenas", "buenos dias", "buenas tardes", "hey", "que tal"}:
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
        s.paso = "inicio"
        return await _paso_inicio(s, txt, texto, datos)

    if s.paso == "inicio":
        return await _paso_inicio(s, txt, texto, datos)
    if s.paso == "agendar_servicio":
        return _paso_servicio(s, txt, datos)
    if s.paso == "agendar_slot":
        return _paso_slot(s, txt, texto, datos)
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
        return _ofrecer_servicios(s, datos)

    s.servicio_id = sid
    s.paso = "agendar_slot"
    libres = slots_disponibles(datos, sid)
    if not libres:
        s.paso = "inicio"
        s.handoff = True
        return [_t("No me quedan huecos en los próximos 7 días. Te paso con la manicurista para ver si abre un espacio.")], s
    sv = _servicio(datos, sid) or {"nombre": sid, "precio": 0, "minutos": 30}
    return [_b(f"{sv['nombre']} (${sv['precio']}, {sv['minutos']} min).\n¿Qué día te queda bien?",
               [(i, _etiqueta_slot(i)) for i in libres[:8]] + [("humano", "Otra fecha / hablar con la manicurista")])], s


def _paso_slot(s: Sesion, txt: str, texto: str, datos: dict[str, Any]):
    sid = s.servicio_id or datos["servicios"][0]["id"]
    libres = slots_disponibles(datos, sid)
    elegido = _elegir_slot(libres, txt, texto)
    if elegido is None:
        if not libres:
            return _paso_servicio(s, sid, datos)
        return [_b("Ese horario ya no está. Elige otro:",
                   [(i, _etiqueta_slot(i)) for i in libres[:8]])], s
    s.slot = elegido
    s.paso = "agendar_nombre"
    return [_t("Apartado. ¿A nombre de quién queda?")], s


async def _paso_nombre(s: Sesion, texto: str, datos: dict[str, Any]):
    nombre = texto.strip()[:60]
    if len(nombre) < 2:
        return [_t("Dame un nombre para la cita, por favor.")], s
    sid = s.servicio_id or ""
    slot = s.slot or ""

    ok, motivo = horarios.validar(datos, sid, slot, store.confirmadas(), reloj.ahora())
    if not ok:
        s.paso = "agendar_slot"
        libres = slots_disponibles(datos, sid)
        return [_b(f"Ese horario se acaba de ocupar ({motivo}). Elige otro:",
                   [(i, _etiqueta_slot(i)) for i in libres[:8]])], s

    try:
        # En WhatsApp la cita queda ligada al teléfono: sin esto, el candado de folio
        # (`_folio_autorizado`) no deja al propio cliente cancelar ni mover su cita.
        tel = s.telefono if s.canal == "wa" else ""
        cita = store.crear(slot, sid, nombre, tel)
    except store.SlotOcupado:
        s.paso = "agendar_slot"
        libres = slots_disponibles(datos, sid)
        return [_b("Justo alguien más tomó ese horario. Elige otro:",
                   [(i, _etiqueta_slot(i)) for i in libres[:8]])], s

    event_id = await asyncio.to_thread(calendar_sync.crear, cita, datos)
    if event_id:
        store.set_evento(cita["folio"], event_id)

    s.nombre = nombre
    s.folio = cita["folio"]
    s.paso = "fin"
    s.resuelta = True
    sv = _servicio(datos, sid) or {"nombre": sid, "precio": 0}
    horas = datos["reglas_agenda"]["cancelacion_libre_hasta_horas"]
    return [_t(f"Listo, {nombre}. Quedas {_etiqueta_slot(cita['slot'])} para {sv['nombre']} "
               f"(${sv['precio']}). Llega 5 min antes.\n\n"
               f"Tu folio es {cita['folio']}. Puedes cancelar o mover sin costo hasta {horas} h antes.\n"
               f"Te esperamos en {datos['direccion']}."),
            _b("¿Algo más?", MENU)], s


# ---------- cancelar / reagendar ----------
def _folio_autorizado(s: Sesion, cita: dict[str, Any] | None) -> bool:
    """En WhatsApp el folio solo abre la cita del mismo teléfono."""
    if s.canal != "wa" or not cita:
        return True
    return _t10(cita.get("telefono", "")) == _t10(s.telefono)


def _paso_citas_folio(s: Sesion, texto: str, datos: dict[str, Any]):
    folio = _folio_de(texto) or (texto.strip().upper() if texto.strip() else "")
    cita = store.obtener(folio) if folio else None
    if not _folio_autorizado(s, cita):
        cita = None            # a partir de aquí se comporta como "no existe"
    if not cita:
        return [_t("No encuentro ninguna cita con ese folio. Revísalo en tu confirmación (tipo AMB-7K4Q).")], s
    if cita["estado"] == "cancelada":
        s.paso = "inicio"
        return [_t(f"La cita {cita['folio']} ya está cancelada. Si quieres, agendamos otra."),
                _b("¿Qué hacemos?", MENU)], s

    s.folio = cita["folio"]
    s.servicio_id = cita["servicio_id"]
    s.paso = "citas_accion"
    aviso = ""
    if _fuera_de_ventana(cita, datos):
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


async def _cancelar_folio(s: Sesion, folio: str, datos: dict[str, Any]):
    cita = store.obtener(folio)
    if not _folio_autorizado(s, cita):
        cita = None            # a partir de aquí se comporta como "no existe"
    if not cita or cita["estado"] == "cancelada":
        s.paso = "inicio"
        return [_t("Ese folio no tiene una cita activa. ¿Te ayudo con otra cosa?"),
                _b("Menú:", MENU)], s
    if _fuera_de_ventana(cita, datos):
        s.handoff = True
        s.paso = "humano"
        return [_t(f"Falta menos de {datos['reglas_agenda']['cancelacion_libre_hasta_horas']} h para tu cita "
                   f"del {_etiqueta_slot(cita['slot'])}, así que la cancelación la cierra la manicurista. "
                   "Ya le pasé tu conversación. "
                   f"Si urge, marca al {datos['telefono']}.")], s

    cita = store.cancelar(folio)
    await asyncio.to_thread(calendar_sync.borrar, cita.get("calendar_event_id"))
    store.set_evento(cita["folio"], None)
    s.folio = None
    s.paso = "fin"
    s.resuelta = True
    return [_t(f"Cancelada la cita {cita['folio']} del {_etiqueta_slot(cita['slot'])}. "
               "El horario ya quedó libre.\n\nSi quieres, agendamos otro día."),
            _b("¿Algo más?", MENU)], s


def _empezar_reagendar(s: Sesion, folio: str, datos: dict[str, Any]):
    cita = store.obtener(folio)
    if not _folio_autorizado(s, cita):
        cita = None            # a partir de aquí se comporta como "no existe"
    if not cita or cita["estado"] == "cancelada":
        s.paso = "inicio"
        return [_t("Ese folio no tiene una cita activa. ¿Te ayudo con otra cosa?"),
                _b("Menú:", MENU)], s
    if _fuera_de_ventana(cita, datos):
        s.handoff = True
        s.paso = "humano"
        return [_t(f"Falta menos de {datos['reglas_agenda']['cancelacion_libre_hasta_horas']} h para tu cita, "
                   "así que el cambio lo ve la manicurista. Ya le pasé tu conversación. "
                   f"Si urge, marca al {datos['telefono']}.")], s

    s.folio = cita["folio"]
    s.servicio_id = cita["servicio_id"]
    libres = [x for x in slots_disponibles(datos, cita["servicio_id"], excluir_folio=cita["folio"])
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
    if not cita or cita["estado"] == "cancelada":
        s.paso = "inicio"
        return [_t("Esa cita ya no está activa."), _b("Menú:", MENU)], s
    sid = cita["servicio_id"]
    libres = [x for x in slots_disponibles(datos, sid, excluir_folio=folio) if x != cita["slot"]]

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
        libres = [x for x in slots_disponibles(datos, sid, excluir_folio=folio) if x != cita["slot"]]
        return [_b("Justo se ocupó. Elige otro:",
                   [(i, _etiqueta_slot(i)) for i in libres[:8]])], s
    except store.FolioNoEncontrado:
        s.paso = "inicio"
        return [_t("Esa cita ya no está activa."), _b("Menú:", MENU)], s

    event_id = await asyncio.to_thread(calendar_sync.mover, cita.get("calendar_event_id"), nueva, datos)
    if event_id and event_id != cita.get("calendar_event_id"):
        store.set_evento(folio, event_id)

    s.paso = "fin"
    s.resuelta = True
    return [_t(f"Movida. Tu cita {nueva['folio']} ahora es {_etiqueta_slot(nueva['slot'])}. "
               f"Te esperamos en {datos['direccion']}."),
            _b("¿Algo más?", MENU)], s
