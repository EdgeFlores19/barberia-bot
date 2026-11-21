"""Avisos al dueño: la línea "con quién" solo cuando la cita tiene agenda."""
from __future__ import annotations

import pytest

from app import avisos

CITA = {
    "folio": "AMB-AAAA",
    "nombre": "Ana",
    "anticipo_monto": 200,
}


@pytest.fixture
def enviados(monkeypatch):
    salida: list[str] = []
    monkeypatch.setattr(avisos, "_telegram", lambda texto: salida.append(texto) or True)
    return salida


def _cita(**kw):
    base = dict(CITA)
    base.update(kw)
    return base


def test_anticipo_dice_con_quien_si_hay_agenda(enviados):
    avisos.notificar_anticipo(cita=_cita(agenda_nombre="Ana"), servicio="Manicura",
                              etiqueta="vie 21 nov 10:00", restante=50,
                              telefono="5215551234567", urgente=False, tardio=False)

    assert "Con: Ana" in enviados[0]


def test_anticipo_no_agrega_la_linea_sin_agenda(enviados):
    avisos.notificar_anticipo(cita=_cita(), servicio="Manicura", etiqueta="vie 21 nov 10:00",
                              restante=50, telefono="5215551234567", urgente=False, tardio=False)

    assert "Con:" not in enviados[0]


def test_cancelacion_con_anticipo_dice_con_quien(enviados):
    avisos.notificar_cancelacion_anticipo(cita=_cita(agenda_nombre="Brenda"),
                                          servicio="Manicura", etiqueta="vie 21 nov 10:00",
                                          telefono="5215551234567", devuelve=True)

    assert "Con: Brenda" in enviados[0]
    assert "SE DEVUELVE" in enviados[0]
