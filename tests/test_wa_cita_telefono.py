"""En WhatsApp la cita guarda el teléfono: el cliente puede gestionar su propia cita."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app import engine, reloj, store
from app.telefono import t10

from tests.telefonos import TEL_A, TEL_B

pytestmark = pytest.mark.usefixtures("bd")


@pytest.fixture()
def datos() -> dict:
    return engine.cargar_datos()


def _slot_con_margen(datos: dict, servicio_id: str) -> str:
    horas = datos["reglas_agenda"]["cancelacion_libre_hasta_horas"]
    limite = reloj.ahora() + timedelta(hours=horas + 1)
    libres = [s for s in engine.slots_disponibles(datos, servicio_id)
              if datetime.fromisoformat(s) >= limite]
    assert libres, "debería haber disponibilidad con margen en los próximos 7 días"
    return libres[0]


def _decir(sid: str, texto: str, datos: dict, canal: str, telefono: str = ""):
    return asyncio.run(engine.manejar(sid, texto, datos, canal=canal, telefono=telefono))


def _agendar(sid: str, datos: dict, canal: str, telefono: str = "") -> dict:
    servicio = datos["servicios"][0]["id"]
    _decir(sid, "agendar", datos, canal, telefono)
    _decir(sid, servicio, datos, canal, telefono)
    _decir(sid, _slot_con_margen(datos, servicio), datos, canal, telefono)
    _decir(sid, "Cliente de Prueba", datos, canal, telefono)
    return store.listar()[-1]


def test_cita_de_whatsapp_guarda_el_telefono(datos):
    cita = _agendar(f"wa:{TEL_A}", datos, "wa", TEL_A)

    assert t10(cita["telefono"]) == t10(TEL_A)


def test_el_mismo_cliente_de_whatsapp_puede_cancelar_su_cita(datos):
    sid = f"wa:{TEL_A}"
    cita = _agendar(sid, datos, "wa", TEL_A)

    _decir(sid, f"cancelar {cita['folio']}", datos, "wa", TEL_A)

    assert store.obtener(cita["folio"])["estado"] == "cancelada"


def test_otro_telefono_sigue_sin_poder_cancelarla(datos):
    cita = _agendar(f"wa:{TEL_A}", datos, "wa", TEL_A)

    _decir(f"wa:{TEL_B}", f"cancelar {cita['folio']}", datos, "wa", TEL_B)

    assert store.obtener(cita["folio"])["estado"] == "confirmada"


def test_el_chat_web_no_guarda_telefono(datos):
    cita = _agendar("web-1", datos, "web")

    assert cita["telefono"] == ""
