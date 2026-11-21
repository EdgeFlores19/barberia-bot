"""Con multiagenda, el ID del calendario se descubre y no está en el entorno.

`habilitado()` no puede seguir exigiendo `GOOGLE_CALENDAR_ID`: si el dueño configura el
prefijo y Google devuelve sus agendas, el espejo y los bloqueos tienen que funcionar aunque
esa variable esté vacía. Sin token, o sin nada que leer, se queda apagado como siempre.
"""
from __future__ import annotations

import pytest

from app import calendar_sync, config, store

PREFIJO = "Barbería - "


@pytest.fixture()
def multi(bd, monkeypatch):
    """Multiagenda configurada: prefijo, token legible y sin calendario único."""
    monkeypatch.setattr(config, "CALENDAR_ID", "")
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "{}")
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", PREFIJO)
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "tok")
    calendar_sync.reiniciar_agendas()
    calendar_sync.reiniciar_bloqueos()
    yield
    calendar_sync.reiniciar_agendas()
    calendar_sync.reiniciar_bloqueos()


def _alta(nombre: str, calendar_id: str) -> None:
    store.agendas_sincronizar([{"calendar_id": calendar_id, "nombre": f"{PREFIJO}{nombre}"}])


def test_sin_calendario_unico_pero_con_agendas_sigue_habilitado(multi):
    """El caso que rompía: prefijo configurado, agendas descubiertas, CALENDAR_ID vacío."""
    assert calendar_sync.habilitado() is False
    _alta("Ana", "ana@group.calendar.google.com")
    assert calendar_sync.habilitado() is True
    assert calendar_sync.multiagenda() is True


def test_con_agendas_el_bloqueo_tambien_sigue_encendido(multi, monkeypatch):
    """Si el espejo funciona, el bloqueo no puede quedarse apagado por la misma razón."""
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO", True)
    _alta("Ana", "ana@group.calendar.google.com")
    assert calendar_sync.bloqueo_activo() is True


def test_sin_agendas_y_sin_calendario_unico_queda_apagado(multi):
    """Nada que leer: no se anuncia como habilitado."""
    assert calendar_sync.habilitado() is False
    assert calendar_sync.multiagenda() is False


def test_sin_token_no_hay_forma_de_hablar_con_google(multi, monkeypatch):
    """El token manda: sin él, ni con agendas dadas de alta."""
    _alta("Ana", "ana@group.calendar.google.com")
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "")
    assert calendar_sync.habilitado() is False


def test_sin_prefijo_las_agendas_viejas_no_encienden_el_espejo(multi, monkeypatch):
    """Sin prefijo vuelve a ser el calendario único; con CALENDAR_ID vacío, apagado."""
    _alta("Ana", "ana@group.calendar.google.com")
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", "")
    assert calendar_sync.habilitado() is False
