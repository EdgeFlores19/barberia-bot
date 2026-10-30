"""La hora del negocio. Todo lo que necesite "ahora" pasa por aquí.

Render corre en UTC: usar datetime.now() directamente da una hora equivocada
en los slots, la anticipación mínima y la ventana de cancelación.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app import config


def ahora() -> datetime:
    """Hora local del negocio, sin tzinfo (el resto del código compara naive)."""
    zona = ZoneInfo(config.CALENDAR_TZ)
    return datetime.now(zona).replace(tzinfo=None)
