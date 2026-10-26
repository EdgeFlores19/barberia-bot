"""Almacenamiento de citas. F1: memoria del proceso. F2: SQLite (misma interfaz)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

_CITAS: dict[str, dict[str, Any]] = {}


def ocupados() -> set[str]:
    """Slots ISO ya tomados."""
    return {c["slot"] for c in _CITAS.values() if c.get("estado") == "confirmada"}


def crear(slot: str, servicio_id: str, nombre: str, telefono: str = "") -> dict[str, Any]:
    cita = {
        "id": f"cita_{len(_CITAS) + 1}",
        "slot": slot,
        "servicio_id": servicio_id,
        "nombre": nombre,
        "telefono": telefono,
        "estado": "confirmada",
        "creada": datetime.now().isoformat(timespec="seconds"),
    }
    _CITAS[cita["id"]] = cita
    return cita


def listar() -> list[dict[str, Any]]:
    return sorted(_CITAS.values(), key=lambda c: c["slot"])


def reset() -> None:
    _CITAS.clear()
