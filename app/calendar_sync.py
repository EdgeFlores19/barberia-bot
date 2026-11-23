"""Google Calendar opcional: espejo de las citas y, en sentido contrario, bloqueo de huecos.

Si falta GOOGLE_CALENDAR_ID, el token o la red, el bot agenda igual: SQLite es la fuente
de verdad. Calendar hace dos cosas, y ninguna puede tumbar una reserva:
  1. Espejo (bot -> Calendar): cada cita se copia como evento para verla en el teléfono.
  2. Bloqueo (Calendar -> bot): lo que la manicurista anota a mano ahí ocupa ese horario
     y el bot no lo ofrece. Se lee con events.list y se guarda en un caché corto; si la
     lectura falla, el bot sigue con lo que sabe y avisa una vez (ver ADR 0007).
Se usa REST directo (httpx) para no arrastrar google-api-python-client.

Variables: GOOGLE_TOKEN_JSON, GOOGLE_CALENDAR_ID, CALENDAR_TZ, CALENDAR_BLOQUEO,
CALENDAR_BLOQUEO_TTL_S, CALENDAR_BLOQUEO_REINTENTO_S, CALENDAR_AGENDAS_PREFIJO,
CALENDAR_AGENDAS_TTL_S.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from . import avisos, config, store

log = logging.getLogger("ambar.calendar")

_TOKEN: dict[str, Any] = {"access": "", "expira": 0.0}

# Diagnóstico sin secretos: por qué no se está agendando. Sale en /health.
_ESTADO: dict[str, Any] = {"intentos": 0, "ok": 0, "ultimo_error": ""}

# Marca que lleva el evento espejo del propio bot: al leer Calendar se ignora, porque esa
# cita ya está en SQLite (contarla dos veces estorbaría al mover una cita sobre sí misma).
MARCA_BOT = "Reservado por el bot"


class _SinAcceso(Exception):
    """No hay access token para hablar con Calendar."""


def _mono() -> float:
    return time.monotonic()


def _bloqueo_inicial() -> dict[str, Any]:
    return {
        "intervalos": [],      # [(inicio, fin)] naive, hora del negocio
        "ok_ts": None,         # monotonic de la última lectura buena
        "fallo_ts": None,      # monotonic del último fallo
        "caido": False,
        "alertado": False,
        "ultimo_error": "",
        "lecturas": 0,
    }


# Un estado de bloqueo por calendario: si una agenda tiene su Calendar caído, las demás
# siguen leyéndose. La clave "" es el calendario único de siempre (GOOGLE_CALENDAR_ID).
_BLOQS: dict[str, dict[str, Any]] = {}


def _estado_bloq(calendar_id: str | None = None) -> dict[str, Any]:
    """Estado (y caché) del bloqueo de un calendario, creándolo si es la primera vez."""
    clave = calendar_id or ""
    if clave not in _BLOQS:
        _BLOQS[clave] = _bloqueo_inicial()
    return _BLOQS[clave]


def _anotar(motivo: str) -> None:
    _ESTADO["ultimo_error"] = motivo[:300]


def _hay_agendas() -> bool:
    """¿Hay alguna agenda activa? Solo mira la tabla: ni red ni reloj."""
    try:
        return bool(store.agendas(solo_activas=True))
    except Exception:
        return False


def habilitado() -> bool:
    """¿Se puede hablar con Google? Hace falta token y algo que leer.

    Con multiagenda el ID del calendario se descubre, así que no está en el entorno: basta
    con que haya agendas dadas de alta. Se pide el prefijo para no encender el espejo con
    filas viejas cuando el dueño ya volvió al calendario único.
    """
    if not config.CALENDAR_TOKEN_FILE:
        return False
    if config.CALENDAR_ID:
        return True
    return bool(config.CALENDAR_AGENDAS_PREFIJO) and _hay_agendas()


def salud(agendas: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Estado de la integración para /health. Nunca expone el token ni el id del calendario.

    `agendas` es la lista de agendas configuradas (clave, nombre y calendar_id); se resume
    cada una por su clave y su nombre, que es lo único que necesita quien mira /health.
    """
    t = _leer_token()
    if not config.CALENDAR_TOKEN_FILE:
        motivo = "falta GOOGLE_TOKEN_JSON"
    elif not t:
        motivo = "el token no se pudo leer (archivo inexistente, sin permisos o JSON inválido)"
    elif not t.get("refresh_token"):
        motivo = "el token no tiene refresh_token"
    elif not config.CALENDAR_ID and not config.CALENDAR_AGENDAS_PREFIJO:
        # Sin ID ni prefijo no hay nada que leer: es la config del demo.
        motivo = "falta GOOGLE_CALENDAR_ID o CALENDAR_AGENDAS_PREFIJO"
    elif not config.CALENDAR_ID and not _hay_agendas():
        # Con multiagenda el calendario se descubre: sin filas todavía no es un error de
        # configuración, pero sí lo más probable si el token no puede listar calendarios.
        motivo = "el prefijo no encontró agendas activas (¿el token puede listar calendarios?)"
    else:
        motivo = ""
    en_variable = config.CALENDAR_TOKEN_FILE.strip().startswith("{")
    return {
        "habilitado": habilitado(),
        "calendario": "configurado" if config.CALENDAR_ID else "(vacío)",
        # Nunca se imprime el valor de la variable: si trae el JSON, se enmascara.
        "token_file": "(JSON en la variable GOOGLE_TOKEN_JSON)" if en_variable else config.CALENDAR_TOKEN_FILE,
        "token_en_variable": en_variable,
        "token_file_existe": (not en_variable) and os.path.exists(config.CALENDAR_TOKEN_FILE),
        "token_legible": bool(t),
        "tiene_refresh": bool(t and t.get("refresh_token")),
        "intentos": _ESTADO["intentos"],
        "eventos_ok": _ESTADO["ok"],
        "problema_config": motivo,
        "ultimo_error": _ESTADO["ultimo_error"],
        "bloqueo": _salud_bloqueo(),
        "agendas": [
            {
                "clave": str(a.get("clave", "")),
                "nombre": str(a.get("nombre", "")),
                "bloqueo": _salud_bloqueo(a.get("calendar_id") or ""),
            }
            for a in (agendas or [])
        ],
    }


def _leer_token() -> dict[str, Any] | None:
    """El token puede venir como ruta a un archivo o como JSON pegado en la variable.

    Pegar el JSON entero evita depender de que el Secret File de Render llegue montado
    en /etc/secrets: si el archivo no aparece, el bot se queda sin Calendar en silencio.
    """
    origen = config.CALENDAR_TOKEN_FILE.strip()
    if origen.startswith("{"):
        try:
            return json.loads(origen)
        except ValueError as e:
            log.warning("GOOGLE_TOKEN_JSON no es JSON válido: %s", e)
            return None
    try:
        with open(origen, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        log.warning("sin token de Calendar: %s", e)
        return None


def _access_token() -> str | None:
    """Token vigente; lo refresca con el refresh_token si hace falta."""
    if _TOKEN["access"] and _TOKEN["expira"] > time.time() + 60:
        return _TOKEN["access"]
    t = _leer_token()
    if not t:
        return None
    # A veces el archivo trae un access_token todavía válido.
    expira_txt = str(t.get("expiry", ""))
    if t.get("token") and expira_txt:
        try:
            expira = datetime.fromisoformat(expira_txt.replace("Z", "+00:00")).timestamp()
            if expira > time.time() + 120:
                _TOKEN.update(access=t["token"], expira=expira)
                return _TOKEN["access"]
        except ValueError:
            pass
    if not t.get("refresh_token"):
        return None
    try:
        r = httpx.post(
            t.get("token_uri", "https://oauth2.googleapis.com/token"),
            data={
                "grant_type": "refresh_token",
                "client_id": t["client_id"],
                "client_secret": t["client_secret"],
                "refresh_token": t["refresh_token"],
            },
            timeout=config.CALENDAR_TIMEOUT_S,
        )
        r.raise_for_status()
        datos = r.json()
        _TOKEN["access"] = datos["access_token"]
        _TOKEN["expira"] = time.time() + int(datos.get("expires_in", 3600))
        return _TOKEN["access"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("no se pudo refrescar el token de Calendar: %s", e)
        return None


def _cabeceras() -> dict[str, str] | None:
    token = _access_token()
    return {"Authorization": f"Bearer {token}"} if token else None


def _evento(cita: dict[str, Any], datos: dict[str, Any]) -> dict[str, Any]:
    servicio = next(
        (s for s in datos["servicios"] if s["id"] == cita["servicio_id"]), None
    )
    nombre_sv = servicio["nombre"] if servicio else cita["servicio_id"]
    minutos = int(servicio["minutos"]) if servicio else 30
    inicio = datetime.fromisoformat(cita["slot"])
    fin = inicio + timedelta(minutes=minutos)
    return {
        "summary": f"{nombre_sv} · {cita['nombre']}",
        "description": f"Folio {cita['folio']}. {MARCA_BOT} de {datos['nombre']}.",
        "location": datos.get("direccion", ""),
        "start": {"dateTime": inicio.isoformat(timespec="seconds"), "timeZone": config.CALENDAR_TZ},
        "end": {"dateTime": fin.isoformat(timespec="seconds"), "timeZone": config.CALENDAR_TZ},
    }


def _url(event_id: str = "", calendar_id: str | None = None) -> str:
    cal = calendar_id or config.CALENDAR_ID
    base = f"https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
    return f"{base}/{event_id}" if event_id else base


def crear(cita: dict[str, Any], datos: dict[str, Any], calendar_id: str | None = None) -> str | None:
    """Crea el evento espejo. Devuelve el id o None si Calendar no está disponible.

    `calendar_id` elige el calendario; sin él se usa GOOGLE_CALENDAR_ID, como siempre.
    """
    if not habilitado():
        _anotar("Calendar deshabilitado (revisa GOOGLE_CALENDAR_ID / GOOGLE_TOKEN_JSON)")
        return None
    cab = _cabeceras()
    if not cab:
        _anotar("sin access token: no se pudo leer el archivo ni refrescar")
        return None
    _ESTADO["intentos"] += 1
    try:
        r = httpx.post(
            _url(calendar_id=calendar_id),
            json=_evento(cita, datos),
            headers=cab,
            timeout=config.CALENDAR_TIMEOUT_S,
        )
        r.raise_for_status()
        _ESTADO["ok"] += 1
        _anotar("")
        return r.json().get("id")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("no se creó el evento de %s: %s", cita.get("folio"), e)
        _anotar(f"crear {cita.get('folio')}: {e}")
        return None


def mover(
    event_id: str | None,
    cita: dict[str, Any],
    datos: dict[str, Any],
    calendar_id: str | None = None,
) -> str | None:
    """Mueve el evento existente; si ya no existe, lo vuelve a crear."""
    if not habilitado():
        _anotar("Calendar deshabilitado (revisa GOOGLE_CALENDAR_ID / GOOGLE_TOKEN_JSON)")
        return None
    cab = _cabeceras()
    if not cab:
        _anotar("sin access token: no se pudo leer el archivo ni refrescar")
        return None
    if not event_id:
        return crear(cita, datos, calendar_id)
    try:
        r = httpx.patch(
            _url(event_id, calendar_id),
            json=_evento(cita, datos),
            headers=cab,
            timeout=config.CALENDAR_TIMEOUT_S,
        )
        if r.status_code == 404:
            return crear(cita, datos, calendar_id)
        r.raise_for_status()
        return event_id
    except (httpx.HTTPError, ValueError) as e:
        log.warning("no se movió el evento de %s: %s", cita.get("folio"), e)
        _anotar(f"mover {cita.get('folio')}: {e}")
        return event_id


def borrar(event_id: str | None, calendar_id: str | None = None) -> bool:
    """Borra el evento espejo. 404/410 cuentan como borrado."""
    if not habilitado() or not event_id:
        return False
    cab = _cabeceras()
    if not cab:
        return False
    try:
        r = httpx.delete(_url(event_id, calendar_id), headers=cab, timeout=config.CALENDAR_TIMEOUT_S)
        return r.status_code in (200, 204, 404, 410)
    except httpx.HTTPError as e:
        log.warning("no se borró el evento %s: %s", event_id, e)
        _anotar(f"borrar {event_id}: {e}")
        return False


# ---------------------------------------------------------------- bloqueo (Calendar -> bot)
def bloqueo_activo() -> bool:
    """¿Se leen los eventos de Calendar para bloquear huecos?"""
    return habilitado() and config.CALENDAR_BLOQUEO


def reiniciar_bloqueos(calendar_id: str | None = None) -> None:
    """Vacía el caché y el estado del bloqueo (pruebas).

    Sin argumento vacía todos los calendarios; con `calendar_id`, solo el de esa agenda.
    """
    if calendar_id is None:
        _BLOQS.clear()
        return
    _BLOQS.pop(calendar_id or "", None)


def bloqueo_vencido(calendar_id: str | None = None) -> bool:
    """¿Toca volver a leer? Solo mira el reloj: no hace red."""
    ok = _estado_bloq(calendar_id)["ok_ts"]
    return ok is None or _mono() - ok >= config.CALENDAR_BLOQUEO_TTL_S


def _salud_bloqueo(calendar_id: str | None = None) -> dict[str, Any]:
    estado = _estado_bloq(calendar_id)
    ok = estado["ok_ts"]
    return {
        "activo": bloqueo_activo(),
        "intervalos": len(estado["intervalos"]),
        "lecturas": estado["lecturas"],
        "edad_s": None if ok is None else round(_mono() - ok),
        "caido": estado["caido"],
        "ultimo_error": estado["ultimo_error"],
    }


def bloqueos(calendar_id: str | None = None) -> list[dict[str, Any]]:
    """Lo del caché como citas ocupadas para app/slots.py. No hace red.

    Cada agenda ocupa su propio horario: quien llama suma `bloqueos()` de la agenda que
    esté consultando.
    """
    if not bloqueo_activo():
        return []
    return [
        {
            "slot": ini.isoformat(timespec="minutes"),
            "fin": fin.isoformat(timespec="minutes"),
            "estado": "confirmada",
            "bloqueo_externo": True,
        }
        for ini, fin in _estado_bloq(calendar_id)["intervalos"]
    ]


def _a_local(txt: str, tz: ZoneInfo) -> datetime:
    """dateTime RFC 3339 de Google -> hora del negocio sin zona."""
    dt = datetime.fromisoformat(str(txt).replace("Z", "+00:00"))
    return dt.astimezone(tz).replace(tzinfo=None) if dt.tzinfo else dt


def _intervalo_evento(ev: dict[str, Any], tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """Intervalo que un evento le quita a la manicurista, o None si no la ocupa."""
    if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
        return None                       # cancelado, o marcado como "libre" a propósito
    if MARCA_BOT in str(ev.get("description") or ""):
        return None                       # el espejo del bot: ya está en SQLite
    for invitado in ev.get("attendees") or []:
        if invitado.get("self") and invitado.get("responseStatus") == "declined":
            return None
    inicio, fin = ev.get("start") or {}, ev.get("end") or {}
    try:
        if inicio.get("dateTime"):
            ini, fn = _a_local(inicio["dateTime"], tz), _a_local(fin["dateTime"], tz)
        elif inicio.get("date"):          # todo el día: el `end` es exclusivo
            ini = datetime.combine(date.fromisoformat(inicio["date"]), datetime.min.time())
            fn = (
                datetime.combine(date.fromisoformat(fin["date"]), datetime.min.time())
                if fin.get("date") else ini + timedelta(days=1)
            )
        else:
            return None
    except (KeyError, ValueError):
        return None                       # un evento raro no debe tumbar la lectura
    return (ini, fn) if fn > ini else None


def _listar_eventos(
    desde: datetime, hasta: datetime, calendar_id: str | None = None
) -> list[dict[str, Any]]:
    """events.list del calendario entre dos horas locales. Sin títulos: no se necesitan."""
    cab = _cabeceras()
    if not cab:
        raise _SinAcceso()
    tz = ZoneInfo(config.CALENDAR_TZ)
    params = {
        "timeMin": desde.replace(tzinfo=tz).isoformat(timespec="seconds"),
        "timeMax": hasta.replace(tzinfo=tz).isoformat(timespec="seconds"),
        "singleEvents": "true",           # expande los recurrentes
        "orderBy": "startTime",
        "maxResults": "250",
        "fields": "nextPageToken,items(status,transparency,description,attendees(self,responseStatus),start,end)",
    }
    eventos: list[dict[str, Any]] = []
    for _ in range(4):                    # tope de páginas: 8 días no llegan ni a una
        r = httpx.get(
            _url(calendar_id=calendar_id),
            params=params,
            headers=cab,
            timeout=config.CALENDAR_TIMEOUT_S,
        )
        r.raise_for_status()
        cuerpo = r.json()
        eventos += cuerpo.get("items", [])
        if not cuerpo.get("nextPageToken"):
            break
        params["pageToken"] = cuerpo["nextPageToken"]
    return eventos


def _motivo(e: Exception) -> str:
    """Causa del fallo SIN el texto de la excepción: lleva la URL con el id del calendario."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return type(e).__name__


def refrescar_bloqueos(
    ahora: datetime, dias: int, forzar: bool = False, calendar_id: str | None = None
) -> bool:
    """Relee Calendar y actualiza el caché. Síncrona: se llama con asyncio.to_thread.

    `forzar` se salta el TTL (se usa al confirmar una cita), pero no el periodo de
    reintento tras un fallo: con Calendar caído no se espera el timeout en cada mensaje.
    Cada calendario lleva su propio caché, su propio reintento y su propio aviso, así que
    una agenda caída no deja sin bloqueos a las demás.
    Devuelve True si el caché quedó vigente.
    """
    if not bloqueo_activo():
        return False
    estado = _estado_bloq(calendar_id)
    ahora_m = _mono()
    if estado["fallo_ts"] is not None and ahora_m - estado["fallo_ts"] < config.CALENDAR_BLOQUEO_REINTENTO_S:
        return False
    if not forzar and not bloqueo_vencido(calendar_id):
        return True
    tz = ZoneInfo(config.CALENDAR_TZ)
    desde = ahora
    hasta = datetime.combine(ahora.date() + timedelta(days=dias + 1), datetime.min.time())
    try:
        # Con el calendario único de siempre se llama como antes: las pruebas que sustituyen
        # _listar_eventos con dos argumentos siguen valiendo.
        eventos = (
            _listar_eventos(desde, hasta, calendar_id)
            if calendar_id
            else _listar_eventos(desde, hasta)
        )
        intervalos = [i for i in (_intervalo_evento(e, tz) for e in eventos) if i]
    except (httpx.HTTPError, ValueError, KeyError, _SinAcceso) as e:
        estado["fallo_ts"] = ahora_m
        estado["caido"] = True
        estado["ultimo_error"] = _motivo(e)
        log.warning("no se pudo leer Calendar (%s); se agenda con lo que hay en SQLite", estado["ultimo_error"])
        if not estado["alertado"]:
            estado["alertado"] = True
            avisos.notificar_alerta(
                "No pude leer tu Google Calendar. Mientras tanto el bot no sabe de los eventos "
                "que anotes a mano y podría ofrecer un horario ocupado. Revisa /health."
            )
        return False
    estado.update(intervalos=intervalos, ok_ts=ahora_m, fallo_ts=None, caido=False, ultimo_error="")
    estado["lecturas"] += 1
    if estado["alertado"]:
        estado["alertado"] = False
        avisos.notificar_alerta("Google Calendar volvió a responder: los eventos manuales vuelven a bloquear horarios.")
    return True

# ------------------------------------------------------- agendas (una persona por calendario)
def _agendas_inicial() -> dict[str, Any]:
    return {
        "ok_ts": None,         # monotonic de la última lectura buena de calendarList
        "fallo_ts": None,      # monotonic del último fallo
        "degradado": False,    # se leyó mal: la lista de agendas quedó como estaba
        "alertado": False,
        "ultimo_error": "",
        "lecturas": 0,
        "reporte": {},         # lo último que cambió (altas, renombres, bajas…)
    }


_AGENDAS: dict[str, Any] = _agendas_inicial()


def reiniciar_agendas() -> None:
    """Vacía el estado del descubrimiento de agendas (pruebas)."""
    _AGENDAS.clear()
    _AGENDAS.update(_agendas_inicial())


def multiagenda() -> bool:
    """¿Hay que descubrir agendas? Hace falta el prefijo y un token con el que ir a buscarlas.

    No depende de que ya haya agendas en la tabla (a diferencia de `habilitado()`): la primera vez
    la tabla está vacía y es justo esta lectura la que la llena. Si dependiera de ellas, nunca
    se descubriría ninguna.
    """
    return bool(config.CALENDAR_AGENDAS_PREFIJO and config.CALENDAR_TOKEN_FILE)


def necesita_bucle() -> bool:
    """¿Hay algo que mantener al día en segundo plano?

    El espejo y los bloqueos (`habilitado()`: hay a dónde escribir y leer) o el descubrimiento de
    agendas (`multiagenda()`). Lo usan el arranque de la app y cada pasada del bucle.
    """
    return habilitado() or multiagenda()


def agendas_vencidas() -> bool:
    """¿Toca volver a leer la lista de calendarios? Solo mira el reloj: no hace red."""
    ok = _AGENDAS["ok_ts"]
    return ok is None or _mono() - ok >= config.CALENDAR_AGENDAS_TTL_S


def salud_agendas() -> dict[str, Any]:
    """Estado del descubrimiento para /health. Sin ids de calendario ni nombres de personas.

    Los cambios van como conteos (`+2`, `-1`) y no como listas: las listas llevan nombres
    de personas y `/health` es pública.
    """
    ok = _AGENDAS["ok_ts"]
    reporte = _AGENDAS["reporte"] if isinstance(_AGENDAS["reporte"], dict) else {}
    return {
        "multiagenda": multiagenda(),
        "prefix_configurado": bool(config.CALENDAR_AGENDAS_PREFIJO),
        "lecturas": _AGENDAS["lecturas"],
        "edad_s": None if ok is None else round(_mono() - ok),
        "degradado": _AGENDAS["degradado"],
        "ultimo_error": _AGENDAS["ultimo_error"],
        **{
            clave: len(reporte.get(clave) or [])
            for clave in ("altas", "renombres", "bajas", "reactivadas", "sin_permiso")
        },
    }


def nombre_de_agenda(nombre: str) -> str | None:
    """Nombre tal como lo ve la clienta, o None si el calendario no es una agenda.

    El nombre de la agenda es lo que sigue al prefijo, sin espacios sobrantes. El prefijo
    se compara sin distinguir mayúsculas porque lo escribe una persona en Google.
    """
    prefijo = config.CALENDAR_AGENDAS_PREFIJO
    if not prefijo:
        return None
    texto = str(nombre or "").strip()
    if not texto.lower().startswith(prefijo.strip().lower()):
        return None
    return texto[len(prefijo.strip()):].strip()


def _listar_calendarios() -> list[dict[str, Any]]:
    """calendarList.list: todos los calendarios del token, con su permiso de escritura."""
    cab = _cabeceras()
    if not cab:
        raise _SinAcceso()
    params = {"maxResults": "250", "fields": "nextPageToken,items(id,summary,accessRole,deleted)"}
    salida: list[dict[str, Any]] = []
    for _ in range(4):
        r = httpx.get(
            "https://www.googleapis.com/calendar/v3/users/me/calendarList",
            params=params,
            headers=cab,
            timeout=config.CALENDAR_TIMEOUT_S,
        )
        r.raise_for_status()
        cuerpo = r.json()
        salida += cuerpo.get("items", [])
        if not cuerpo.get("nextPageToken"):
            break
        params["pageToken"] = cuerpo["nextPageToken"]
    return salida


def agendas_desde_calendarios(calendarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los calendarios que son agendas, con `escritura` en False si el bot no puede anotar.

    Se descartan los borrados y los que no llevan el prefijo. Los de solo lectura se
    devuelven marcados para que store no los ofrezca y avise al dueño, en vez de
    esconderlos sin decir nada.
    """
    agendas: list[dict[str, Any]] = []
    for cal in calendarios:
        if cal.get("deleted"):
            continue
        nombre = nombre_de_agenda(cal.get("summary") or "")
        if nombre is None:
            continue
        cid = str(cal.get("id") or "").strip()
        if not cid:
            continue
        agendas.append(
            {
                "calendar_id": cid,
                "nombre": nombre,
                "escritura": str(cal.get("accessRole") or "") in {"owner", "writer"},
            }
        )
    return agendas


def _reporte_vacio() -> dict[str, Any]:
    """Reporte sin cambios; con `degradado` en True cuando la lectura no sirvió."""
    return {
        "altas": [], "renombres": [], "bajas": [], "reactivadas": [],
        "sin_permiso": [], "degradado": True,
    }


def _texto_cambios(reporte: dict[str, Any]) -> str:
    """Aviso para el dueño con lo que cambió en Google, en una línea por tipo."""
    partes: list[str] = []
    if reporte.get("altas"):
        partes.append("nuevas: " + ", ".join(a["nombre"] for a in reporte["altas"]))
    if reporte.get("renombres"):
        partes.append("renombradas: " + ", ".join(a["nombre"] for a in reporte["renombres"]))
    if reporte.get("reactivadas"):
        partes.append("de vuelta: " + ", ".join(a["nombre"] for a in reporte["reactivadas"]))
    if reporte.get("bajas"):
        partes.append("fuera: " + ", ".join(a["nombre"] for a in reporte["bajas"]))
    if reporte.get("sin_permiso"):
        partes.append("sin permiso de escritura: " + ", ".join(a["nombre"] for a in reporte["sin_permiso"]))
    if not partes:
        return ""
    return "Cambios en las agendas de Google. " + "; ".join(partes) + "."


def refrescar_agendas(forzar: bool = False) -> dict[str, Any] | None:
    """Relee calendarList y alinea la tabla `agendas`. Síncrona: se llama con to_thread.

    Devuelve el reporte de lo que cambió, o None si no hay multiagenda (prefijo vacío).
    Si la lectura o el guardado fallan, el reporte viene con `degradado` en True.
    Con la lectura caída o vacía no se da de baja a nadie: se marca `degradado` y se avisa
    una sola vez, igual que el bloqueo. Avisa al dueño cuando alguien entra, se renombra o
    sale de la lista, para que el catálogo no cambie sin que se entere.
    """
    if not multiagenda():
        return None
    ahora_m = _mono()
    if not forzar and not agendas_vencidas():
        return _AGENDAS["reporte"] or {}
    try:
        calendarios = _listar_calendarios()
    except (httpx.HTTPError, ValueError, KeyError, _SinAcceso) as e:
        _AGENDAS.update(
            fallo_ts=ahora_m,
            degradado=True,
            ultimo_error=_motivo(e),
        )
        log.warning("no se pudo leer la lista de calendarios (%s); se sigue con las agendas de SQLite", _AGENDAS["ultimo_error"])
        if not _AGENDAS["alertado"]:
            _AGENDAS["alertado"] = True
            avisos.notificar_alerta(
                "No pude leer la lista de calendarios de Google. Las agendas siguen como estaban; "
                "si alguien entra o sale del negocio, hay que darla de alta a mano. Revisa /health."
            )
        return _reporte_vacio()
    agendas = agendas_desde_calendarios(calendarios)
    try:
        reporte = store.agendas_sincronizar(agendas)
    except Exception as e:  # noqa: BLE001 — la base no debe tumbar el mensaje del cliente
        _AGENDAS.update(fallo_ts=ahora_m, degradado=True, ultimo_error=f"SQLite: {type(e).__name__}")
        log.warning("no se pudieron guardar las agendas (%s)", _AGENDAS["ultimo_error"])
        return _reporte_vacio()
    texto = _texto_cambios(reporte)
    if texto:
        avisos.notificar_alerta(texto)
    _AGENDAS.update(
        ok_ts=ahora_m,
        fallo_ts=None,
        degradado=bool(reporte.get("degradado")),
        ultimo_error="",
        reporte=reporte,
    )
    _AGENDAS["lecturas"] += 1
    if _AGENDAS["alertado"] and not _AGENDAS["degradado"]:
        _AGENDAS["alertado"] = False
        avisos.notificar_alerta("Google volvió a responder la lista de calendarios.")
    return reporte
