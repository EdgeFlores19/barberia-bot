"""Disponibilidad por agenda y regla para asignar "cualquiera disponible" (M8).

Cada agenda es un calendario con su propio horario ocupado: este módulo junta lo que
está libre en cada una y decide a quién mandar la cita sin preguntar a la clienta.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from app import calendar_sync, slots, store

Lectura = Callable[[str], list[dict[str, Any]]]


def _ocupados_por_defecto(clave: str) -> list[dict[str, Any]]:
    """Citas de esa agenda que consumen horario. Sin red: solo base de datos."""
    return store.confirmadas(agenda_id=clave)


def _bloqueos_por_defecto(clave: str) -> list[dict[str, Any]]:
    """Eventos manuales del calendario de esa agenda. Sin red: caché del bucle."""
    return calendar_sync.bloqueos(clave)


def unir_horarios(listas: Iterable[Iterable[str]]) -> list[str]:
    """Unión de horarios sin repetidos, en orden cronológico."""
    vistos: set[str] = set()
    for lista in listas:
        vistos.update(lista)
    return sorted(vistos)


def _libres(
    datos: dict[str, Any],
    servicio_id: str,
    agenda: dict[str, Any],
    ahora: datetime,
    ocupados_por: Lectura,
    bloqueos_por: Lectura,
    limite: int,
) -> list[str]:
    """Lo que queda libre en una agenda: sus citas más sus bloqueos externos."""
    clave = str(agenda.get("clave") or "")
    citas = [*ocupados_por(clave), *bloqueos_por(clave)]
    return slots.generar_slots(datos, servicio_id, citas, ahora, limite=limite)


def candidatas(
    datos: dict[str, Any],
    servicio_id: str,
    agendas: list[dict[str, Any]],
    ahora: datetime,
    *,
    ocupados_por: Lectura | None = None,
    bloqueos_por: Lectura | None = None,
    limite: int = 24,
) -> dict[str, list[str]]:
    """Horarios libres por agenda activa, en el orden en que se presentan."""
    ocupados_por = ocupados_por or _ocupados_por_defecto
    bloqueos_por = bloqueos_por or _bloqueos_por_defecto
    return {
        str(agenda["clave"]): _libres(
            datos, servicio_id, agenda, ahora, ocupados_por, bloqueos_por, limite
        )
        for agenda in agendas
        if agenda.get("activa", True)
    }


def horarios_libres(
    datos: dict[str, Any],
    servicio_id: str,
    agendas: list[dict[str, Any]],
    ahora: datetime,
    *,
    ocupados_por: Lectura | None = None,
    bloqueos_por: Lectura | None = None,
    limite: int = 24,
) -> list[str]:
    """Los horarios donde al menos una agenda puede tomar a la clienta."""
    por_agenda = candidatas(
        datos,
        servicio_id,
        agendas,
        ahora,
        ocupados_por=ocupados_por,
        bloqueos_por=bloqueos_por,
        limite=limite,
    )
    return unir_horarios(por_agenda.values())[:limite]


def elegir(
    horario: str,
    por_agenda: dict[str, list[str]],
    citas: list[dict[str, Any]],
    agendas: list[dict[str, Any]],
    telefono: str = "",
) -> str | None:
    """La agenda para ese horario, o None si nadie puede.

    Regla: quien ya la atendió la última vez, luego quien tenga menos citas ese día y,
    si sigue el empate, la de menor `orden`. Desempate final por clave: es determinista.
    """
    libres = [clave for clave, horarios in por_agenda.items() if horario in horarios]
    if not libres:
        return None
    if len(libres) == 1:
        return libres[0]

    orden = {str(a.get("clave")): int(a.get("orden") or 0) for a in agendas}
    clave_de = {str(a.get("calendar_id")): str(a.get("clave")) for a in agendas}
    # `citas.agenda_id` guarda la CLAVE (así la escribe el motor); se acepta también el id del calendario.
    clave_de.update({clave: clave for clave in orden})
    # Lo cancelado o expirado ya no ocupa a nadie: ni pesa como carga ni cuenta como "la última vez".
    citas = [c for c in citas if c.get("estado") not in ("cancelada", "expirada")]

    if telefono:
        previas = [
            c for c in citas
            if c.get("telefono") == telefono and str(c.get("agenda_id") or "") in clave_de
        ]
        if previas:
            ultima = clave_de[str(max(previas, key=lambda c: str(c.get("slot") or ""))["agenda_id"])]
            if ultima in libres:
                return ultima

    dia = horario[:10]
    carga = Counter(
        clave_de[str(c["agenda_id"])]
        for c in citas
        if str(c.get("agenda_id") or "") in clave_de and str(c.get("slot") or "")[:10] == dia
    )
    return min(libres, key=lambda clave: (carga[clave], orden.get(clave, 0), clave))
