"""Anticipo: configuración con valores por defecto, políticas y textos que salen de ella."""
from __future__ import annotations

from datetime import datetime

import pytest

from app import anticipo, config

AHORA = datetime(2026, 11, 16, 10, 0)           # lunes
CLABE_OK = "002010077777777771"


def _datos(**cambios) -> dict:
    return {"anticipo": {"activo": True, **cambios}}


# ---------- configuración ----------
def test_sin_bloque_el_anticipo_esta_apagado():
    assert anticipo.activo({}) is False
    assert anticipo.activo({"anticipo": {}}) is False


def test_los_valores_acordados_son_los_por_defecto():
    c = anticipo.cfg(_datos())

    assert c["monto"] == 200
    assert c["descuenta_del_total"] is True
    assert (c["apartado_minutos"], c["apartado_minutos_mismo_dia"]) == (60, 30)
    assert c["alerta_inmediata_horas"] == 24
    assert c["cancelar"]["reembolso"] == "nunca"
    assert c["reagendar"] == {"permitido": True, "max_veces": 1, "horas_minimas": 24, "conserva_anticipo": True}


def test_la_config_del_negocio_se_mezcla_con_los_defectos():
    c = anticipo.cfg(_datos(monto=350, reagendar={"max_veces": 3}))

    assert c["monto"] == 350
    assert c["reagendar"]["max_veces"] == 3
    assert c["reagendar"]["horas_minimas"] == 24          # lo que no se tocó queda por defecto


def test_la_variable_de_entorno_manda_sobre_el_json(monkeypatch):
    monkeypatch.setattr(config, "ANTICIPO_ACTIVO", True)
    assert anticipo.activo({"anticipo": {"activo": False}}) is True
    monkeypatch.setattr(config, "ANTICIPO_ACTIVO", False)
    assert anticipo.activo(_datos()) is False


# ---------- cuentas ----------
def test_restante_descuenta_el_anticipo_sin_bajar_de_cero():
    c = anticipo.cfg(_datos())
    assert anticipo.restante(350, c) == 150
    assert anticipo.restante(150, c) == 0


def test_restante_si_el_anticipo_no_se_descuenta():
    assert anticipo.restante(350, anticipo.cfg(_datos(descuenta_del_total=False))) == 350


# ---------- CLABE y datos de pago ----------
def test_clabe_valida_con_digito_de_control():
    assert anticipo.clabe_valida(CLABE_OK) is True


@pytest.mark.parametrize("c", ["002010077777777772", "00201007777777777", "00201007777777777A", "", " " * 18])
def test_clabe_invalida(c):
    assert anticipo.clabe_valida(c) is False


def test_problemas_de_pago_lista_lo_que_falta(monkeypatch):
    monkeypatch.setattr(config, "ANTICIPO_BANCO", "")
    monkeypatch.setattr(config, "ANTICIPO_CLABE", "123")
    monkeypatch.setattr(config, "ANTICIPO_TITULAR", "")

    problemas = " ".join(anticipo.problemas_de_pago())

    assert "ANTICIPO_BANCO" in problemas and "ANTICIPO_TITULAR" in problemas and "ANTICIPO_CLABE" in problemas


def test_pago_bien_configurado_no_tiene_problemas(monkeypatch):
    monkeypatch.setattr(config, "ANTICIPO_BANCO", "Banco de Prueba")
    monkeypatch.setattr(config, "ANTICIPO_CLABE", CLABE_OK)
    monkeypatch.setattr(config, "ANTICIPO_TITULAR", "Nombre Apellido")

    assert anticipo.problemas_de_pago() == []


# ---------- tiempos ----------
def test_apartado_mas_corto_si_la_cita_es_hoy():
    c = anticipo.cfg(_datos())
    assert anticipo.apartado_minutos(c, "2026-11-16T17:00", AHORA) == 30
    assert anticipo.apartado_minutos(c, "2026-11-17T10:00", AHORA) == 60


def test_alerta_inmediata_si_faltan_menos_de_24_horas():
    c = anticipo.cfg(_datos())
    assert anticipo.es_urgente(c, "2026-11-17T09:30", AHORA) is True      # 23.5 h
    assert anticipo.es_urgente(c, "2026-11-17T10:00", AHORA) is False     # 24 h justas


# ---------- políticas ----------
def test_cancelar_nunca_devuelve():
    c = anticipo.cfg(_datos())
    assert anticipo.cancelacion_devuelve(c, 500) is False


def test_cancelar_con_aviso_devuelve_solo_si_hay_tiempo():
    c = anticipo.cfg(_datos(cancelar={"reembolso": "hasta_horas", "horas": 48}))
    assert anticipo.cancelacion_devuelve(c, 72) is True
    assert anticipo.cancelacion_devuelve(c, 47.9) is False


@pytest.mark.parametrize("cambios,veces,horas,esperado", [
    ({}, 0, 48, (True, "")),
    ({}, 1, 48, (False, "max_veces")),
    ({}, 0, 23, (False, "poco_tiempo")),
    ({"reagendar": {"permitido": False}}, 0, 99, (False, "no_permitido")),
    ({"reagendar": {"conserva_anticipo": False}}, 0, 99, (False, "sin_conservar")),
    ({"reagendar": {"max_veces": 3}}, 2, 30, (True, "")),
])
def test_politica_de_reagendar(cambios, veces, horas, esperado):
    c = anticipo.cfg(_datos(**cambios))
    assert anticipo.reagendar_permitido(c, veces, horas) == esperado


# ---------- los textos salen de la configuración ----------
def test_el_aviso_dice_lo_que_el_sistema_hara_por_defecto():
    t = anticipo.texto_aviso(anticipo.cfg(_datos()), precio=350)

    assert "$200" in t
    assert "no se devuelve" in t
    assert "te resta pagar $150" in t
    assert "1 vez" in t and "24 h" in t


def test_el_aviso_cambia_con_la_politica_de_reembolso():
    t = anticipo.texto_aviso(anticipo.cfg(_datos(cancelar={"reembolso": "hasta_horas", "horas": 48})), precio=350)

    assert "48 h" in t
    assert "se te devuelve" in t


def test_el_aviso_cambia_si_no_se_permite_mover():
    t = anticipo.texto_aviso(anticipo.cfg(_datos(reagendar={"permitido": False})), precio=350)

    assert "no se puede mover" in t


def test_el_aviso_no_promete_descuento_si_no_se_descuenta():
    t = anticipo.texto_aviso(anticipo.cfg(_datos(descuenta_del_total=False)), precio=350)

    assert "te resta pagar" not in t


def test_el_texto_de_pago_lleva_cuenta_concepto_y_tiempo():
    t = anticipo.texto_pago(anticipo.cfg(_datos()), folio="AMB-7K4Z", minutos=60,
                            banco="Banco de Prueba", clabe=CLABE_OK, titular="Nombre Apellido")

    for esperado in ("$200", "Banco de Prueba", CLABE_OK, "Nombre Apellido", "AMB-7K4Z", "60 min"):
        assert esperado in t


def test_la_confirmacion_dice_que_el_pago_se_verifica_y_muestra_el_resto():
    t = anticipo.texto_comprobante_ok(anticipo.cfg(_datos()), servicio="Gelish", etiqueta="Martes 17 nov · 10:00",
                                      folio="AMB-7K4Z", precio=350)

    assert "verificará" in t and "Resta por pagar: $150" in t and "AMB-7K4Z" in t
