"""Quién dispara el concentrado: el bucle del proceso (Render no tiene cron).

`resumen_hora` en el JSON no programa nada por sí solo; esto fija el comportamiento del
disparador: sale una vez al día, después de la hora, y si Telegram falla se reintenta.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app import anticipo, config, main, reloj, store

from tests.telefonos import TEL_A


@pytest.fixture()
def a_las(bd, monkeypatch):
    """Fija la hora del negocio y deja el teléfono falso, sin tocar la red."""
    enviados: list[str] = []

    def fijar(hora: str, telegram_ok: bool = True):
        monkeypatch.setattr(reloj, "ahora", lambda: datetime.fromisoformat(hora))
        monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "token-de-prueba")
        monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "123")
        monkeypatch.setattr(
            "app.avisos._telegram",
            lambda texto: enviados.append(texto) or telegram_ok,
        )
        return enviados

    return fijar


def _deposito(ahora: str = "2026-11-15T18:42:05") -> dict:
    c = store.crear(slot="2026-11-16T10:00", servicio_id="manicura", nombre="Ana",
                    telefono=TEL_A, pendiente_hasta="2026-11-15T23:59:59", anticipo=200)
    return store.registrar_comprobante(c["folio"], "image", "media-1", ahora=ahora)


def test_hora_del_resumen_es_la_del_negocio():
    """Si el JSON cambia la hora, el disparador la obedece: no hay hora cableada."""
    assert "resumen_hora" in anticipo.cfg(main.DATOS)


def test_antes_de_la_hora_no_manda(a_las):
    enviados = a_las("2026-11-15T19:59:00")
    _deposito()

    main._concentrado_si_toca()

    assert enviados == [] and store.resumen_hecho("2026-11-15") is False


def test_a_la_hora_manda_una_vez_y_lo_marca(a_las):
    enviados = a_las("2026-11-15T20:00:30")
    c = _deposito()

    main._concentrado_si_toca()

    assert len(enviados) == 1 and c["folio"] in enviados[0]
    assert store.resumen_hecho("2026-11-15") is True


def test_no_repite_el_mismo_dia(a_las):
    """El bucle pregunta cada minuto: sin la marca mandaría el mismo reporte 60 veces."""
    enviados = a_las("2026-11-15T20:00:30")
    _deposito()

    main._concentrado_si_toca()
    main._concentrado_si_toca()
    main._concentrado_si_toca()

    assert len(enviados) == 1


def test_si_telegram_falla_no_marca_y_reintenta(a_las):
    enviados = a_las("2026-11-15T20:00:30", telegram_ok=False)
    _deposito()

    main._concentrado_si_toca()

    assert enviados and store.resumen_hecho("2026-11-15") is False


def test_proceso_que_estaba_abajo_manda_al_volver(a_las):
    """Se cayó a las 20:00 y volvió a las 22:10: el concentrado del día no se pierde."""
    enviados = a_las("2026-11-15T22:10:00")
    _deposito()

    main._concentrado_si_toca()

    assert len(enviados) == 1


def test_hora_muerta_no_manda_nada(bd, monkeypatch, a_las):
    """`resumen_hora` vacía o basura es config muerta: mejor nada que un reporte a medianoche."""
    enviados = a_las("2026-11-15T23:30:00")
    monkeypatch.setattr(
        main, "DATOS", {**main.DATOS, "anticipo": {**main.DATOS["anticipo"], "resumen_hora": ""}}
    )
    _deposito()

    main._concentrado_si_toca()

    assert enviados == []


def test_reset_del_dia_permite_reenviar(a_las):
    """Reiniciar la demo borra la marca: sirve para volver a ver el concentrado."""
    enviados = a_las("2026-11-15T20:00:30")
    _deposito()
    main._concentrado_si_toca()

    store.reset()
    _deposito()
    main._concentrado_si_toca()

    assert len(enviados) == 2
