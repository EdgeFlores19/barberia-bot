"""Tests del traductor motor -> payloads de WhatsApp Cloud API."""
from __future__ import annotations

from app import whatsapp_formato


def test_texto_plano_se_convierte_en_mensaje_de_texto():
    assert whatsapp_formato.a_payloads([{"tipo": "texto", "texto": "Hola"}]) == [
        {"type": "text", "text": {"body": "Hola"}}
    ]
