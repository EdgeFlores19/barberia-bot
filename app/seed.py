"""Citas de ejemplo para que la demo nunca arranque vacía.

En el plan free de Render el disco es efímero: cada vez que el servicio se
apaga, SQLite nace vacío. Sembrar un par de citas lejanas deja ver de
inmediato que la agenda "tiene vida" sin robar los horarios que el visitante
verá primero en el flujo de agendar.

Es idempotente: si ya hay cualquier cita, no toca nada.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from . import slots as horarios
from . import store

log = logging.getLogger("ambar.seed")

# (servicio_id, días de anticipación, nombre, teléfono)
PLAN = [
    ("manicura_pedicura", 3, "Luis Ramírez", "449 118 2043"),
    ("gelish", 5, "Ana Beltrán", "449 205 7712"),
]


def sembrar_si_vacio(datos: dict[str, Any], ahora: datetime | None = None) -> int:
    """Crea las citas de ejemplo que falten. Devuelve cuántas se crearon."""
    if store.listar():
        return 0

    ahora = ahora or datetime.now()
    creadas = 0
    for servicio_id, dias, nombre, telefono in PLAN:
        libres = horarios.generar_slots(
            datos, servicio_id, store.confirmadas(), ahora, limite=500
        )
        slot = _primer_hueco(libres, ahora, dias)
        if not slot:
            log.warning("sin horario para %s a %s días, se omite", servicio_id, dias)
            continue
        try:
            store.crear(slot, servicio_id, nombre, telefono)
        except store.SlotOcupado:
            log.warning("slot %s ya ocupado, se omite la semilla", slot)
            continue
        creadas += 1

    if creadas:
        log.info("sembradas %s citas de ejemplo", creadas)
    return creadas


def _primer_hueco(libres: list[str], ahora: datetime, dias: int) -> str | None:
    """Hueco del día pedido o del siguiente que abra (el domingo el estudio cierra)."""
    for salto in range(dias, dias + 8):
        objetivo = (ahora + timedelta(days=salto)).date().isoformat()
        del_dia = [s for s in libres if s.startswith(objetivo)]
        if del_dia:
            # Un poco después de la apertura: se ve natural y deja huecos tempranos libres.
            return del_dia[min(2, len(del_dia) - 1)]
    return None
