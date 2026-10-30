"""Tests del traductor motor -> payloads de WhatsApp Cloud API."""
from __future__ import annotations

from app import whatsapp_formato


def test_texto_plano_se_convierte_en_mensaje_de_texto():
    assert whatsapp_formato.a_payloads([{"tipo": "texto", "texto": "Hola"}]) == [
        {"type": "text", "text": {"body": "Hola"}}
    ]


def test_tres_opciones_van_como_botones():
    resp = [{
        "tipo": "botones",
        "texto": "¿Qué necesitas?",
        "opciones": [
            {"id": "agendar", "label": "Agendar cita"},
            {"id": "citas", "label": "Mi cita"},
            {"id": "precios", "label": "Precios"},
        ],
    }]
    p = whatsapp_formato.a_payloads(resp)[0]
    assert p["interactive"]["type"] == "button"
    botones = p["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"] for b in botones] == ["agendar", "citas", "precios"]
    assert all(b["type"] == "reply" for b in botones)


def test_mas_de_tres_opciones_van_como_lista():
    resp = [{
        "tipo": "botones",
        "texto": "Menú",
        "opciones": [{"id": f"op{i}", "label": f"Opción {i}"} for i in range(5)],
    }]
    p = whatsapp_formato.a_payloads(resp)[0]
    assert p["interactive"]["type"] == "list"
    filas = p["interactive"]["action"]["sections"][0]["rows"]
    assert [f["id"] for f in filas] == ["op0", "op1", "op2", "op3", "op4"]
