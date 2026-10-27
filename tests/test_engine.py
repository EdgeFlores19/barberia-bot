"""Pruebas del motor: agendar, folio, cancelar y reagendar contra SQLite temporal.

Corre con: .venv/Scripts/python.exe -m pytest tests/test_engine.py -q
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import config, engine, store

CODIGO_SLOT = "%Y-%m-%dT%H:%M"


@pytest.fixture()
def datos() -> dict:
    d = engine.cargar_datos()
    assert d["servicios"], "el JSON debe traer servicios"
    return d


@pytest.fixture(autouse=True)
def bd_temporal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Cada prueba usa su propia base y arranca vacía."""
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.db")
    monkeypatch.setattr(store, "_inicializada", False)
    store.init()
    engine.SESIONES.clear()
    yield
    store.reset()


def _enviar(sid: str, texto: str, datos: dict):
    return asyncio.run(engine.manejar(sid, texto, datos))


def _primer_slot(datos: dict, servicio_id: str = "corte") -> str:
    libres = engine.slots_disponibles(datos, servicio_id)
    assert libres, "debería haber disponibilidad en los próximos 7 días"
    return libres[0]


def _agendar(datos: dict, sid: str = "s1", servicio_id: str = "corte", nombre: str = "Edgar Flores") -> str:
    """Flujo completo por botones. Devuelve el folio."""
    _enviar(sid, "agendar", datos)
    _enviar(sid, servicio_id, datos)
    slot = _primer_slot(datos, servicio_id)
    resp, _ = _enviar(sid, engine._etiqueta_slot(slot), datos)
    assert "nombre" in resp[0]["texto"].lower()
    resp, _ = _enviar(sid, nombre, datos)
    texto = resp[0]["texto"]
    assert "folio" in texto.lower(), texto
    folio = texto.split("Tu folio es ")[1].split(".")[0].strip()
    assert folio.startswith("NVZ-")
    return folio


def test_agendar_guarda_en_sqlite(datos):
    folio = _agendar(datos)
    cita = store.obtener(folio)
    assert cita is not None
    assert cita["estado"] == "confirmada"
    assert cita["nombre"] == "Edgar Flores"
    assert cita["slot"] in store.ocupados()
    assert len(store.listar()) == 1


def test_folio_es_unico_y_sobrevive_a_nueva_sesion(datos):
    folio = _agendar(datos, sid="s1")
    # Otra sesión (otro "cliente") ve la cita por folio: no depende de la memoria.
    engine.SESIONES.clear()
    resp, _ = _enviar("s2", folio, datos)
    resp, _ = _enviar("s2", folio, datos)
    assert folio in resp[0]["texto"]


def test_cancelar_libera_el_slot(datos):
    folio = _agendar(datos)
    slot = store.obtener(folio)["slot"]
    assert slot not in engine.slots_disponibles(datos, "corte")

    resp, _ = _enviar("s1", "menu", datos)
    resp, _ = _enviar("s1", "citas", datos)
    resp, _ = _enviar("s1", folio, datos)
    assert "Mover a otro horario" in [o["label"] for o in resp[1]["opciones"]]

    resp, _ = _enviar("s1", "cancelar", datos)
    assert "Cancelada" in resp[0]["texto"]
    assert store.obtener(folio)["estado"] == "cancelada"
    assert slot in engine.slots_disponibles(datos, "corte")


def test_cancelar_con_folio_en_una_frase(datos):
    folio = _agendar(datos)
    resp, _ = _enviar("s9", f"oye, cancela la {folio} porfa", datos)
    assert "Cancelada" in resp[0]["texto"]
    assert store.obtener(folio)["estado"] == "cancelada"


def test_folio_inexistente_no_rompe(datos):
    resp, _ = _enviar("s1", "citas", datos)
    resp, _ = _enviar("s1", "NVZ-ZZZZ", datos)
    assert "No encuentro" in resp[0]["texto"]


def test_reagendar_mueve_y_libera_el_anterior(datos):
    folio = _agendar(datos)
    viejo = store.obtener(folio)["slot"]
    libres = [x for x in engine.slots_disponibles(datos, "corte", excluir_folio=folio) if x != viejo]
    assert libres, "debe haber otro hueco para mover"
    nuevo = libres[0]

    _enviar("s1", f"mover {folio}", datos)
    resp, _ = _enviar("s1", engine._etiqueta_slot(nuevo), datos)
    assert "Movida" in resp[0]["texto"]

    cita = store.obtener(folio)
    assert cita["slot"] == nuevo
    assert cita["estado"] == "confirmada"
    assert viejo in store.ocupados() | {viejo}  # sigue libre
    assert viejo not in store.ocupados()
    assert nuevo in store.ocupados()


def test_no_se_puede_reservar_dos_veces_el_mismo_slot(datos):
    folio = _agendar(datos, sid="s1")
    slot = store.obtener(folio)["slot"]
    with pytest.raises(store.SlotOcupado):
        store.crear(slot, "corte", "Otra Persona")


def test_slot_ocupado_desaparece_de_las_opciones(datos):
    _agendar(datos, sid="s1")
    engine.SESIONES.clear()
    _enviar("s2", "agendar", datos)
    resp, _ = _enviar("s2", "corte", datos)
    ids = [o["id"] for o in resp[0]["opciones"]]
    ocupado = next(iter(store.ocupados()))
    assert ocupado not in ids


def test_reagendar_tarde_pide_al_barbero(datos):
    """Menos de 2 h antes: no cancela sola, hace handoff."""
    inicio = datetime.now() + timedelta(minutes=45)
    cita = store.crear(inicio.strftime(CODIGO_SLOT), "corte", "Tarde")
    resp, sesion = _enviar("s1", f"cancelar {cita['folio']}", datos)
    assert sesion.handoff is True
    assert store.obtener(cita["folio"])["estado"] == "confirmada"


def test_reagendar_acepta_el_id_del_boton(datos):
    """El botón manda el id ISO ('2026-10-27T10:30'), no la etiqueta legible."""
    folio = _agendar(datos)
    viejo = store.obtener(folio)["slot"]
    nuevo = next(x for x in engine.slots_disponibles(datos, "corte", excluir_folio=folio) if x != viejo)
    resp, _ = _enviar("s1", f"mover {folio}", datos)
    ids = [o["id"] for o in resp[0]["opciones"]]
    assert nuevo in ids
    resp, _ = _enviar("s1", nuevo, datos)
    assert "Movida" in resp[0]["texto"]
    assert store.obtener(folio)["slot"] == nuevo


def test_reset_vacia_la_agenda(datos):
    _agendar(datos)
    store.reset()
    assert store.listar() == []
    assert store.ocupados() == set()
