"""Chat web: sid firmados, código de demo y rate limit por IP (vía HTTP)."""
from __future__ import annotations

import pytest

from app import acceso, config, engine

CODIGO = "codigo-de-prueba"


@pytest.fixture()
def cliente(bd, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(config, "DEMO_CODE", CODIGO)
    monkeypatch.setattr(config, "RATE_SESION_POR_MIN", 1000)
    monkeypatch.setattr(config, "RATE_MENSAJE_POR_MIN", 1000)
    acceso._REGISTRO.clear()
    return TestClient(app)


def _abrir(cliente) -> str:
    r = cliente.post("/api/sesion", json={"codigo": CODIGO})
    assert r.status_code == 200
    return r.json()["sid"]


def test_sesion_con_codigo_correcto_entrega_un_sid_firmado(cliente):
    sid = _abrir(cliente)

    assert acceso.sid_valido(sid)
    assert sid in engine.SESIONES


def test_sesion_con_codigo_incorrecto_es_401(cliente):
    r = cliente.post("/api/sesion", json={"codigo": "nope"})

    assert r.status_code == 401
    assert not engine.SESIONES


def test_mensaje_con_sid_inventado_es_401_y_no_crea_sesion(cliente):
    r = cliente.post("/api/mensaje", json={"sid": "web1", "texto": "hola", "codigo": CODIGO})

    assert r.status_code == 401
    assert "web1" not in engine.SESIONES


def test_mensaje_con_sid_firmado_funciona(cliente):
    sid = _abrir(cliente)

    r = cliente.post("/api/mensaje", json={"sid": sid, "texto": "precios", "codigo": CODIGO})

    assert r.status_code == 200
    assert r.json()["respuestas"]


def test_un_sid_firmado_sobrevive_a_que_el_servidor_olvide_la_sesion(cliente):
    sid = _abrir(cliente)
    engine.SESIONES.clear()                      # reinicio o desalojo

    r = cliente.post("/api/mensaje", json={"sid": sid, "texto": "precios", "codigo": CODIGO})

    assert r.status_code == 200


def test_el_tope_de_sesiones_desaloja_la_mas_vieja(cliente, monkeypatch):
    monkeypatch.setattr(config, "MAX_SESIONES_WEB", 3)

    sids = [_abrir(cliente) for _ in range(5)]

    assert len(engine.SESIONES) <= 3
    assert sids[-1] in engine.SESIONES


def test_rate_limit_de_sesiones_por_ip(cliente, monkeypatch):
    monkeypatch.setattr(config, "RATE_SESION_POR_MIN", 2)

    codigos = [cliente.post("/api/sesion", json={"codigo": CODIGO}).status_code for _ in range(3)]

    assert codigos == [200, 200, 429]


def test_rate_limit_de_mensajes_por_ip(cliente, monkeypatch):
    sid = _abrir(cliente)
    monkeypatch.setattr(config, "RATE_MENSAJE_POR_MIN", 2)

    codigos = [
        cliente.post("/api/mensaje", json={"sid": sid, "texto": "precios", "codigo": CODIGO}).status_code
        for _ in range(3)
    ]

    assert codigos == [200, 200, 429]
