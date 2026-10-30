"""La hora del negocio: un solo lugar para "ahora"."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import config, reloj


def test_ahora_usa_la_zona_configurada(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TZ", "UTC")
    utc = reloj.ahora()

    monkeypatch.setattr(config, "CALENDAR_TZ", "Asia/Tokyo")
    tokio = reloj.ahora()

    assert abs((tokio - utc) - timedelta(hours=9)) < timedelta(seconds=2)


def test_ahora_devuelve_naive_en_hora_local(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TZ", "America/Mexico_City")
    a = reloj.ahora()
    referencia = datetime.now(ZoneInfo("America/Mexico_City")).replace(tzinfo=None)

    assert a.tzinfo is None
    assert abs(a - referencia) < timedelta(seconds=2)


def test_zona_invalida_falla_con_nombre_claro(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TZ", "Marte/Olympus")

    with pytest.raises(Exception) as exc:
        reloj.ahora()

    assert "Marte/Olympus" in str(exc.value)
