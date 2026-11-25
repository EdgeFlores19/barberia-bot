"""Store: claves de rastreo vistas y extensión del apartado (ADR 0011)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app import reloj, store

from tests.telefonos import TEL_A

pytestmark = pytest.mark.usefixtures("bd")


def _apartado(minutos: int = 60) -> dict:
    ahora = reloj.ahora()
    return store.crear((ahora + timedelta(days=3)).strftime("%Y-%m-%dT10:00"), "gelish", "Ana", TEL_A,
                       pendiente_hasta=(ahora + timedelta(minutes=minutos)).isoformat(timespec="seconds"),
                       anticipo=200, aceptado_en=ahora.isoformat(timespec="seconds"))


def test_la_primera_cita_en_ver_una_clave_se_queda_con_ella():
    assert store.registrar_clave("MBAN01ABCDEF1234", "AMB-AAAA") == "AMB-AAAA"


def test_la_clave_no_se_pisa_y_devuelve_a_su_dueno():
    store.registrar_clave("MBAN01ABCDEF1234", "AMB-AAAA")

    assert store.registrar_clave("MBAN01ABCDEF1234", "AMB-BBBB") == "AMB-AAAA"
    assert store.registrar_clave("MBAN01ABCDEF1234", "AMB-AAAA") == "AMB-AAAA"     # su propio reenvío


def test_el_folio_se_guarda_normalizado():
    store.registrar_clave("MBAN01ABCDEF1234", " amb-aaaa ")

    assert store.registrar_clave("MBAN01ABCDEF1234", "AMB-BBBB") == "AMB-AAAA"


def test_extender_alarga_el_apartado_pendiente():
    cita = _apartado(minutos=60)
    nuevo = (reloj.ahora() + timedelta(hours=12)).isoformat(timespec="seconds")

    fila = store.extender_apartado(cita["folio"], nuevo)

    assert fila["vence"] == nuevo
    assert store.obtener(cita["folio"])["vence"] == nuevo


def test_extender_nunca_acorta_el_apartado():
    cita = _apartado(minutos=60)
    antes = store.obtener(cita["folio"])["vence"]

    fila = store.extender_apartado(cita["folio"], (reloj.ahora() + timedelta(minutes=5)).isoformat(timespec="seconds"))

    assert fila["vence"] == antes


def test_extender_no_resucita_un_apartado_expirado():
    cita = _apartado()
    store.expirar_vencidas((reloj.ahora() + timedelta(days=1)).isoformat(timespec="seconds"))

    assert store.extender_apartado(cita["folio"], (reloj.ahora() + timedelta(hours=12)).isoformat(timespec="seconds")) is None
    assert store.obtener(cita["folio"])["estado"] == "expirada"
