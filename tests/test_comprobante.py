"""Validación del comprobante (ADR 0011): parseo estricto y comparación determinista. Sin red."""
from __future__ import annotations

from datetime import date

import pytest

from app import comprobante

CLABE = "002010077777777771"
HOY = date(2026, 11, 20)
DESDE = date(2026, 11, 20)


def _lectura(**kw) -> dict:
    base = {"monto": 200.0, "fecha": HOY, "clave_rastreo": "MBAN01ABCDEF1234", "cuenta_digitos": "7771",
            "beneficiario": "Nombre Apellido", "concepto": "Ana AMB-7K4Q"}
    return {**base, **kw}


def _evaluar(lectura, **kw):
    args = dict(monto=200, folio="AMB-7K4Q", clabe=CLABE, titular="Nombre Apellido",
                desde=DESDE, hoy=HOY, clave_de=None)
    return comprobante.evaluar(lectura, **{**args, **kw})


# ---------- parsear ----------
def test_parsea_un_json_limpio():
    texto = ('{"es_comprobante": true, "monto": 200, "fecha": "2026-11-20", "clave_rastreo": "mban01-abcdef1234", '
             '"cuenta_destino": "****7771", "beneficiario": "Nombre Apellido", "concepto": "Ana AMB-7K4Q"}')

    lectura = comprobante.parsear(texto)

    assert lectura == {"monto": 200.0, "fecha": HOY, "clave_rastreo": "MBAN01ABCDEF1234",
                       "cuenta_digitos": "7771", "beneficiario": "Nombre Apellido", "concepto": "Ana AMB-7K4Q"}


def test_parsea_aunque_el_modelo_lo_envuelva_en_un_bloque_de_codigo():
    assert comprobante.parsear('```json\n{"es_comprobante": true, "monto": "$200.00"}\n```')["monto"] == 200.0


@pytest.mark.parametrize("texto", [
    "no puedo leer esta imagen", "", "[1, 2, 3]", '{"es_comprobante": false, "monto": 200}',
    '{"monto": 200}', '{"es_comprobante": "true", "monto": 200}'])
def test_lo_que_no_es_un_comprobante_no_se_parsea(texto):
    assert comprobante.parsear(texto) is None


@pytest.mark.parametrize("crudo, esperado", [
    (200, 200.0), ("200", 200.0), ("$1,200.50", 1200.5), ("200,50", 200.5), ("1,200", 1200.0),
    (True, None), (-5, None), ("abc", None), (None, None)])
def test_monto(crudo, esperado):
    assert comprobante._a_monto(crudo) == esperado


def test_los_campos_raros_se_saneán_sin_romper():
    lectura = comprobante.parsear('{"es_comprobante": true, "fecha": "ayer", "clave_rastreo": "123", '
                                  '"cuenta_destino": 7771, "beneficiario": 5, "concepto": "' + "x" * 500 + '"}')

    assert lectura["fecha"] is None                       # no es AAAA-MM-DD
    assert lectura["clave_rastreo"] is None               # demasiado corta para ser una clave
    assert lectura["cuenta_digitos"] == ""                # no era texto
    assert lectura["beneficiario"] is None
    assert len(lectura["concepto"]) == comprobante.MAX_TEXTO


# ---------- evaluar: el camino feliz ----------
def test_un_comprobante_que_cuadra_pasa_sin_avisos():
    v = _evaluar(_lectura())

    assert v.ok and v.motivos == () and v.avisos == ()


def test_la_cuenta_completa_se_compara_entera():
    assert _evaluar(_lectura(cuenta_digitos=CLABE)).ok
    assert not _evaluar(_lectura(cuenta_digitos="002010077777777772")).ok


# ---------- evaluar: lo que NO cuadra ----------
def test_sin_lectura_va_a_revision():
    v = _evaluar(None)

    assert not v.ok and "No se pudo leer" in v.motivos[0]


def test_un_monto_distinto_no_pasa():
    v = _evaluar(_lectura(monto=100.0))

    assert not v.ok and "$100.00" in v.motivos[0] and "$200.00" in v.motivos[0]


def test_un_monto_ilegible_no_pasa():
    assert not _evaluar(_lectura(monto=None)).ok


def test_una_cuenta_de_otro_banco_no_pasa_aunque_el_nombre_coincida():
    v = _evaluar(_lectura(cuenta_digitos="1234"))

    assert not v.ok and any("cuenta de destino no coincide" in m for m in v.motivos)


def test_un_beneficiario_distinto_sin_cuenta_visible_no_pasa():
    v = _evaluar(_lectura(cuenta_digitos="", beneficiario="Pedro Ramirez"))

    assert not v.ok


def test_un_beneficiario_distinto_pero_con_la_cuenta_correcta_pasa_con_aviso():
    v = _evaluar(_lectura(beneficiario="Negocio de Ana SA"))

    assert v.ok and any("beneficiario" in a for a in v.avisos)


def test_si_no_se_ve_ni_la_cuenta_ni_el_beneficiario_no_pasa():
    v = _evaluar(_lectura(cuenta_digitos="", beneficiario=None))

    assert not v.ok and any("No se ve la cuenta" in m for m in v.motivos)


def test_el_nombre_acortado_por_el_banco_cuenta():
    assert _evaluar(_lectura(cuenta_digitos="", beneficiario="NOMBRE APELLIDO G")).ok


@pytest.mark.parametrize("fecha, ok", [
    (HOY, True), (date(2026, 11, 19), False), (date(2026, 11, 21), False), (None, False)])
def test_la_fecha_debe_ser_de_hoy_o_desde_el_apartado(fecha, ok):
    assert _evaluar(_lectura(fecha=fecha)).ok is ok


def test_la_clave_ya_usada_en_otra_cita_no_pasa():
    v = _evaluar(_lectura(), clave_de="AMB-ZZZZ")

    assert not v.ok and "AMB-ZZZZ" in v.motivos[0]


def test_la_clave_de_la_misma_cita_es_un_reenvio_y_pasa():
    assert _evaluar(_lectura(), clave_de="AMB-7K4Q").ok


def test_sin_clave_pasa_pero_avisa():
    v = _evaluar(_lectura(clave_rastreo=None))

    assert v.ok and any("clave de rastreo" in a for a in v.avisos)


# ---------- el folio es blando ----------
@pytest.mark.parametrize("concepto", [None, "", "pago de uñas", "AMB-0000"])
def test_sin_el_folio_pasa_pero_avisa(concepto):
    v = _evaluar(_lectura(concepto=concepto))

    assert v.ok and any("folio" in a for a in v.avisos)


@pytest.mark.parametrize("concepto", ["Ana AMB-7K4Q", "ana amb7k4q", "AMB 7K4Q ana"])
def test_el_folio_se_reconoce_con_otro_formato(concepto):
    assert _evaluar(_lectura(concepto=concepto)).avisos == ()


def test_varios_problemas_se_listan_todos():
    v = _evaluar(_lectura(monto=50.0, fecha=None, cuenta_digitos="1234"))

    assert not v.ok and len(v.motivos) == 3
