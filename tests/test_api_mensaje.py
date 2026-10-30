"""POST /api/mensaje: el chat web no puede suplantar sesiones de WhatsApp."""
from __future__ import annotations

from app import config, engine

from tests.telefonos import TEL_A


def _cliente():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_web_no_puede_usar_un_sid_de_whatsapp(bd, monkeypatch):
    monkeypatch.setattr(config, "DEMO_CODE", "")

    r = _cliente().post("/api/mensaje", json={"sid": f"wa:{TEL_A}", "texto": "hola"})

    assert r.status_code == 400
    assert f"wa:{TEL_A}" not in engine.SESIONES


def test_texto_demasiado_largo_se_rechaza(bd, monkeypatch):
    monkeypatch.setattr(config, "DEMO_CODE", "")

    r = _cliente().post("/api/mensaje", json={"sid": "web1", "texto": "x" * 501})

    assert r.status_code == 422
