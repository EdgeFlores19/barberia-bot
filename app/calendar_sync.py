"""Espejo opcional en Google Calendar. Nunca bloquea una reserva.

Si falta GOOGLE_CALENDAR_ID, el token o la red, el bot agenda igual: SQLite es
la fuente de verdad y Calendar es solo una copia para que la manicurista lo vea en
su teléfono. Se usa REST directo (httpx) para no arrastrar google-api-python-client.

Variables: GOOGLE_TOKEN_JSON, GOOGLE_CALENDAR_ID, CALENDAR_TZ.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from . import config

log = logging.getLogger("ambar.calendar")

_TOKEN: dict[str, Any] = {"access": "", "expira": 0.0}

# Diagnóstico sin secretos: por qué no se está agendando. Sale en /health.
_ESTADO: dict[str, Any] = {"intentos": 0, "ok": 0, "ultimo_error": ""}


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
        "description": f"Folio {cita['folio']}. Reservado por el bot de {datos['nombre']}.",
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
