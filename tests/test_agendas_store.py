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
def test_las_ausencias_se_cuentan_y_la_baja_llega_justo_en_el_umbral():
    store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Brenda")])

    store.agendas_sincronizar([_calendario(BRENDA, "Brenda")])
    assert store.agenda_de(ANA)["activa"] == 1
    assert store.agenda_de(ANA)["ausencias"] == 1

    for _ in range(store.AUSENCIAS_PARA_BAJA - 2):                  # hasta una antes del umbral: sigue activa
        store.agendas_sincronizar([_calendario(BRENDA, "Brenda")])
    assert store.agenda_de(ANA)["activa"] == 1

    reporte = store.agendas_sincronizar([_calendario(BRENDA, "Brenda")])        # la lectura del umbral
    assert store.agenda_de(ANA)["activa"] == 0
    assert [a["nombre"] for a in reporte["bajas"]] == ["Ana"]


def test_una_agenda_dada_de_baja_que_reaparece_vuelve_sin_ausencias():
    store.agendas_sincronizar([_calendario(ANA, "Ana"), _calendario(BRENDA, "Brenda")])
    for _ in range(store.AUSENCIAS_PARA_BAJA):
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
    for _ in range(store.AUSENCIAS_PARA_BAJA):
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


# ---------- M2: el choque de horario es por agenda ----------
OTRO = "2026-11-17T11:00"


def test_dos_agendas_distintas_pueden_vender_la_misma_hora():
    a = store.crear(SLOT, "gelish", "Ana", "5214491112233", agenda_id=ANA, agenda_nombre="Ana")
    b = store.crear(SLOT, "gelish", "Brenda", "5214491112244", agenda_id=BRENDA, agenda_nombre="Brenda")

    assert a["agenda_id"] == ANA and b["agenda_id"] == BRENDA
    assert store.ocupados(agenda_id=ANA) == {SLOT}
    assert store.ocupados(agenda_id=BRENDA) == {SLOT}


def test_la_misma_agenda_no_puede_vender_dos_veces_la_misma_hora():
    store.crear(SLOT, "gelish", "Ana", "5214491112233", agenda_id=ANA)

    with pytest.raises(store.SlotOcupado):
        store.crear(SLOT, "gelish", "Brenda", "5214491112244", agenda_id=ANA)


def test_la_agenda_de_la_cita_se_guarda_copiada_no_se_deduce_despues():
    cita = store.crear(SLOT, "gelish", "Ana", "5214491112233", agenda_id=ANA, agenda_nombre="Ana")

    assert (cita["agenda_id"], cita["agenda_nombre"]) == (ANA, "Ana")


def test_reagendar_juzga_el_choque_dentro_de_su_agenda():
    mia = store.crear(SLOT, "gelish", "Ana", "5214491112233", agenda_id=ANA)
    store.crear(OTRO, "gelish", "Brenda", "5214491112244", agenda_id=BRENDA)

    movida = store.reagendar(mia["folio"], OTRO, ahora=_iso(T0))
    assert (movida["slot"], movida["agenda_id"]) == (OTRO, ANA)

    store.crear("2026-11-17T12:00", "gelish", "Otra", "5214491112245", agenda_id=ANA)
    with pytest.raises(store.SlotOcupado):
        store.reagendar(mia["folio"], "2026-11-17T12:00", ahora=_iso(T0))


def test_un_apartado_bloquea_solo_su_agenda():
    store.crear(SLOT, "gelish", "Ana", "5214491112233", agenda_id=ANA,
                pendiente_hasta="2026-11-16T12:00")

    assert store.ocupados(agenda_id=ANA, ahora=_iso()) == {SLOT}
    assert store.ocupados(agenda_id=BRENDA, ahora=_iso()) == set()
    store.crear(SLOT, "gelish", "Brenda", "5214491112244", agenda_id=BRENDA)


def test_el_comprobante_juzga_el_choque_dentro_de_su_agenda():
    apartada = store.crear(SLOT, "gelish", "Ana", "5214491112233", agenda_id=ANA,
                           pendiente_hasta="2026-11-16T23:00")
    despues = "2026-11-17T09:00"
    store.crear(SLOT, "gelish", "Brenda", "5214491112244", agenda_id=BRENDA, ahora=despues)

    r = store.registrar_comprobante(apartada["folio"], "image", "m1", ahora=despues)

    assert (r["estado"], r["agenda_id"]) == ("confirmada", ANA)


def test_un_comprobante_tardio_no_pisa_a_quien_tomo_el_horario_en_su_agenda():
    apartada = store.crear(OTRO, "gelish", "Ana", "5214491112233", agenda_id=ANA,
                           pendiente_hasta="2026-11-16T23:00")
    despues = "2026-11-17T09:00"
    store.crear(OTRO, "gelish", "Brenda", "5214491112244", agenda_id=ANA, ahora=despues)

    with pytest.raises(store.SlotOcupado):
        store.registrar_comprobante(apartada["folio"], "image", "m2", ahora=despues)


def test_confirmadas_filtra_por_agenda():
    store.crear(SLOT, "gelish", "Ana", "5214491112233", agenda_id=ANA)
    store.crear(OTRO, "gelish", "Brenda", "5214491112244", agenda_id=BRENDA)

    assert [c["slot"] for c in store.confirmadas(agenda_id=ANA)] == [SLOT]
    assert [c["slot"] for c in store.confirmadas(agenda_id=BRENDA)] == [OTRO]
    assert len(store.confirmadas()) == 2


def test_sin_agenda_todo_se_comporta_como_hoy():
    store.crear(SLOT, "gelish", "Ana", "5214491112233")

    with pytest.raises(store.SlotOcupado):
        store.crear(SLOT, "gelish", "Brenda", "5214491112244")
    with pytest.raises(store.SlotOcupado):
        store.crear(SLOT, "gelish", "Brenda", "5214491112244", agenda_id=ANA)
    assert store.ocupados() == {SLOT}


def test_una_cita_sin_agenda_ocupa_en_todas_las_agendas():
    """C1 de la revisión: las citas viejas con agenda_id='' no pueden volver a venderse."""
    store.crear(SLOT, "gelish", "Ana", "5214491112233")

    assert store.ocupados(agenda_id=ANA) == {SLOT}
    assert store.ocupados(agenda_id=BRENDA) == {SLOT}
    assert [c["slot"] for c in store.confirmadas(agenda_id=ANA)] == [SLOT]
    with pytest.raises(store.SlotOcupado):
        store.crear(SLOT, "gelish", "Brenda", "5214491112244", agenda_id=BRENDA)


def test_una_base_que_ya_existia_gana_las_columnas_de_agenda():
    """La base de producción ya tiene citas: las columnas nuevas entran con DEFAULT ''. """
    con = sqlite3.connect(Path(config.DB_FILE))
    con.executescript(
        "DROP TABLE citas;"
        "CREATE TABLE citas (id INTEGER PRIMARY KEY AUTOINCREMENT, folio TEXT NOT NULL UNIQUE,"
        " slot TEXT NOT NULL, servicio_id TEXT NOT NULL, nombre TEXT NOT NULL,"
        " telefono TEXT NOT NULL DEFAULT '', estado TEXT NOT NULL DEFAULT 'confirmada'"
        " CHECK (estado IN ('confirmada', 'cancelada', 'pendiente', 'expirada')),"
        " creada TEXT NOT NULL, actualizada TEXT NOT NULL, calendar_event_id TEXT,"
        " anticipo_monto INTEGER NOT NULL DEFAULT 0, vence TEXT, aceptado_en TEXT,"
        " comprobante_en TEXT, comprobante_tipo TEXT, comprobante_media_id TEXT,"
        " reagendadas INTEGER NOT NULL DEFAULT 0);"
        "INSERT INTO citas (folio, slot, servicio_id, nombre, estado, creada, actualizada)"
        " VALUES ('AMB-AAAA', '2026-11-17T10:00', 'gelish', 'Ana', 'confirmada', 'x', 'x');"
    )
    con.commit()
    con.close()
    store._inicializada = False

    vieja = store.ocupados(agenda_id=ANA)

    assert vieja == {"2026-11-17T10:00"}
    con = sqlite3.connect(Path(config.DB_FILE))
    columnas = {f[1] for f in con.execute("PRAGMA table_info(citas)")}
    con.close()
    assert {"agenda_id", "agenda_nombre"} <= columnas


# ---------- citas que ocupan agenda, con todas las columnas ----------
def test_vigentes_trae_confirmadas_y_apartados_vigentes_con_todas_las_columnas(bd):
    from datetime import timedelta
    from app import reloj

    ahora = reloj.ahora()
    futuro = (ahora + timedelta(hours=1)).isoformat(timespec="seconds")
    pasado = (ahora - timedelta(hours=1)).isoformat(timespec="seconds")
    confirmada = store.crear("2026-12-01T10:00", "manicura", "Ana", "5214491112233", agenda_id="ab12cd34")
    store.crear("2026-12-01T11:00", "manicura", "Vigente", "5210000000002", pendiente_hasta=futuro, anticipo=200)
    store.crear("2026-12-01T12:00", "manicura", "Vencido", "5210000000003", pendiente_hasta=pasado, anticipo=200)
    cancelada = store.crear("2026-12-01T13:00", "manicura", "Cancelada", "5210000000004")
    store.cancelar(cancelada["folio"])

    filas = store.vigentes()

    assert sorted(f["nombre"] for f in filas) == ["Ana", "Vigente"]
    ana = next(f for f in filas if f["nombre"] == "Ana")
    assert (ana["telefono"], ana["agenda_id"], ana["folio"]) == ("5214491112233", "ab12cd34", confirmada["folio"])
