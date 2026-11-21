"""Almacén: tabla de agendas, migración aditiva de `citas` y reglas de sincronización.

Estas pruebas son de la fase 1 (M1): nada de esto se ve todavía en el flujo del bot.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from app import config, store

pytestmark = pytest.mark.usefixtures("bd")

SLOT = "2026-11-17T10:00"
T0 = datetime(2026, 11, 16, 10, 0)

ANA = "ana@group.calendar.google.com"
BRENDA = "brenda@group.calendar.google.com"


def _iso(dt: datetime = T0) -> str:
    return dt.isoformat(timespec="seconds")


def _calendario(calendar_id: str, nombre: str, escritura: bool = True) -> dict:
    return {"calendar_id": calendar_id, "nombre": nombre, "escritura": escritura}


def _clave(calendar_id: str) -> str:
    return hashlib.sha1(calendar_id.encode("utf-8")).hexdigest()[:8]


# ---------- migración ----------
def test_init_crea_la_tabla_de_agendas():
    columnas = {f["name"] for f in _columnas("agendas")}

    assert {"calendar_id", "clave", "nombre", "orden", "activa", "ausencias", "alta"} <= columnas


def test_init_agrega_las_columnas_de_agenda_a_citas():
    columnas = {f["name"] for f in _columnas("citas")}

    assert {"agenda_id", "agenda_nombre"} <= columnas


def test_init_es_idempotente_y_no_pierde_filas():
    cita = store.crear(SLOT, "gelish", "Ana", "5214491112233")

    store._inicializada = False
    store.init()
    store._inicializada = False
    store.init()

    assert [c["folio"] for c in store.listar()] == [cita["folio"]]


def test_una_base_anterior_se_migra_y_las_citas_viejas_quedan_sin_agenda():
    """Base con la forma previa a este plan: se migra sin perder filas y sin inventar agenda."""
    store._inicializada = False
    vieja = config.DB_FILE
    con = sqlite3.connect(vieja)
    con.execute("DROP TABLE IF EXISTS citas")
    con.execute(
        "CREATE TABLE citas ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, folio TEXT NOT NULL UNIQUE, slot TEXT NOT NULL,"
        " servicio_id TEXT NOT NULL, nombre TEXT NOT NULL, telefono TEXT NOT NULL DEFAULT '',"
        " estado TEXT NOT NULL DEFAULT 'confirmada'"
        " CHECK (estado IN ('confirmada', 'cancelada', 'pendiente', 'expirada')),"
        " creada TEXT NOT NULL, actualizada TEXT NOT NULL, calendar_event_id TEXT)"
    )
    con.execute(
        "INSERT INTO citas (folio, slot, servicio_id, nombre, telefono, estado, creada, actualizada)"
        " VALUES ('AMB-VIEJA', ?, 'gelish', 'Ana', '5214491112233', 'confirmada', ?, ?)",
        (SLOT, _iso(), _iso()),
    )
    con.commit()
    con.close()

    store.init()

    citas = store.listar()
    assert [c["folio"] for c in citas] == ["AMB-VIEJA"]
    assert (citas[0]["agenda_id"], citas[0]["agenda_nombre"]) == ("", "")
    assert {"agenda_id", "agenda_nombre"} <= {f["name"] for f in _columnas("citas")}


# ---------- alta ----------
def test_una_lista_de_calendarios_da_de_alta_las_agendas_en_orden():
    reporte = store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Brenda")])

    agendas = store.agendas()
    assert [(a["nombre"], a["orden"], a["activa"]) for a in agendas] == [("Ana", 1, 1), ("Brenda", 2, 1)]
    assert [a["clave"] for a in agendas] == [_clave(ANA), _clave(BRENDA)]
    assert [a["nombre"] for a in reporte["altas"]] == ["Ana", "Brenda"]


def test_la_clave_es_lo_unico_que_se_puede_mandar_al_cliente():
    store.agendas_sincronizar([_calendario(ANA, "Ana")])

    agenda = store.agenda_por_clave(_clave(ANA))

    assert agenda["calendar_id"] == ANA
    assert agenda["clave"] == _clave(ANA)
    assert len(agenda["clave"]) == 8


def test_sincronizar_dos_veces_el_mismo_calendario_no_duplica():
    store.agendas_sincronizar([_calendario(ANA, "Ana")])

    reporte = store.agendas_sincronizar([_calendario(ANA, "Ana")])

    assert (len(store.agendas()), reporte["altas"]) == (1, [])


# ---------- renombre ----------
def test_cambiar_el_nombre_del_calendario_renombra_la_agenda():
    store.agendas_sincronizar([_calendario(ANA, "Ana")])

    reporte = store.agendas_sincronizar([_calendario(ANA, "Anahí")])

    assert store.agenda_de(ANA)["nombre"] == "Anahí"
    assert [a["nombre"] for a in reporte["renombres"]] == ["Anahí"]


# ---------- ausencias y baja suave ----------
def test_una_ausencia_no_da_de_baja_y_la_segunda_si():
    store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Brenda")])

    store.agendas_sincronizar([_calendario(BRENDA, "Brenda")])
    assert store.agenda_de(ANA)["activa"] == 1
    assert store.agenda_de(ANA)["ausencias"] == 1

    reporte = store.agendas_sincronizar([_calendario(BRENDA, "Brenda")])
    assert store.agenda_de(ANA)["activa"] == 0
    assert [a["nombre"] for a in reporte["bajas"]] == ["Ana"]


def test_una_agenda_dada_de_baja_que_reaparece_vuelve_sin_ausencias():
    store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Brenda")])
    store.agendas_sincronizar([_calendario(BRENDA, "Brenda")])
    store.agendas_sincronizar([_calendario(BRENDA, "Brenda")])

    reporte = store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Brenda")])

    agenda = store.agenda_de(ANA)
    assert (agenda["activa"], agenda["ausencias"]) == (1, 0)
    assert [a["nombre"] for a in reporte["reactivadas"]] == ["Ana"]


def test_una_lista_vacia_cuando_ya_habia_agendas_es_un_error_y_no_cambia_nada():
    store.agendas_sincronizar([_calendario(ANA, "Ana")])

    reporte = store.agendas_sincronizar([], lectura_ok=False)

    agenda = store.agenda_de(ANA)
    assert (agenda["activa"], agenda["ausencias"]) == (1, 0)
    assert reporte["degradado"] is True
    assert reporte["bajas"] == []


def test_una_lectura_fallida_no_da_de_baja_ni_cuenta_ausencias():
    store.agendas_sincronizar([_calendario(ANA, "Ana")])

    reporte = store.agendas_sincronizar(None, lectura_ok=False)

    assert store.agenda_de(ANA)["ausencias"] == 0
    assert reporte["degradado"] is True


# ---------- nombres ----------
def test_dos_calendarios_con_el_mismo_nombre_reciben_sufijo():
    store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Ana")])

    assert [a["nombre"] for a in store.agendas()] == ["Ana", "Ana (2)"]


def test_el_nombre_cualquiera_esta_reservado():
    store.agendas_sincronizar([_calendario(ANA, "Cualquiera")])

    assert store.agenda_de(ANA)["nombre"] == "Cualquiera (2)"


def test_un_calendario_sin_permiso_de_escritura_se_excluye_y_se_avisa():
    reporte = store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Brenda", escritura=False)])

    assert store.agenda_de(ANA) is not None
    assert store.agenda_de(BRENDA) is None
    assert [a["nombre"] for a in reporte["sin_permiso"]] == ["Brenda"]
    assert reporte["degradado"] is False


# ---------- consultas del motor ----------
def test_agendas_activas_deja_fuera_las_dadas_de_baja():
    store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Brenda")])
    store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Brenda")])
    store.agendas_sincronizar([_calendario(ANA, "Ana")])
    store.agendas_sincronizar([_calendario(ANA, "Ana")])

    assert [a["nombre"] for a in store.agendas(solo_activas=True)] == ["Ana"]


def test_una_agenda_se_busca_por_su_id_y_no_por_su_nombre():
    store.agendas_sincronizar([_calendario(ANA, "Ana")])

    assert store.agenda_de("Ana") is None


def _columnas(tabla: str) -> list[sqlite3.Row]:
    con = sqlite3.connect(Path(config.DB_FILE))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(f"PRAGMA table_info({tabla})").fetchall()
    finally:
        con.close()
