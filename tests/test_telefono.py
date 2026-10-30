from __future__ import annotations

from app.telefono import t4, t10


def test_t10_iguala_formatos_con_y_sin_el_1():
    assert t10("5215500000011") == t10("525500000011") == "5500000011"


def test_t10_ignora_signos_y_vacios():
    assert t10("+52 55 0000 0011") == "5500000011"
    assert t10("") == "" and t10(None) == ""


def test_t4_devuelve_los_ultimos_cuatro():
    assert t4("5215500000011") == "0011"
    assert t4("") == "----"
