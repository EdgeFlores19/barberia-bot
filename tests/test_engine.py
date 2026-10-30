"""Pruebas del motor: agendar, folio, cancelar y reagendar contra SQLite temporal.

Corre con: .venv/Scripts/python.exe -m pytest tests/test_engine.py -q
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import config, engine, reloj, store

from tests.telefonos import TEL_A, TEL_A_SIN_1, TEL_B

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


def _primer_slot(datos: dict, servicio_id: str = "manicura") -> str:
    """Primer hueco con margen suficiente para que la cancelación sea libre.

    Sin este margen el test falla si el primer hueco cae dentro de las
    `cancelacion_libre_hasta_horas` y la hora actual está cerca del cierre.
    """
    reglas = datos["reglas_agenda"]
    limite = reloj.ahora() + timedelta(hours=reglas["cancelacion_libre_hasta_horas"] + 1)
    libres = [
        s for s in engine.slots_disponibles(datos, servicio_id)
        if datetime.fromisoformat(s) >= limite
    ]
    assert libres, "debería haber disponibilidad con margen en los próximos 7 días"
    return libres[0]


def _agendar(datos: dict, sid: str = "s1", servicio_id: str = "manicura", nombre: str = "Edgar Flores") -> str:
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
    assert folio.startswith("AMB-")
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
    assert slot not in engine.slots_disponibles(datos, "manicura")

    resp, _ = _enviar("s1", "menu", datos)
    resp, _ = _enviar("s1", "citas", datos)
    resp, _ = _enviar("s1", folio, datos)
    assert "Mover a otro horario" in [o["label"] for o in resp[1]["opciones"]]

    resp, _ = _enviar("s1", "cancelar", datos)
    assert "Cancelada" in resp[0]["texto"]
    assert store.obtener(folio)["estado"] == "cancelada"
    assert slot in engine.slots_disponibles(datos, "manicura")


def test_cancelar_con_folio_en_una_frase(datos):
    folio = _agendar(datos)
    resp, _ = _enviar("s9", f"oye, cancela la {folio} porfa", datos)
    assert "Cancelada" in resp[0]["texto"]
    assert store.obtener(folio)["estado"] == "cancelada"


def test_folio_inexistente_no_rompe(datos):
    resp, _ = _enviar("s1", "citas", datos)
    resp, _ = _enviar("s1", "AMB-ZZZZ", datos)
    assert "No encuentro" in resp[0]["texto"]


def test_reagendar_mueve_y_libera_el_anterior(datos):
    folio = _agendar(datos)
    viejo = store.obtener(folio)["slot"]
    libres = [x for x in engine.slots_disponibles(datos, "manicura", excluir_folio=folio) if x != viejo]
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
        store.crear(slot, "manicura", "Otra Persona")


def test_slot_ocupado_desaparece_de_las_opciones(datos):
    _agendar(datos, sid="s1")
    engine.SESIONES.clear()
    _enviar("s2", "agendar", datos)
    resp, _ = _enviar("s2", "manicura", datos)
    ids = [o["id"] for o in resp[0]["opciones"]]
    ocupado = next(iter(store.ocupados()))
    assert ocupado not in ids


def test_reagendar_tarde_pide_a_la_manicurista(datos):
    """Menos de 2 h antes: no cancela sola, hace handoff."""
    inicio = datetime.now() + timedelta(minutes=45)
    cita = store.crear(inicio.strftime(CODIGO_SLOT), "manicura", "Tarde")
    resp, sesion = _enviar("s1", f"cancelar {cita['folio']}", datos)
    assert sesion.handoff is True
    assert store.obtener(cita["folio"])["estado"] == "confirmada"


def test_reagendar_acepta_el_id_del_boton(datos):
    """El botón manda el id ISO ('2026-10-27T10:30'), no la etiqueta legible."""
    folio = _agendar(datos)
    viejo = store.obtener(folio)["slot"]
    nuevo = next(x for x in engine.slots_disponibles(datos, "manicura", excluir_folio=folio) if x != viejo)
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


# ---------- Tarea 12: canal, teléfono y expiración de sesión ----------
def test_canal_y_telefono_se_guardan_en_la_sesion(datos):
    resp, s = asyncio.run(engine.manejar("s1", "hola", datos, canal="wa", telefono=TEL_A))

    assert s.canal == "wa"
    assert s.telefono == TEL_A
    assert resp


def test_ttl_reinicia_el_paso_a_inicio(datos, monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    engine.SESIONES["s2"] = engine.Sesion("s2", paso="agendar_servicio", servicio_id="manicura")
    engine.SESIONES["s2"].ultima_actividad = reloj.ahora() - timedelta(minutes=31)

    resp, s = asyncio.run(engine.manejar("s2", "hola", datos))

    assert s.paso == "inicio"
    assert s.servicio_id is None
    assert resp


def test_ttl_no_reinicia_dentro_del_limite(datos, monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    engine.SESIONES["s3"] = engine.Sesion("s3", paso="agendar_servicio", servicio_id="manicura")
    engine.SESIONES["s3"].ultima_actividad = reloj.ahora() - timedelta(minutes=5)

    asyncio.run(engine.manejar("s3", "manicura", datos))

    assert engine.SESIONES["s3"].paso != "inicio"


def test_ultima_actividad_se_actualiza_en_cada_mensaje(datos):
    s = engine.SESIONES["s4"] = engine.Sesion("s4", paso="agendar_servicio", servicio_id="manicura")
    s.ultima_actividad = reloj.ahora() - timedelta(minutes=10)

    asyncio.run(engine.manejar("s4", "manicura", datos))

    assert reloj.ahora() - s.ultima_actividad < timedelta(seconds=5)


def test_purga_elimina_sesiones_viejas(datos, monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    engine.SESIONES.clear()
    engine.SESIONES["vieja"] = engine.Sesion("vieja")
    # C7: el tope de la purga es max(TTL*8, TTL de humano) = 12 h por defecto,
    # así que "vieja" tiene que estar más allá de las 12 h para que se borre.
    engine.SESIONES["vieja"].ultima_actividad = reloj.ahora() - timedelta(hours=13)

    asyncio.run(engine.manejar("nueva", "hola", datos))

    assert "vieja" not in engine.SESIONES


# ---------- Tarea 13: el folio solo abre la cita del mismo teléfono ----------
def _cita_ajena(datos: dict, telefono: str = TEL_B) -> dict:
    """Cita en un horario real con margen de cancelación (C9: sin fecha fija)."""
    slot = _primer_slot(datos)
    return store.crear(slot=slot, servicio_id="manicura", nombre="Otro", telefono=telefono)


def test_wa_no_cancela_cita_de_otro_telefono(datos):
    cita = _cita_ajena(datos, TEL_B)

    ajena, _ = asyncio.run(engine.manejar("w1", f"cancelar {cita['folio']}", datos,
                                          canal="wa", telefono=TEL_A))
    inexistente, _ = asyncio.run(engine.manejar("w2", "cancelar AMB-ZZZZ", datos,
                                               canal="wa", telefono=TEL_A))

    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    # Indistinguible de un folio inexistente: no se revela que la cita existe.
    assert ajena == inexistente


def test_wa_cancela_su_propia_cita_aunque_llegue_con_521(datos):
    cita = _cita_ajena(datos, TEL_A)

    resp, _ = asyncio.run(engine.manejar("w3", f"cancelar {cita['folio']}", datos,
                                         canal="wa", telefono=TEL_A_SIN_1))

    assert "Cancelada" in resp[0]["texto"]
    assert store.obtener(cita["folio"])["estado"] == "cancelada"


def test_web_sigue_cancelando_por_folio(datos):
    cita = _cita_ajena(datos, telefono="")

    asyncio.run(engine.manejar("web1", f"cancelar {cita['folio']}", datos))

    assert store.obtener(cita["folio"])["estado"] == "cancelada"


# ---------- C7: TTL de humano y contador de mensajes ----------
def test_ttl_no_reinicia_una_conversacion_en_humano(datos, monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    monkeypatch.setattr(config, "SESION_HUMANO_TTL_MIN", 720)
    s = engine.SESIONES["s5"] = engine.Sesion("s5", paso="humano", canal="wa", telefono=TEL_A)
    s.ultima_actividad = reloj.ahora() - timedelta(minutes=90)

    asyncio.run(engine.manejar("s5", "¿hola?", datos, canal="wa", telefono=TEL_A))

    assert engine.SESIONES["s5"].paso == "humano"


def test_humano_expira_con_su_ttl_largo(datos, monkeypatch):
    monkeypatch.setattr(config, "SESION_HUMANO_TTL_MIN", 720)
    s = engine.SESIONES["s6"] = engine.Sesion("s6", paso="humano", handoff=True)
    s.ultima_actividad = reloj.ahora() - timedelta(hours=13)

    resp, s = asyncio.run(engine.manejar("s6", "hola", datos))

    assert s.paso != "humano" and s.handoff is False
    assert resp


def test_ttl_reinicia_el_contador_de_mensajes(datos, monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    s = engine.SESIONES["s7"] = engine.Sesion("s7", mensajes=39)
    s.ultima_actividad = reloj.ahora() - timedelta(minutes=31)

    asyncio.run(engine.manejar("s7", "hola", datos))

    assert engine.SESIONES["s7"].mensajes == 1


