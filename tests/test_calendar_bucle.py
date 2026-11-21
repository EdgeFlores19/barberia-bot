"""Google se lee en un bucle de fondo, no en el turno de la clienta.

El bloqueo y el espejo de agendas son cachés: si están vencidos los refresca el bucle, así
que un mensaje (o una confirmación) nunca espera a Google. Medido: ~480 ms por vuelta en el
peor caso del plan free; con `forzar` en cada confirmación eso se pagaba justo al cerrar.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app import calendar_bucle, calendar_sync, config, engine, store

PREFIJO = "Barbería - "


@pytest.fixture(autouse=True)
def _bd_temporal(bd):
    """Cada prueba corre sobre su propia base: la de trabajo no se ensucia."""
    yield


@pytest.fixture()
def datos() -> dict:
    return json.loads(Path(config.DATA_FILE).read_text(encoding="utf-8"))


@pytest.fixture()
def google(monkeypatch):
    """Google falso: qué eventos devuelve cada calendario y qué se le pidió leer."""

    class Google:
        eventos: dict[str | None, list[dict]] = {}
        lecturas: list[str | None] = []
        falla: Exception | None = None
        calendarios: list[dict] = []

    g = Google()
    g.eventos, g.lecturas = {}, []
    monkeypatch.setattr(config, "CALENDAR_ID", "cal-secreto@example.com")
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "{}")
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO", True)
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", "")

    def listar(desde, hasta, calendar_id=None):
        g.lecturas.append(calendar_id)
        if g.falla:
            raise g.falla
        return list(g.eventos.get(calendar_id) or [])

    monkeypatch.setattr(calendar_sync, "_listar_eventos", listar)
    monkeypatch.setattr(calendar_sync, "_listar_calendarios", lambda: list(g.calendarios))
    monkeypatch.setattr(calendar_sync.avisos, "notificar_alerta", lambda _t: None)
    calendar_sync.reiniciar_bloqueos()
    calendar_sync.reiniciar_agendas()
    yield g
    calendar_sync.reiniciar_bloqueos()
    calendar_sync.reiniciar_agendas()


def _multi(monkeypatch, *nombres: str) -> list[str]:
    """Multiagenda con esas agendas dadas de alta; devuelve sus calendar_id."""
    monkeypatch.setattr(config, "CALENDAR_ID", "")
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", PREFIJO)
    ids = [f"{n.lower()}@group.calendar.google.com" for n in nombres]
    store.agendas_sincronizar(
        [{"calendar_id": i, "nombre": f"{PREFIJO}{n}"} for i, n in zip(ids, nombres)]
    )
    calendar_sync.reiniciar_bloqueos()
    calendar_sync.reiniciar_agendas()
    return ids


# ------------------------------------------------- el turno ya no toca la red
def test_el_mensaje_de_la_clienta_no_espera_a_google(datos, google):
    """El motor lee solo el caché: aunque esté vencido, contesta ya."""
    assert calendar_sync.bloqueo_vencido() is True

    engine.slots_disponibles(datos, "manicura")

    assert google.lecturas == []


def test_la_confirmacion_tampoco_espera_a_google(datos, google):
    """Cerrar la cita era el peor momento para una vuelta de ~480 ms a Google."""
    assert calendar_sync.bloqueo_vencido() is True

    engine.slots_disponibles(datos, "manicura")
    engine.slots_disponibles(datos, "manicura")

    assert google.lecturas == []


# ------------------------------------------------- qué hace una pasada del bucle
def test_el_bucle_lee_el_calendario_del_negocio(datos, google):
    calendar_bucle.sincronizar(datos)

    assert google.lecturas == [None]
    assert calendar_sync.bloqueo_vencido() is False


def test_el_bucle_no_repite_la_lectura_si_el_cache_esta_fresco(datos, google):
    calendar_bucle.sincronizar(datos)
    calendar_bucle.sincronizar(datos)

    assert google.lecturas == [None]


def test_el_bucle_recorre_cada_agenda_activa(datos, google, monkeypatch):
    ana, brenda = _multi(monkeypatch, "Ana", "Brenda")

    calendar_bucle.sincronizar(datos)

    assert sorted(google.lecturas) == sorted([ana, brenda])


def test_el_bucle_da_de_alta_las_agendas_nuevas_antes_de_leerlas(datos, google, monkeypatch):
    _multi(monkeypatch, "Ana")
    google.calendarios = [
        {"id": "nueva@group.calendar.google.com", "summary": f"{PREFIJO}Ceci", "accessRole": "writer"}
    ]

    calendar_bucle.sincronizar(datos)

    nombres = {a["nombre"] for a in store.agendas(solo_activas=True)}
    assert "Ceci" in nombres
    assert len(nombres) == 2
    assert "nueva@group.calendar.google.com" in google.lecturas


def test_un_fallo_de_google_no_tumba_el_bucle(datos, google):
    """Si el ciclo revienta, el proceso debe seguir vivo y reintentar luego."""
    google.falla = httpx.HTTPError("boom")

    calendar_bucle.sincronizar(datos)   # no lanza
    calendar_bucle.sincronizar(datos)

    assert google.lecturas == [None]    # el reintento respeta su periodo
    assert calendar_sync.salud()["bloqueo"]["caido"] is True


def test_sin_calendario_ni_agendas_el_bucle_no_llama_a_google(datos, google, monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_ID", "")

    calendar_bucle.sincronizar(datos)

    assert google.lecturas == []


def test_con_el_bloqueo_apagado_el_bucle_solo_atiende_el_espejo(datos, google, monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO", False)

    calendar_bucle.sincronizar(datos)

    assert google.lecturas == []
