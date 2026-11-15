"""GET /api/depositos: el concentrado que la dueña cuadra a mano contra su banco.

A diferencia de /api/citas, esta ruta SÍ devuelve teléfono y monto: es exactamente lo que
le sirve para reconocer el depósito. Sigue cerrada por ADMIN_TOKEN.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, reloj, store

from tests.telefonos import TEL_A

TOKEN = {"X-Admin-Token": "secreto-de-prueba"}


@pytest.fixture()
def cliente(bd, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secreto-de-prueba")
    from app.main import app

    return TestClient(app)


def _cita_con_comprobante(folio: str = "AMB-AAAA", fecha: str = "2026-11-15T18:42:05", **kw) -> dict:
    """Aparta (pendiente), luego registra el comprobante: así queda con dinero y confirmada."""
    c = store.crear(slot="2026-11-16T10:00", servicio_id="manicura", nombre="Ana",
                    telefono=TEL_A, pendiente_hasta="2026-11-15T23:59:59", anticipo=200, **kw)
    return store.registrar_comprobante(c["folio"], "image", "media-1", ahora=fecha)


def test_sin_admin_token_configurado_devuelve_503(bd, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")
    from app.main import app

    assert TestClient(app).get("/api/depositos").status_code == 503


def test_sin_cabecera_devuelve_401(cliente):
    assert cliente.get("/api/depositos").status_code == 401


def test_token_incorrecto_devuelve_401(cliente):
    assert cliente.get("/api/depositos", headers={"X-Admin-Token": "otro"}).status_code == 401


def test_devuelve_telefono_y_monto(cliente):
    _cita_con_comprobante()

    r = cliente.get("/api/depositos", params={"desde": "2026-11-15", "hasta": "2026-11-15"}, headers=TOKEN)

    assert r.status_code == 200
    fila = r.json()["filas"][0]
    assert fila["telefono"] and fila["anticipo"] > 0
    assert fila["folio"] and fila["nombre"] == "Ana"


def test_totales_en_la_respuesta(cliente):
    _cita_con_comprobante()

    r = cliente.get("/api/depositos", params={"desde": "2026-11-15"}, headers=TOKEN).json()

    assert r["n"] == 1 and r["total_anticipo"] == 200


def test_por_defecto_es_hoy(cliente):
    """Sin fechas: el día del negocio (America/Mexico_City), no UTC. El depósito de ahora cuenta."""
    _cita_con_comprobante(fecha=reloj.ahora().isoformat(timespec="seconds"))

    r = cliente.get("/api/depositos", headers=TOKEN).json()

    assert r["desde"] == r["hasta"] == reloj.ahora().date().isoformat()
    assert r["n"] == 1


def test_formato_texto_es_lo_que_se_manda_por_telegram(cliente):
    c = _cita_con_comprobante()

    r = cliente.get("/api/depositos", params={"desde": "2026-11-15", "formato": "texto"}, headers=TOKEN)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "Concentrado" in r.text and c["folio"] in r.text


def test_fecha_invalida_devuelve_422(cliente):
    assert cliente.get("/api/depositos", params={"desde": "ayer"}, headers=TOKEN).status_code == 422


def test_rango_al_reves_devuelve_422(cliente):
    r = cliente.get("/api/depositos", params={"desde": "2026-11-16", "hasta": "2026-11-15"}, headers=TOKEN)

    assert r.status_code == 422


def test_enviar_sin_telegram_configurado_avisa_y_no_finge(cliente, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")

    r = cliente.post("/api/depositos/enviar", params={"desde": "2026-11-15"}, headers=TOKEN)

    assert r.status_code == 503
    assert "telegram" in r.json()["detail"].lower()


def test_enviar_manda_el_concentrado(cliente, monkeypatch):
    enviados: list[str] = []
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token-de-prueba")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr("app.avisos._telegram", lambda texto: enviados.append(texto) or True)
    c = _cita_con_comprobante()

    r = cliente.post("/api/depositos/enviar", params={"desde": "2026-11-15"}, headers=TOKEN)

    assert r.status_code == 200 and r.json()["enviado"] is True
    assert c["folio"] in enviados[0]
