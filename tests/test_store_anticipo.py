"""Almacén: citas pendientes de anticipo, apartado con vencimiento, comprobante y migración."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from app import config, store

pytestmark = pytest.mark.usefixtures("bd")

SLOT = "2026-11-17T10:00"
T0 = datetime(2026, 11, 16, 10, 0)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _pendiente(slot: str = SLOT, tel: str = "5214491112233", minutos: int = 60, ahora: datetime = T0) -> dict:
    return store.crear(slot, "gelish", "Ana", tel, pendiente_hasta=_iso(ahora + timedelta(minutes=minutos)),
                       anticipo=200, aceptado_en=_iso(ahora), ahora=_iso(ahora))


# ---------- crear pendiente ----------
def test_crear_pendiente_guarda_el_apartado_y_la_aceptacion():
    c = _pendiente()

    assert c["estado"] == "pendiente"
    assert c["anticipo_monto"] == 200
    assert c["vence"] == _iso(T0 + timedelta(minutes=60))
    assert c["aceptado_en"] == _iso(T0)
    assert c["comprobante_en"] is None


def test_una_cita_normal_sigue_igual():
    c = store.crear(SLOT, "gelish", "Ana")

    assert (c["estado"], c["anticipo_monto"], c["vence"], c["reagendadas"]) == ("confirmada", 0, None, 0)


# ---------- el apartado bloquea mientras está vigente ----------
def test_un_apartado_vigente_bloquea_el_horario():
    _pendiente()

    assert SLOT in store.ocupados(ahora=_iso(T0 + timedelta(minutes=30)))
    assert [c["estado"] for c in store.confirmadas(ahora=_iso(T0 + timedelta(minutes=30)))] == ["pendiente"]
    with pytest.raises(store.SlotOcupado):
        store.crear(SLOT, "gelish", "Otra", ahora=_iso(T0 + timedelta(minutes=30)))


def test_un_apartado_vencido_libera_el_horario_sin_necesitar_un_proceso():
    _pendiente()
    despues = _iso(T0 + timedelta(minutes=61))

    assert SLOT not in store.ocupados(ahora=despues)
    assert store.confirmadas(ahora=despues) == []
    otra = store.crear(SLOT, "gelish", "Otra", ahora=despues)           # otra clienta puede tomarlo
    assert otra["estado"] == "confirmada"


def test_expirar_vencidas_marca_solo_las_vencidas_y_las_devuelve():
    vencida = _pendiente("2026-11-17T10:00", minutos=30)
    vigente = _pendiente("2026-11-17T12:00", minutos=120)

    caidas = store.expirar_vencidas(ahora=_iso(T0 + timedelta(minutes=45)))

    assert [c["folio"] for c in caidas] == [vencida["folio"]]
    assert store.obtener(vencida["folio"])["estado"] == "expirada"
    assert store.obtener(vigente["folio"])["estado"] == "pendiente"
    assert store.expirar_vencidas(ahora=_iso(T0 + timedelta(minutes=45))) == []       # idempotente


# ---------- comprobante ----------
def test_registrar_comprobante_confirma_la_cita():
    c = _pendiente()

    r = store.registrar_comprobante(c["folio"], "image", "media-1", ahora=_iso(T0 + timedelta(minutes=10)))

    assert r["estado"] == "confirmada"
    assert (r["comprobante_tipo"], r["comprobante_media_id"]) == ("image", "media-1")
    assert r["comprobante_en"] == _iso(T0 + timedelta(minutes=10))
    assert SLOT in store.ocupados(ahora=_iso(T0 + timedelta(days=1)))        # ya no depende del vencimiento


def test_un_comprobante_tardio_confirma_si_el_horario_sigue_libre():
    c = _pendiente()

    r = store.registrar_comprobante(c["folio"], "document", "media-2", ahora=_iso(T0 + timedelta(hours=3)))

    assert r["estado"] == "confirmada"


def test_un_comprobante_tardio_no_pisa_a_quien_ya_tomo_el_horario():
    c = _pendiente()
    despues = _iso(T0 + timedelta(minutes=90))
    store.crear(SLOT, "gelish", "Otra", ahora=despues)

    with pytest.raises(store.SlotOcupado):
        store.registrar_comprobante(c["folio"], "image", "media-3", ahora=despues)


def test_no_se_registra_comprobante_en_una_cita_que_no_esta_pendiente():
    c = store.crear(SLOT, "gelish", "Ana")

    with pytest.raises(store.EstadoInvalido):
        store.registrar_comprobante(c["folio"], "image", "media-4")


# ---------- buscar el apartado de una clienta por su teléfono ----------
def test_apartado_reciente_encuentra_por_los_ultimos_10_digitos():
    c = _pendiente(tel="5214491112233")

    hallada = store.apartado_reciente("524491112233", ahora=_iso(T0 + timedelta(minutes=5)))

    assert hallada["folio"] == c["folio"]


def test_apartado_reciente_incluye_vencidos_dentro_de_la_gracia_y_no_despues():
    c = _pendiente()

    assert store.apartado_reciente("5214491112233", ahora=_iso(T0 + timedelta(hours=5)))["folio"] == c["folio"]
    assert store.apartado_reciente("5214491112233", ahora=_iso(T0 + timedelta(hours=30))) is None


def test_apartado_reciente_ignora_otras_clientas_y_citas_ya_confirmadas():
    otra = _pendiente(tel="5215500000001")
    store.registrar_comprobante(otra["folio"], "image", "m")

    assert store.apartado_reciente("5214491112233", ahora=_iso(T0 + timedelta(minutes=5))) is None
    assert store.apartado_reciente("5215500000001", ahora=_iso(T0 + timedelta(minutes=5))) is None


# ---------- cancelar y reagendar ----------
def test_cancelar_un_apartado_libera_el_horario():
    c = _pendiente()

    store.cancelar(c["folio"])

    assert SLOT not in store.ocupados(ahora=_iso(T0 + timedelta(minutes=5)))


def test_reagendar_cuenta_las_veces_y_respeta_los_apartados():
    c = store.crear(SLOT, "gelish", "Ana", "5214491112233")
    _pendiente("2026-11-18T10:00", tel="5215500000001")

    with pytest.raises(store.SlotOcupado):
        store.reagendar(c["folio"], "2026-11-18T10:00", ahora=_iso(T0 + timedelta(minutes=5)))
    r = store.reagendar(c["folio"], "2026-11-19T10:00")

    assert (r["slot"], r["reagendadas"]) == ("2026-11-19T10:00", 1)


# ---------- migración de bases que ya existen ----------
def test_una_base_con_el_esquema_viejo_se_migra_sin_perder_citas(tmp_path, monkeypatch):
    viejo = tmp_path / "viejo.db"
    con = sqlite3.connect(viejo)
    con.executescript("""
        CREATE TABLE citas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, folio TEXT NOT NULL UNIQUE, slot TEXT NOT NULL,
            servicio_id TEXT NOT NULL, nombre TEXT NOT NULL, telefono TEXT NOT NULL DEFAULT '',
            estado TEXT NOT NULL DEFAULT 'confirmada' CHECK (estado IN ('confirmada', 'cancelada')),
            creada TEXT NOT NULL, actualizada TEXT NOT NULL, calendar_event_id TEXT);
        CREATE INDEX ix_citas_slot ON citas (slot);
        CREATE INDEX ix_citas_estado ON citas (estado);
        INSERT INTO citas (folio, slot, servicio_id, nombre, telefono, estado, creada, actualizada, calendar_event_id)
        VALUES ('AMB-AAAA', '2026-11-20T10:00', 'gelish', 'Vieja Uno', '5214490000001', 'confirmada', 'c', 'a', 'evt-1'),
               ('AMB-BBBB', '2026-11-21T11:00', 'manicura', 'Vieja Dos', '', 'cancelada', 'c', 'a', NULL);
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(config, "DB_FILE", viejo)
    monkeypatch.setattr(store, "_inicializada", False)

    store.init()

    filas = {c["folio"]: c for c in store.listar()}
    assert set(filas) == {"AMB-AAAA", "AMB-BBBB"}
    assert filas["AMB-AAAA"]["calendar_event_id"] == "evt-1"
    assert (filas["AMB-AAAA"]["anticipo_monto"], filas["AMB-AAAA"]["reagendadas"]) == (0, 0)
    assert filas["AMB-BBBB"]["estado"] == "cancelada"
    nueva = _pendiente("2026-11-22T10:00")                       # ya admite el estado nuevo
    assert nueva["estado"] == "pendiente"


def test_migrar_dos_veces_no_hace_nada_la_segunda(monkeypatch):
    store.crear(SLOT, "gelish", "Ana")
    monkeypatch.setattr(store, "_inicializada", False)

    store.init()

    assert [c["nombre"] for c in store.listar()] == ["Ana"]
