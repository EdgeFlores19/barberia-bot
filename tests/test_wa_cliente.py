"""Tests del cliente de Graph API: no deben tocar la red real."""
from __future__ import annotations

import asyncio

from app import config, wa_cliente

from tests.telefonos import TEL_A, TEL_A_SIN_1


class _ClienteFalso:
    """Doble de httpx.AsyncClient con status_code/text configurables."""

    llamado: tuple | None = None
    status_code = 200
    text = "{}"

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        _ClienteFalso.llamado = (url, json, headers)

        class _R:
            status_code = _ClienteFalso.status_code
            text = _ClienteFalso.text

        return _R()


def _usar_meta(monkeypatch, status_code: int = 200, text: str = "{}") -> None:
    monkeypatch.setattr(config, "WA_MODO", "graph")
    monkeypatch.setattr(config, "WA_TOKEN", "T0KEN")
    monkeypatch.setattr(config, "WA_PHONE_ID", "123456")
    monkeypatch.setattr(wa_cliente.httpx, "AsyncClient", _ClienteFalso)
    monkeypatch.setattr(_ClienteFalso, "status_code", status_code)
    monkeypatch.setattr(_ClienteFalso, "text", text)


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
    _usar_meta(monkeypatch)

    ok = asyncio.run(
        wa_cliente.enviar("5215500000001", {"type": "text", "text": {"body": "hola"}})
    )

    assert ok is True
    url, cuerpo, cabeceras = _ClienteFalso.llamado
    assert url == f"https://graph.facebook.com/{config.WA_API_VERSION}/123456/messages"
    assert cuerpo["messaging_product"] == "whatsapp"
    assert cuerpo["to"] == "525500000001"
    assert cabeceras["Authorization"] == "Bearer T0KEN"


def test_envia_con_la_grafia_canonica_de_los_telefonos_de_prueba(monkeypatch):
    _usar_meta(monkeypatch)

    asyncio.run(wa_cliente.enviar(TEL_A, {"type": "text", "text": {"body": "hola"}}))

    _, cuerpo, _ = _ClienteFalso.llamado
    assert cuerpo["to"] == TEL_A_SIN_1


def test_un_400_con_code_de_meta_se_registra(monkeypatch):
    _usar_meta(monkeypatch, status_code=400, text='{"error": {"code": 131030}}')

    ok = asyncio.run(
        wa_cliente.enviar(TEL_A, {"type": "text", "text": {"body": "hola"}})
    )

    assert ok is False
    assert wa_cliente.ultimo_codigo() == 131030


def test_un_envio_exitoso_reinicia_el_codigo(monkeypatch):
    _usar_meta(monkeypatch, status_code=400, text='{"error": {"code": 131030}}')
    asyncio.run(wa_cliente.enviar(TEL_A, {"type": "text", "text": {"body": "hola"}}))
    assert wa_cliente.ultimo_codigo() == 131030

    monkeypatch.setattr(_ClienteFalso, "status_code", 200)
    monkeypatch.setattr(_ClienteFalso, "text", "{}")
    ok = asyncio.run(
        wa_cliente.enviar(TEL_A, {"type": "text", "text": {"body": "hola"}})
    )

    assert ok is True
    assert wa_cliente.ultimo_codigo() == 0


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
