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
