"""Tests del webhook: firma, extracción de mensajes y procesamiento."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pytest

from app import config, engine, store, whatsapp_webhook


def _firmar(cuerpo: bytes, secreto: str = "s3cr3t") -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def _payload(texto: str = "precios", msg_id: str = "wamid.1", telefono: str = "5215500000001") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "0",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "messages": [{
                        "id": msg_id,
                        "from": telefono,
                        "timestamp": "1793000000",
                        "type": "text",
                        "text": {"body": texto},
                    }],
                },
            }],
        }],
    }


def test_firma_valida_acepta_la_correcta(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")
    cuerpo = b'{"a":1}'
    assert whatsapp_webhook.firma_valida(cuerpo, _firmar(cuerpo)) is True


def test_firma_valida_rechaza_firma_ajena(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")
    cuerpo = b'{"a":1}'
    assert whatsapp_webhook.firma_valida(cuerpo, _firmar(cuerpo, "otro")) is False


def test_firma_valida_rechaza_cabecera_vacia(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")
    assert whatsapp_webhook.firma_valida(b'{"a":1}', "") is False


def test_firma_valida_rechaza_sin_secreto(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "")
    cuerpo = b'{"a":1}'
    assert whatsapp_webhook.firma_valida(cuerpo, _firmar(cuerpo)) is False


def test_firma_con_caracteres_no_ascii_es_invalida(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")

    assert whatsapp_webhook.firma_valida(b"{}", "sha256=ñandú") is False


def test_extrae_mensaje_de_texto():
    msgs = whatsapp_webhook.extraer_mensajes(_payload("hola"))
    assert msgs == [{"id": "wamid.1", "wa_id": "5215500000001", "texto": "hola", "tipo": "text"}]


def test_extrae_la_opcion_elegida_en_una_lista():
    p = _payload()
    p["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "id": "wamid.2",
        "from": "5215500000001",
        "type": "interactive",
        "interactive": {"type": "list_reply", "list_reply": {"id": "agendar", "title": "Agendar cita"}},
    }
    assert whatsapp_webhook.extraer_mensajes(p)[0]["texto"] == "agendar"


def test_ignora_confirmaciones_de_entrega():
    p = _payload()
    del p["entry"][0]["changes"][0]["value"]["messages"]
    p["entry"][0]["changes"][0]["value"]["statuses"] = [{"id": "wamid.1", "status": "delivered"}]
    assert whatsapp_webhook.extraer_mensajes(p) == []
    # reaction, unsupported y system se ignoran en silencio: no generan respuesta.
    p["entry"][0]["changes"][0]["value"]["messages"] = [
        {"id": "wamid.4", "from": "5215500000001", "type": "reaction",
         "reaction": {"message_id": "wamid.1", "emoji": "\U0001f44d"}},
        {"id": "wamid.5", "from": "5215500000001", "type": "unsupported"},
        {"id": "wamid.6", "from": "5215500000001", "type": "system"},
    ]
    assert whatsapp_webhook.extraer_mensajes(p) == []


def test_mensaje_de_imagen_llega_con_texto_vacio():
    p = _payload()
    p["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "id": "wamid.3", "from": "5215500000001", "type": "image", "image": {"id": "X"},
    }
    msgs = whatsapp_webhook.extraer_mensajes(p)
    assert msgs[0]["tipo"] == "image"
    assert msgs[0]["texto"] == ""


class _Enviador:
    def __init__(self):
        self.salida: list[tuple[str, dict]] = []

    async def __call__(self, wa_id: str, payload: dict) -> bool:
        self.salida.append((wa_id, payload))
        return True


def _cuerpo(wa_id: str, payload: dict) -> str:
    if payload["type"] == "text":
        return payload["text"]["body"]
    return json.dumps(payload["interactive"], ensure_ascii=False)


def test_procesar_responde_faq_y_manda_al_telefono(bd):
    enviador = _Enviador()
    n = asyncio.run(whatsapp_webhook.procesar_evento(_payload("precios"), enviador))
    assert n == 1
    wa_id, payload = enviador.salida[0]
    assert wa_id == "5215500000001"
    assert "450" in _cuerpo(wa_id, payload)


def test_procesar_usa_una_sesion_por_telefono(bd):
    enviador = _Enviador()
    asyncio.run(whatsapp_webhook.procesar_evento(_payload("hola", "wamid.a"), enviador))
    asyncio.run(whatsapp_webhook.procesar_evento(_payload("precios", "wamid.b"), enviador))
    assert list(engine.SESIONES) == ["wa:5215500000001"]


def test_procesar_ignora_el_mensaje_repetido(bd):
    enviador = _Enviador()
    primero = asyncio.run(whatsapp_webhook.procesar_evento(_payload("precios", "wamid.x"), enviador))
    enviados_tras_el_primero = len(enviador.salida)
    segundo = asyncio.run(whatsapp_webhook.procesar_evento(_payload("precios", "wamid.x"), enviador))
    assert (primero, segundo) == (1, 0)
    # "precios" produce dos respuestas (texto + lista): lo que importa es que el
    # reintento no mande ningún payload adicional.
    assert len(enviador.salida) == enviados_tras_el_primero


def test_procesar_avisa_si_no_es_texto(bd):
    p = _payload()
    p["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "id": "wamid.img", "from": "5215500000001", "type": "image", "image": {"id": "X"},
    }
    enviador = _Enviador()
    asyncio.run(whatsapp_webhook.procesar_evento(p, enviador))
    assert "solo entiendo mensajes de texto" in _cuerpo(*enviador.salida[0])


def test_procesar_guarda_el_telefono_en_la_sesion(bd):
    enviador = _Enviador()
    asyncio.run(whatsapp_webhook.procesar_evento(_payload("hola"), enviador))
    assert engine.SESIONES["wa:5215500000001"].telefono == "5215500000001"
