"""Google Calendar opcional: espejo de las citas y, en sentido contrario, bloqueo de huecos.

Si falta GOOGLE_CALENDAR_ID, el token o la red, el bot agenda igual: SQLite es la fuente
de verdad. Calendar hace dos cosas, y ninguna puede tumbar una reserva:
  1. Espejo (bot -> Calendar): cada cita se copia como evento para verla en el teléfono.
  2. Bloqueo (Calendar -> bot): lo que la manicurista anota a mano ahí ocupa ese horario
     y el bot no lo ofrece. Se lee con events.list y se guarda en un caché corto; si la
     lectura falla, el bot sigue con lo que sabe y avisa una vez (ver ADR 0007).
Se usa REST directo (httpx) para no arrastrar google-api-python-client.

Variables: GOOGLE_TOKEN_JSON, GOOGLE_CALENDAR_ID, CALENDAR_TZ, CALENDAR_BLOQUEO,
CALENDAR_BLOQUEO_TTL_S, CALENDAR_BLOQUEO_REINTENTO_S.
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

from . import avisos, config

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


_BLOQ: dict[str, Any] = _bloqueo_inicial()


def _anotar(motivo: str) -> None:
    _ESTADO["ultimo_error"] = motivo[:300]


def habilitado() -> bool:
    return bool(config.CALENDAR_ID and config.CALENDAR_TOKEN_FILE)


def salud() -> dict[str, Any]:
    """Estado de la integración para /health. Nunca expone el contenido del token."""
    t = _leer_token()
    if not config.CALENDAR_ID:
        motivo = "falta GOOGLE_CALENDAR_ID"
    elif not config.CALENDAR_TOKEN_FILE:
        motivo = "falta GOOGLE_TOKEN_JSON"
    elif not t:
        motivo = "el token no se pudo leer (archivo inexistente, sin permisos o JSON inválido)"
    elif not t.get("refresh_token"):
        motivo = "el token no tiene refresh_token"
    else:
        motivo = ""
    en_variable = config.CALENDAR_TOKEN_FILE.strip().startswith("{")
    return {
        "habilitado": habilitado(),
        "calendar_id": config.CALENDAR_ID or "(vacío)",
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


def _url(event_id: str = "") -> str:
    base = f"https://www.googleapis.com/calendar/v3/calendars/{config.CALENDAR_ID}/events"
    return f"{base}/{event_id}" if event_id else base


def crear(cita: dict[str, Any], datos: dict[str, Any]) -> str | None:
    """Crea el evento espejo. Devuelve el id o None si Calendar no está disponible."""
    if not habilitado():
        _anotar("Calendar deshabilitado (revisa GOOGLE_CALENDAR_ID / GOOGLE_TOKEN_JSON)")
        return None
    cab = _cabeceras()
    if not cab:
        _anotar("sin access token: no se pudo leer el archivo ni refrescar")
        return None
    _ESTADO["intentos"] += 1
    try:
        r = httpx.post(_url(), json=_evento(cita, datos), headers=cab, timeout=config.CALENDAR_TIMEOUT_S)
        r.raise_for_status()
        _ESTADO["ok"] += 1
        _anotar("")
        return r.json().get("id")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("no se creó el evento de %s: %s", cita.get("folio"), e)
        _anotar(f"crear {cita.get('folio')}: {e}")
        return None


def mover(event_id: str | None, cita: dict[str, Any], datos: dict[str, Any]) -> str | None:
    """Mueve el evento existente; si ya no existe, lo vuelve a crear."""
    if not habilitado():
        _anotar("Calendar deshabilitado (revisa GOOGLE_CALENDAR_ID / GOOGLE_TOKEN_JSON)")
        return None
    cab = _cabeceras()
    if not cab:
        _anotar("sin access token: no se pudo leer el archivo ni refrescar")
        return None
    if not event_id:
        return crear(cita, datos)
    try:
        r = httpx.patch(
            _url(event_id), json=_evento(cita, datos), headers=cab, timeout=config.CALENDAR_TIMEOUT_S
        )
        if r.status_code == 404:
            return crear(cita, datos)
        r.raise_for_status()
        return event_id
    except (httpx.HTTPError, ValueError) as e:
        log.warning("no se movió el evento de %s: %s", cita.get("folio"), e)
        _anotar(f"mover {cita.get('folio')}: {e}")
        return event_id


def borrar(event_id: str | None) -> bool:
    """Borra el evento espejo. 404/410 cuentan como borrado."""
    if not habilitado() or not event_id:
        return False
    cab = _cabeceras()
    if not cab:
        return False
    try:
        r = httpx.delete(_url(event_id), headers=cab, timeout=config.CALENDAR_TIMEOUT_S)
        return r.status_code in (200, 204, 404, 410)
    except httpx.HTTPError as e:
        log.warning("no se borró el evento %s: %s", event_id, e)
        _anotar(f"borrar {event_id}: {e}")
        return False


# ---------------------------------------------------------------- bloqueo (Calendar -> bot)
def bloqueo_activo() -> bool:
    """¿Se leen los eventos de Calendar para bloquear huecos?"""
    return habilitado() and config.CALENDAR_BLOQUEO


def reiniciar_bloqueos() -> None:
    """Vacía el caché y el estado del bloqueo (pruebas)."""
    _BLOQ.clear()
    _BLOQ.update(_bloqueo_inicial())


def bloqueo_vencido() -> bool:
    """¿Toca volver a leer? Solo mira el reloj: no hace red."""
    ok = _BLOQ["ok_ts"]
    return ok is None or _mono() - ok >= config.CALENDAR_BLOQUEO_TTL_S


def _salud_bloqueo() -> dict[str, Any]:
    ok = _BLOQ["ok_ts"]
    return {
        "activo": bloqueo_activo(),
        "intervalos": len(_BLOQ["intervalos"]),
        "lecturas": _BLOQ["lecturas"],
        "edad_s": None if ok is None else round(_mono() - ok),
        "caido": _BLOQ["caido"],
        "ultimo_error": _BLOQ["ultimo_error"],
    }


def bloqueos() -> list[dict[str, Any]]:
    """Lo que hay en el caché como citas ocupadas para app/slots.py. No hace red."""
    if not bloqueo_activo():
        return []
    return [
        {
            "slot": ini.isoformat(timespec="minutes"),
            "fin": fin.isoformat(timespec="minutes"),
            "estado": "confirmada",
            "bloqueo_externo": True,
        }
        for ini, fin in _BLOQ["intervalos"]
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


def _listar_eventos(desde: datetime, hasta: datetime) -> list[dict[str, Any]]:
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
        r = httpx.get(_url(), params=params, headers=cab, timeout=config.CALENDAR_TIMEOUT_S)
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


def refrescar_bloqueos(ahora: datetime, dias: int, forzar: bool = False) -> bool:
    """Relee Calendar y actualiza el caché. Síncrona: se llama con asyncio.to_thread.

    `forzar` se salta el TTL (se usa al confirmar una cita), pero no el periodo de
    reintento tras un fallo: con Calendar caído no se espera el timeout en cada mensaje.
    Devuelve True si el caché quedó vigente.
    """
    if not bloqueo_activo():
        return False
    ahora_m = _mono()
    if _BLOQ["fallo_ts"] is not None and ahora_m - _BLOQ["fallo_ts"] < config.CALENDAR_BLOQUEO_REINTENTO_S:
        return False
    if not forzar and not bloqueo_vencido():
        return True
    tz = ZoneInfo(config.CALENDAR_TZ)
    try:
        eventos = _listar_eventos(ahora, datetime.combine(ahora.date() + timedelta(days=dias + 1), datetime.min.time()))
        intervalos = [i for i in (_intervalo_evento(e, tz) for e in eventos) if i]
    except (httpx.HTTPError, ValueError, KeyError, _SinAcceso) as e:
        _BLOQ["fallo_ts"] = ahora_m
        _BLOQ["caido"] = True
        _BLOQ["ultimo_error"] = _motivo(e)
        log.warning("no se pudo leer Calendar (%s); se agenda con lo que hay en SQLite", _BLOQ["ultimo_error"])
        if not _BLOQ["alertado"]:
            _BLOQ["alertado"] = True
            avisos.notificar_alerta(
                "No pude leer tu Google Calendar. Mientras tanto el bot no sabe de los eventos "
                "que anotes a mano y podría ofrecer un horario ocupado. Revisa /health."
            )
        return False
    _BLOQ.update(intervalos=intervalos, ok_ts=ahora_m, fallo_ts=None, caido=False, ultimo_error="")
    _BLOQ["lecturas"] += 1
    if _BLOQ["alertado"]:
        _BLOQ["alertado"] = False
        avisos.notificar_alerta("Google Calendar volvió a responder: los eventos manuales vuelven a bloquear horarios.")
    return True
