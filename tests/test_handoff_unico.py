"""El handoff avisa al dueño una vez por episodio y desde todos los caminos."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app import engine, reloj, store

pytestmark = pytest.mark.usefixtures("bd")


@pytest.fixture()
def datos() -> dict:
    return engine.cargar_datos()


@pytest.fixture()
def avisos_enviados(monkeypatch) -> list:
    enviados: list = []
    monkeypatch.setattr(engine.avisos, "notificar_handoff",
                        lambda sid, tel, msg: enviados.append((sid, tel, msg)))
    return enviados


def _contar_handoffs() -> int:
    with store._con() as con:
        return con.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]


def _decir(sid: str, texto: str, datos: dict):
    return asyncio.run(engine.manejar(sid, texto, datos))


def _cita_en_45_minutos() -> dict:
    inicio = reloj.ahora() + timedelta(minutes=45)
    return store.crear(inicio.strftime("%Y-%m-%dT%H:%M"), "manicura", "Tarde")


def test_repetir_humano_avisa_una_sola_vez(datos, avisos_enviados):
    for _ in range(3):
        _decir("u1", "quiero hablar con una persona", datos)

    assert len(avisos_enviados) == 1
    assert _contar_handoffs() == 1


def test_cancelar_fuera_de_ventana_avisa_a_la_manicurista(datos, avisos_enviados):
    cita = _cita_en_45_minutos()

    _, s = _decir("u2", f"cancelar {cita['folio']}", datos)

    assert s.handoff is True
    assert len(avisos_enviados) == 1
    assert _contar_handoffs() == 1
    assert store.obtener(cita["folio"])["estado"] == "confirmada"


def test_mover_fuera_de_ventana_avisa_a_la_manicurista(datos, avisos_enviados):
    cita = _cita_en_45_minutos()

    _, s = _decir("u3", f"mover {cita['folio']}", datos)

    assert s.handoff is True
    assert len(avisos_enviados) == 1


def test_la_opcion_5_avisa_a_la_manicurista(datos, avisos_enviados):
    _decir("u4", "5", datos)

    assert len(avisos_enviados) == 1
    assert _contar_handoffs() == 1


def test_sin_huecos_avisa_a_la_manicurista(datos, avisos_enviados, monkeypatch):
    monkeypatch.setattr(engine, "slots_disponibles", lambda *a, **k: [])
    _decir("u5", "agendar", datos)

    _, s = _decir("u5", datos["servicios"][0]["id"], datos)

    assert s.handoff is True
    assert len(avisos_enviados) == 1


def test_menu_reinicia_y_un_nuevo_handoff_vuelve_a_avisar(datos, avisos_enviados):
    _decir("u6", "humano", datos)
    _decir("u6", "menu", datos)
    _decir("u6", "humano", datos)

    assert len(avisos_enviados) == 2
    assert _contar_handoffs() == 2
