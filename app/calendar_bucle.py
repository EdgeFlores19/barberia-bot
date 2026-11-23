"""Calendar se lee en un bucle de fondo, nunca en el turno de la clienta.

Antes, cada mensaje —y sobre todo cada confirmación, con `forzar=True`— podía pagar una
vuelta completa a Google: ~480 ms en el peor caso del plan free, justo al cerrar la cita.
Ahora el motor lee solo el caché (SQLite + bloqueos en memoria) y este bucle lo mantiene
fresco: una pasada cada `CALENDAR_CICLO_S`, con el TTL y el reintento de cada calendario a
cargo de `calendar_sync`. Una clienta nunca espera a Google, y si Google cae el bot sigue
contestando con lo que ya sabía.

Síncrono a propósito: el bucle lo llama con `asyncio.to_thread` para no frenar el event loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import calendar_sync, config, reloj, store

log = logging.getLogger(__name__)


def _dias(datos: dict[str, Any]) -> int:
    try:
        return int(datos["reglas_agenda"].get("dias_adelante", 7))
    except (KeyError, TypeError, ValueError):
        return 7


def calendarios() -> list[str | None]:
    """Los calendarios que hay que refrescar.

    `[None]` es el calendario único de siempre (`GOOGLE_CALENDAR_ID`), el modo sin prefijo:
    así la instalación actual no cambia de comportamiento. Con multiagenda, un calendario por
    agenda activa; las agendas sin calendario propio se quedan fuera (no hay nada que leer).
    """
    if not calendar_sync.multiagenda():
        return [None] if config.CALENDAR_ID else []
    ids = [str(a.get("calendar_id") or "") for a in store.agendas(solo_activas=True)]
    return [i for i in ids if i]


def sincronizar(datos: dict[str, Any]) -> None:
    """Una pasada del bucle: agendas nuevas primero, después los bloqueos que toquen.

    No fuerza nada: `refrescar_bloqueos` respeta el TTL de cada calendario y su periodo de
    reintento tras un fallo, así que llamar a esto cada 30 s no multiplica las lecturas.
    """
    if not calendar_sync.necesita_bucle():
        return
    try:
        # Las agendas nuevas del grupo tienen que existir antes de pedir sus bloqueos.
        calendar_sync.refrescar_agendas()
    except Exception:  # noqa: BLE001 — sin lista de calendarios se sigue con las conocidas
        log.exception("no se pudo leer la lista de calendarios; sigo con las agendas guardadas")
    if not calendar_sync.bloqueo_activo():
        return
    dias = _dias(datos)
    for calendar_id in calendarios():
        try:
            calendar_sync.refrescar_bloqueos(reloj.ahora(), dias, False, calendar_id)
        except Exception:  # noqa: BLE001 — una agenda caída no deja sin bloqueos a las demás
            log.exception("fallo leyendo un calendario; se reintenta en el próximo ciclo")


async def bucle(datos: dict[str, Any]) -> None:
    """Refresca los calendarios cada `CALENDAR_CICLO_S` mientras viva la app."""
    while True:
        try:
            await asyncio.to_thread(sincronizar, datos)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — un ciclo roto no puede matar el bucle
            log.exception("calendario: falló el ciclo")
        await asyncio.sleep(config.CALENDAR_CICLO_S)
