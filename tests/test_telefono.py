from __future__ import annotations

from app.telefono import a_whatsapp, t4, t10


def test_t10_iguala_formatos_con_y_sin_el_1():
    assert t10("5215500000011") == t10("525500000011") == "5500000011"


def test_t10_ignora_signos_y_vacios():
    assert t10("+52 55 0000 0011") == "5500000011"
    assert t10("") == "" and t10(None) == ""


def test_t4_devuelve_los_ultimos_cuatro():
    assert t4("5215500000011") == "0011"
    assert t4("") == "----"


def test_a_whatsapp_quita_el_1_heredado_de_13_digitos():
    assert a_whatsapp("5215500000011") == "525500000011"


def test_a_whatsapp_deja_intacto_el_canonico_de_12_digitos():
    assert a_whatsapp("525500000011") == "525500000011"


def test_a_whatsapp_no_toca_otros_paises():
    assert a_whatsapp("14155550100") == "14155550100"


def test_a_whatsapp_ignora_signos_espacios_y_el_mas():
    assert a_whatsapp("+52 55 0000 0011") == "525500000011"
    assert a_whatsapp(" +52 1 55 0000 0011 ") == "525500000011"


def test_a_whatsapp_con_vacio_o_none_devuelve_cadena_vacia():
    assert a_whatsapp("") == ""
    assert a_whatsapp(None) == ""
