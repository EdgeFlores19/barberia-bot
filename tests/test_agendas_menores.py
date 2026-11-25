"""Tres detalles de las agendas: nombres repetidos estables, baja por tiempo suficiente y firma de confirmadas()."""
from __future__ import annotations

import pytest

from app import config, store


def _cal(cid: str, nombre: str) -> dict:
    return {"calendar_id": cid, "nombre": nombre, "escritura": True}


def _por_id() -> dict[str, str]:
    return {a["calendar_id"]: a["nombre"] for a in store.agendas()}


# ---------- nombres repetidos: no dependen del orden en que Google los devuelva ----------
def test_dos_calendarios_con_el_mismo_nombre_quedan_igual_sin_importar_el_orden_de_google(bd):
    store.agendas_sincronizar([_cal("cal-2", "Ana"), _cal("cal-1", "Ana")])
    asi = _por_id()
    with store._con() as con:
        con.execute("DELETE FROM agendas")

    store.agendas_sincronizar([_cal("cal-1", "Ana"), _cal("cal-2", "Ana")])

    assert _por_id() == asi == {"cal-1": "Ana", "cal-2": "Ana (2)"}


def test_el_nombre_repetido_no_se_intercambia_entre_lecturas(bd):
    store.agendas_sincronizar([_cal("cal-1", "Ana"), _cal("cal-2", "Ana")])

    reporte = store.agendas_sincronizar([_cal("cal-2", "Ana"), _cal("cal-1", "Ana")])    # Google cambió el orden

    assert reporte["renombres"] == [] and _por_id() == {"cal-1": "Ana", "cal-2": "Ana (2)"}


def test_las_agendas_nuevas_de_una_misma_lectura_se_ordenan_por_nombre(bd):
    store.agendas_sincronizar([_cal("cal-s", "Sofía"), _cal("cal-a", "Ana"), _cal("cal-l", "Laura")])

    assert [a["nombre"] for a in store.agendas()] == ["Ana", "Laura", "Sofía"]
    assert [a["orden"] for a in store.agendas()] == [1, 2, 3]


# ---------- la baja tarda lo suficiente ----------
def test_por_defecto_la_baja_tarda_unos_30_minutos():
    assert config.CALENDAR_AGENDAS_AUSENCIAS == 6        # 6 lecturas de 5 min (CALENDAR_AGENDAS_TTL_S)
    assert store.AUSENCIAS_PARA_BAJA == config.CALENDAR_AGENDAS_AUSENCIAS


def test_una_agenda_que_falta_unas_pocas_lecturas_sigue_activa(bd):
    store.agendas_sincronizar([_cal("cal-a", "Ana"), _cal("cal-l", "Laura")])

    for _ in range(store.AUSENCIAS_PARA_BAJA - 1):
        store.agendas_sincronizar([_cal("cal-a", "Ana")])

    assert [a["nombre"] for a in store.agendas(solo_activas=True)] == ["Ana", "Laura"]


def test_la_baja_llega_justo_en_la_lectura_del_umbral(bd):
    store.agendas_sincronizar([_cal("cal-a", "Ana"), _cal("cal-l", "Laura")])

    bajas = []
    for _ in range(store.AUSENCIAS_PARA_BAJA):
        bajas = store.agendas_sincronizar([_cal("cal-a", "Ana")])["bajas"]

    assert [b["nombre"] for b in bajas] == ["Laura"]
    assert [a["nombre"] for a in store.agendas(solo_activas=True)] == ["Ana"]


def test_el_umbral_se_puede_pedir_en_la_llamada(bd):
    store.agendas_sincronizar([_cal("cal-a", "Ana"), _cal("cal-l", "Laura")])

    store.agendas_sincronizar([_cal("cal-a", "Ana")], ausencias_para_baja=1)

    assert [a["nombre"] for a in store.agendas(solo_activas=True)] == ["Ana"]


def test_volver_a_aparecer_la_reactiva_y_reinicia_la_cuenta(bd):
    store.agendas_sincronizar([_cal("cal-a", "Ana"), _cal("cal-l", "Laura")])
    for _ in range(store.AUSENCIAS_PARA_BAJA - 1):
        store.agendas_sincronizar([_cal("cal-a", "Ana")])
    store.agendas_sincronizar([_cal("cal-a", "Ana"), _cal("cal-l", "Laura")])          # reaparece a tiempo

    for _ in range(store.AUSENCIAS_PARA_BAJA - 1):
        store.agendas_sincronizar([_cal("cal-a", "Ana")])

    assert "Laura" in [a["nombre"] for a in store.agendas(solo_activas=True)]          # la cuenta empezó de cero


# ---------- confirmadas(): la firma original, con la agenda por nombre ----------
def test_confirmadas_conserva_ahora_como_primer_argumento(bd):
    store.crear("2026-12-01T10:00", "manicura", "Ana")

    assert [c["nombre"] for c in store.confirmadas("2026-11-30T09:00:00")] == ["Ana"]


def test_una_fecha_posicional_no_se_confunde_con_una_agenda(bd):
    store.crear("2026-12-01T10:00", "manicura", "Ana", agenda_id="ab12cd34")

    # Antes, el primer argumento era la agenda: una fecha ISO pasada ahí filtraba "silenciosamente" a nada.
    assert [c["nombre"] for c in store.confirmadas("2026-11-30T09:00:00")] == ["Ana"]


def test_la_agenda_solo_se_pide_por_nombre(bd):
    with pytest.raises(TypeError):
        store.confirmadas("2026-11-30T09:00:00", "ab12cd34")
