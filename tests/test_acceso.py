"""sid firmados, comparación del código, IP del cliente, rate limit y tope de sesiones."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app import acceso, config, engine, reloj


@pytest.fixture(autouse=True)
def limpio():
    acceso._REGISTRO.clear()
    engine.SESIONES.clear()
    yield
    acceso._REGISTRO.clear()
    engine.SESIONES.clear()


# ---------- sid firmado ----------
def test_un_sid_emitido_es_valido():
    assert acceso.sid_valido(acceso.nuevo_sid())


def test_los_sid_no_se_repiten():
    assert acceso.nuevo_sid() != acceso.nuevo_sid()


@pytest.mark.parametrize("falso", [
    "", "web1", "abc123abc123", "abc123abc123.", ".firma", "abc123abc123.firmafalsa",
    "wa:5215500000001", "x" * 64, "abc123abc123.ñandú",
])
def test_un_sid_inventado_no_es_valido(falso):
    assert acceso.sid_valido(falso) is False


def test_alterar_la_base_o_la_firma_lo_invalida():
    base, firma = acceso.nuevo_sid().split(".")
    assert acceso.sid_valido(f"{'0' * 12}.{firma}") is False
    assert acceso.sid_valido(f"{base}.{'0' * 20}") is False


def test_un_sid_de_otra_clave_no_sirve(monkeypatch):
    sid = acceso.nuevo_sid()
    monkeypatch.setattr(config, "SESSION_SECRET", "otra-clave")
    assert acceso.sid_valido(sid) is False


# ---------- código de demo ----------
def test_codigo_ok(monkeypatch):
    monkeypatch.setattr(config, "DEMO_CODE", "clave-demo")
    assert acceso.codigo_ok("clave-demo") is True
    assert acceso.codigo_ok("  clave-demo  ") is True
    assert acceso.codigo_ok("otra") is False
    assert acceso.codigo_ok("") is False


def test_codigo_con_acentos_no_lanza(monkeypatch):
    monkeypatch.setattr(config, "DEMO_CODE", "clave-demo")
    assert acceso.codigo_ok("clavé-demó") is False


# ---------- IP del cliente ----------
def test_ip_toma_la_primera_de_xff_por_defecto():
    assert acceso.ip_cliente("1.1.1.1, 2.2.2.2", "9.9.9.9") == "1.1.1.1"


def test_ip_desde_la_derecha_ignora_lo_que_falsea_el_cliente():
    assert acceso.ip_cliente("6.6.6.6, 1.1.1.1, 2.2.2.2", "9.9.9.9", desde_derecha=1) == "2.2.2.2"
    assert acceso.ip_cliente("6.6.6.6, 1.1.1.1, 2.2.2.2", "9.9.9.9", desde_derecha=2) == "1.1.1.1"


def test_ip_sin_cabecera_o_mal_formada_usa_el_peer():
    assert acceso.ip_cliente("", "9.9.9.9") == "9.9.9.9"
    assert acceso.ip_cliente(" , ,", "9.9.9.9") == "9.9.9.9"
    assert acceso.ip_cliente("1.1.1.1", "9.9.9.9", desde_derecha=3) == "9.9.9.9"
    assert acceso.ip_cliente("", "") == "?"


# ---------- rate limit ----------
def test_permite_hasta_el_tope_y_luego_bloquea():
    assert [acceso.permitir("sesion", "1.1.1.1", 3, ahora=100.0) for _ in range(4)] == [True, True, True, False]


def test_la_ventana_se_libera_pasados_60_segundos():
    for _ in range(3):
        acceso.permitir("sesion", "1.1.1.1", 3, ahora=100.0)
    assert acceso.permitir("sesion", "1.1.1.1", 3, ahora=130.0) is False
    assert acceso.permitir("sesion", "1.1.1.1", 3, ahora=161.0) is True


def test_cada_ip_y_cada_clave_cuentan_aparte():
    for _ in range(3):
        acceso.permitir("sesion", "1.1.1.1", 3, ahora=100.0)
    assert acceso.permitir("sesion", "2.2.2.2", 3, ahora=100.0) is True
    assert acceso.permitir("mensaje", "1.1.1.1", 3, ahora=100.0) is True


def test_el_limitador_no_crece_sin_limite(monkeypatch):
    monkeypatch.setattr(acceso, "_MAX_CLAVES", 50)
    for i in range(500):
        acceso.permitir("sesion", f"10.0.{i // 256}.{i % 256}", 3, ahora=100.0)
    assert len(acceso._REGISTRO) <= 50


# ---------- tope de sesiones en memoria ----------
def _sesion(sid: str, minutos_inactiva: int, canal: str = "web") -> None:
    s = engine.SESIONES[sid] = engine.Sesion(sid, canal=canal)
    s.ultima_actividad = reloj.ahora() - timedelta(minutes=minutos_inactiva)


def test_recortar_desaloja_las_web_mas_inactivas():
    for i in range(5):
        _sesion(f"w{i}", minutos_inactiva=i)         # w4 es la más vieja

    engine.recortar_sesiones(3)

    assert set(engine.SESIONES) == {"w0", "w1", "w2"}


def test_recortar_no_toca_las_de_whatsapp():
    _sesion("wa:5215500000001", minutos_inactiva=600, canal="wa")
    for i in range(3):
        _sesion(f"w{i}", minutos_inactiva=i)

    engine.recortar_sesiones(1)

    assert "wa:5215500000001" in engine.SESIONES


def test_recortar_sin_exceso_no_hace_nada():
    _sesion("w0", 0)
    engine.recortar_sesiones(10)
    assert "w0" in engine.SESIONES
