"""Tests del cliente de Graph API: no deben tocar la red real."""
from __future__ import annotations

import asyncio

from app import config, wa_cliente


class _ClienteFalso:
    llamado: tuple | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        _ClienteFalso.llamado = (url, json, headers)

        class _R:
            status_code = 200
            text = "{}"

        return _R()


def test_modo_consola_no_llama_a_la_red(monkeypatch, capsys):
    monkeypatch.setattr(config, "WA_MODO", "consola")

    def _explota(*args, **kwargs):
        raise AssertionError("en modo consola no se debe abrir un cliente HTTP")

    monkeypatch.setattr(wa_cliente.httpx, "AsyncClient", _explota)
    ok = asyncio.run(
        wa_cliente.enviar("5215500000001", {"type": "text", "text": {"body": "hola"}})
    )
    assert ok is True
    assert "[wa:consola]" in capsys.readouterr().out


def test_envia_a_graph_con_telefono_y_token(monkeypatch):
    monkeypatch.setattr(config, "WA_MODO", "graph")
    monkeypatch.setattr(config, "WA_TOKEN", "T0KEN")
    monkeypatch.setattr(config, "WA_PHONE_ID", "123456")
    monkeypatch.setattr(wa_cliente.httpx, "AsyncClient", _ClienteFalso)

    ok = asyncio.run(
        wa_cliente.enviar("5215500000001", {"type": "text", "text": {"body": "hola"}})
    )

    assert ok is True
    url, cuerpo, cabeceras = _ClienteFalso.llamado
    assert url == f"https://graph.facebook.com/{config.WA_API_VERSION}/123456/messages"
    assert cuerpo["messaging_product"] == "whatsapp"
    assert cuerpo["to"] == "5215500000001"
    assert cabeceras["Authorization"] == "Bearer T0KEN"


def test_sin_token_no_intenta_enviar(monkeypatch):
    monkeypatch.setattr(config, "WA_MODO", "graph")
    monkeypatch.setattr(config, "WA_TOKEN", "")
    monkeypatch.setattr(config, "WA_PHONE_ID", "")
    assert (
        asyncio.run(
            wa_cliente.enviar("5215500000001", {"type": "text", "text": {"body": "x"}})
        )
        is False
    )
