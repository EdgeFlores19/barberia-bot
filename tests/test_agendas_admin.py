"""GET /api/agendas: el estado de las agendas para la dueña.

Cerrado por ADMIN_TOKEN, sin `calendar_id` (la identidad de Google no sale del servidor)
y avisando de las citas que quedaron en una agenda dada de baja.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, store

ANA = "ana@group.calendar.google.com"
BRENDA = "brenda@group.calendar.google.com"
TOKEN = {"X-Admin-Token": "secreto-de-prueba"}
FUTURO = "2030-01-07T10:00"


@pytest.fixture()
def cliente(bd):
    # El import va aquí (después del monkeypatch de la fixture `bd`).
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def admin(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secreto-de-prueba")


def test_sin_admin_token_configurado_devuelve_503(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")

    assert cliente.get("/api/agendas").status_code == 503


def test_sin_cabecera_devuelve_401(cliente, admin):
    assert cliente.get("/api/agendas").status_code == 401


def test_lista_las_agendas_con_su_clave_y_sin_el_calendario(cliente, admin):
    store.agendas_sincronizar([{"calendar_id": ANA, "nombre": "Ana"}])

    r = cliente.get("/api/agendas", headers=TOKEN)

    assert r.status_code == 200
    agendas = r.json()["agendas"]
    assert [a["nombre"] for a in agendas] == ["Ana"]
    assert agendas[0]["clave"] == store.agenda_de(ANA)["clave"]
    assert agendas[0]["activa"] is True
    assert "calendar_id" not in agendas[0]
    assert ANA not in r.text


def test_cuenta_las_citas_futuras_de_cada_agenda(cliente, admin):
    store.agendas_sincronizar([
        {"calendar_id": ANA, "nombre": "Ana"},
        {"calendar_id": BRENDA, "nombre": "Brenda"},
    ])
    clave = store.agenda_de(ANA)["clave"]
    store.crear(slot=FUTURO, servicio_id="manicura", nombre="Clienta",
                agenda_id=clave, agenda_nombre="Ana")

    r = cliente.get("/api/agendas", headers=TOKEN)

    por_nombre = {a["nombre"]: a for a in r.json()["agendas"]}
    assert por_nombre["Ana"]["citas_futuras"] == 1
    assert por_nombre["Brenda"]["citas_futuras"] == 0


def test_una_agenda_dada_de_baja_con_citas_futuras_sale_en_huerfanas(cliente, admin):
    store.agendas_sincronizar([
        {"calendar_id": ANA, "nombre": "Ana"},
        {"calendar_id": BRENDA, "nombre": "Brenda"},
    ])
    clave = store.agenda_de(BRENDA)["clave"]
    cita = store.crear(slot=FUTURO, servicio_id="manicura", nombre="Clienta",
                       agenda_id=clave, agenda_nombre="Brenda")
    # Brenda desaparece del calendario: dos lecturas seguidas sin ella y se da de baja.
    for _ in range(store.AUSENCIAS_PARA_BAJA):
        store.agendas_sincronizar([{"calendar_id": ANA, "nombre": "Ana"}])

    huerfanas = store.citas_huerfanas()

    assert [h["folio"] for h in huerfanas] == [cita["folio"]]
    r = cliente.get("/api/agendas", headers=TOKEN)
    assert [h["folio"] for h in r.json()["huerfanas"]] == [cita["folio"]]
    assert "telefono" not in r.json()["huerfanas"][0]


def test_health_cuenta_las_agendas_sin_nombres_ni_ids(cliente, bd):
    """El panel público sólo lleva números: /health no es privado."""
    from app import calendar_sync

    store.agendas_sincronizar([
        {"calendar_id": ANA, "nombre": "Ana"},
        {"calendar_id": BRENDA, "nombre": "Brenda"},
    ])
    # Se simula la lectura fallida de Calendar: ese estado lo pone el descubrimiento.
    calendar_sync._AGENDAS["degradado"] = True
    try:
        h = cliente.get("/health").json()
        assert h["agendas"]["activas"] == 2
        assert h["agendas"]["total"] == 2
        assert h["agendas"]["huerfanas"] == 0
        assert h["agendas"]["degradado"] is True
        assert "Ana" not in str(h["agendas"])
        assert ANA not in str(h)
    finally:
        calendar_sync.reiniciar_agendas()


def test_una_cita_vieja_de_una_agenda_de_baja_no_es_huerfana(bd):
    """Lo pasado ya no se atiende: no ensucia la lista de pendientes."""
    store.agendas_sincronizar([
        {"calendar_id": ANA, "nombre": "Ana"},
        {"calendar_id": BRENDA, "nombre": "Brenda"},
    ])
    clave = store.agenda_de(BRENDA)["clave"]
    store.crear(slot="2020-01-07T10:00", servicio_id="manicura", nombre="Clienta",
                agenda_id=clave, agenda_nombre="Brenda", ahora="2020-01-01T08:00")
    for _ in range(store.AUSENCIAS_PARA_BAJA):
        store.agendas_sincronizar([{"calendar_id": ANA, "nombre": "Ana"}])

    assert store.citas_huerfanas() == []
