"""Tests del webhook: firma, extracción de mensajes y procesamiento."""
from __future__ import annotations

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
