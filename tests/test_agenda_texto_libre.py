"""Conversación libre: el bot debe avanzar la agenda, no dar vueltas.

Reproduce el caso real de WhatsApp donde el cliente escribe frases naturales
("me agendas el miércoles a las 6 pm") y el bot solo repetía preguntas porque
el texto libre nunca tocaba la máquina de estados.

Corre con: .venv/Scripts/python.exe -m pytest tests/test_agenda_texto_libre.py -q
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app import engine, reloj

pytestmark = pytest.mark.usefixtures("bd")


@pytest.fixture()
def datos() -> dict:
    return engine.cargar_datos()


def _enviar(sid: str, texto: str, datos: dict):
    return asyncio.run(engine.manejar(sid, texto, datos))


# ---------- parsers ----------
@pytest.mark.parametrize(
    "frase, esperado",
    [
        ("Manicura y pedicura, por favor", "manicura_pedicura"),
        ("Gelish por favor", "gelish"),
        ("Solo requiero la manicura para mí", "manicura"),
        ("manicura para niñas", "ninas"),
        ("pedicura", "pedicura"),
    ],
)
def test_detecta_servicio(frase, esperado, datos):
    assert engine._detectar_servicio(frase, datos) == esperado


def test_no_inventa_servicio_inexistente(datos):
    assert engine._detectar_servicio("quiero un corte de cabello", datos) is None


@pytest.mark.parametrize(
    "frase, dia, hora",
    [
        ("Me agendas el miércoles a las 6 pm", 2, "18:00"),
        ("el jueves a la 1 pm", 3, "13:00"),
        ("¿Tienes espacio el miércoles?", 2, None),
        ("6:30 pm", None, "18:30"),
        ("el viernes en la mañana", 4, None),
    ],
)
def test_detecta_dia_hora(frase, dia, hora):
    assert engine._detectar_dia_hora(frase) == (dia, hora)


# ---------- conversación completa ----------
def _cita_natural(datos: dict, servicio_id: str) -> tuple[str, str, str]:
    """Primer hueco libre + la frase como la escribiría un cliente."""
    slot = engine.slots_disponibles(datos, servicio_id)[0]
    d = datetime.fromisoformat(slot)
    sufijo = "am" if d.hour < 12 else "pm"
    h12 = d.hour % 12 or 12
    nombre = engine._servicio(datos, servicio_id)["nombre"]
    frase = f"Me agendas {nombre} el {engine.DIAS[d.weekday()]} a las {h12} {sufijo}"
    return slot, frase, nombre


def test_frase_libre_agenda_sin_repetir_preguntas(datos):
    slot, frase, _ = _cita_natural(datos, "manicura_pedicura")
    resp, s = _enviar("libre1", frase, datos)
    assert s.paso == "agendar_nombre", f"quedó en {s.paso}: {resp}"
    assert s.slot == slot
    assert "nombre" in resp[0]["texto"].lower()

    resp, s = _enviar("libre1", "Brenda Pérez", datos)
    texto = resp[0]["texto"]
    assert "folio" in texto.lower(), texto
    assert s.folio and s.folio.startswith("AMB-")


def test_servicio_solo_muestra_horarios_sin_repetir_el_servicio(datos):
    resp, s = _enviar("libre2", "Manicura y pedicura, por favor", datos)
    assert s.paso == "agendar_slot"
    assert s.servicio_id == "manicura_pedicura"
    opciones = resp[-1].get("opciones") or []
    assert opciones, "debe ofrecer horarios concretos"


def test_horario_pedido_se_elige_en_el_siguiente_mensaje(datos):
    _enviar("libre3", "Manicura y pedicura", datos)
    slot, frase, _ = _cita_natural(datos, "manicura_pedicura")
    resp, s = _enviar("libre3", frase, datos)
    assert s.slot == slot, f"eligió {s.slot} en vez de {slot}"
    assert s.paso == "agendar_nombre"


def test_nombre_pegado_al_servicio_se_limpia(datos):
    """'Brenda Pérez, gelish, ...' no debe guardar la frase entera como nombre."""
    slot, frase, _ = _cita_natural(datos, "gelish")
    _enviar("libre4", frase, datos)
    resp, s = _enviar("libre4", "Brenda Pérez, gelish, el miércoles a las 6 pm", datos)
    assert "folio" in resp[0]["texto"].lower(), resp
    assert s.folio
    cita = engine.store.obtener(s.folio)
    assert cita["nombre"] == "Brenda Pérez"


def test_corta_el_bucle_tras_una_respuesta_sin_avance(datos):
    _enviar("libre5", "mmm no sé", datos)
    resp, s = _enviar("libre5", "ok", datos)
    assert s.paso == "agendar_servicio", "debe pasar a botones en vez de otra prosa"
    ids = [o["id"] for o in (resp[-1].get("opciones") or [])]
    assert set(ids) >= {"manicura", "gelish"}


def test_pregunta_de_precio_no_secuestra_la_agenda(datos):
    resp, s = _enviar("libre6", "¿cuánto cuesta la manicura?", datos)
    assert s.paso == "inicio"
    assert not s.servicio_id
    assert resp[0]["texto"].strip()
