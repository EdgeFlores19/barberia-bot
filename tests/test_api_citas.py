"""GET /api/citas: cerrado por ADMIN_TOKEN y sin datos personales."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, store

from tests.telefonos import TEL_A


@pytest.fixture()
def bd(tmp_path, monkeypatch):
    """Base temporal sin seed. Sin esto, importar app.main siembra la BD real."""
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.db")
    monkeypatch.setattr(config, "SEED_DEMO", False)
    monkeypatch.setattr(store, "_inicializada", False)
    store.init()
    yield
    store.reset()


@pytest.fixture()
def cliente(bd):
    # El import va aquí (después del monkeypatch de la fixture `bd`).
    from app.main import app

    return TestClient(app)


def test_sin_admin_token_configurado_devuelve_503(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")

    assert cliente.get("/api/citas").status_code == 503


def test_sin_cabecera_devuelve_401(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secreto-de-prueba")

    assert cliente.get("/api/citas").status_code == 401


def test_cabecera_incorrecta_devuelve_401(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secreto-de-prueba")

    r = cliente.get("/api/citas", headers={"X-Admin-Token": "otro"})

    assert r.status_code == 401


def test_con_token_devuelve_citas_sin_datos_personales(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secreto-de-prueba")
    store.crear(slot="2026-11-03T10:00", servicio_id="manicura", nombre="Ana", telefono=TEL_A)

    r = cliente.get("/api/citas", headers={"X-Admin-Token": "secreto-de-prueba"})

    assert r.status_code == 200
    citas = r.json()["citas"]
    assert len(citas) == 1
    assert "telefono" not in citas[0]
    assert "calendar_event_id" not in citas[0]
