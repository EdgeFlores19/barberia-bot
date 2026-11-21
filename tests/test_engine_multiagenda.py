"""Con varias agendas, cada una lleva sus citas, su espejo y sus bloqueos.

Lo que se agenda con Ana no le quita el horario a Brenda, y el evento espejo se crea en
el calendario de la agenda de la cita. Sin agenda (el modo de siempre) todo sigue igual:
un solo calendario, un solo conjunto de horarios ocupados y ni una migración.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from app import engine, reloj, store
from app import calendar_sync

ANA = "ana@group.calendar.google.com"
BRENDA = "brenda@group.calendar.google.com"

pytestmark = pytest.mark.usefixtures("bd")


@pytest.fixture()
def datos() -> dict:
    return engine.cargar_datos()


@pytest.fixture()
def agenda() -> dict[str, str]:
    """Dos agendas dadas de alta con sus claves cortas (sin hablar con Google)."""
    store.agendas_sincronizar(
        [{"calendar_id": ANA, "nombre": "Ana"}, {"calendar_id": BRENDA, "nombre": "Brenda"}]
    )
    return {
        "ana": store.agenda_de(ANA)["clave"],
        "brenda": store.agenda_de(BRENDA)["clave"],
    }


def _enviar(sid: str, texto: str, datos: dict):
    return asyncio.run(engine.manejar(sid, texto, datos))


def _primer_slot(datos: dict, servicio_id: str = "manicura") -> str:
    """Primer hueco con margen de sobra (si no, cae en la ventana de cancelación)."""
    limite = reloj.ahora() + timedelta(hours=datos["reglas_agenda"]["cancelacion_libre_hasta_horas"] + 1)
    libres = [s for s in engine.slots_disponibles(datos, servicio_id) if datetime.fromisoformat(s) >= limite]
    assert libres, "debería haber disponibilidad con margen en los próximos 7 días"
    return libres[0]


def test_una_cita_no_le_quita_el_horario_a_la_otra_agenda(datos, agenda):
    slot = _primer_slot(datos)
    store.crear(slot, "manicura", "Clienta de Ana", agenda_id=agenda["ana"])

    assert slot not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"])
    assert slot in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])
    # El modo de siempre (sin agenda) sigue viendo todo lo vendido, como antes.
    assert slot not in engine.slots_disponibles(datos, "manicura")


def test_sin_agenda_la_cita_bloquea_a_todas(datos, agenda):
    """Las citas viejas, sin agenda, no pueden reaparecer libres al encender el modo nuevo."""
    slot = _primer_slot(datos)
    store.crear(slot, "manicura", "Clienta de siempre")

    assert slot not in engine.slots_disponibles(datos, "manicura")
    assert slot not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"])
    assert slot not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])


def test_el_evento_espejo_va_al_calendario_de_la_agenda(datos, agenda, monkeypatch):
    vistos: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        calendar_sync, "crear",
        lambda cita, d, calendar_id=None: vistos.append((cita["folio"], calendar_id)) or "ev-ana",
    )
    # Sin Calendar habilitado el motor ni lo intenta: basta con que la creación pase por aquí.
    monkeypatch.setattr(calendar_sync, "habilitado", lambda: True)

    _enviar("s1", "agendar", datos)
    _enviar("s1", "manicura", datos)
    engine.SESIONES["s1"].agenda_id = agenda["ana"]
    slot = _primer_slot(datos)
    _enviar("s1", engine._etiqueta_slot(slot), datos)
    resp, _ = _enviar("s1", "Edgar Flores", datos)

    folio = resp[0]["texto"].split("Tu folio es ")[1].split(".")[0].strip()
    assert vistos == [(folio, ANA)]
    cita = store.obtener(folio)
    assert cita["agenda_id"] == agenda["ana"]
    assert cita["agenda_nombre"] == "Ana"


def test_sin_agenda_el_espejo_usa_el_calendario_de_siempre(datos, monkeypatch):
    vistos: list[str | None] = []
    monkeypatch.setattr(
        calendar_sync, "crear",
        lambda cita, d, calendar_id=None: vistos.append(calendar_id) or "ev-1",
    )
    monkeypatch.setattr(calendar_sync, "habilitado", lambda: True)

    _enviar("s1", "agendar", datos)
    _enviar("s1", "manicura", datos)
    slot = _primer_slot(datos)
    _enviar("s1", engine._etiqueta_slot(slot), datos)
    _enviar("s1", "Edgar Flores", datos)

    assert vistos == [None], "sin agenda se usa CALENDAR_ID, como siempre"


def test_los_bloqueos_de_calendar_se_leen_por_agenda(datos, agenda, monkeypatch):
    pedidos: list[str | None] = []
    monkeypatch.setattr(
        calendar_sync, "bloqueos", lambda calendar_id=None: pedidos.append(calendar_id) or []
    )

    engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])
    assert pedidos == [BRENDA]

    engine.slots_disponibles(datos, "manicura")
    assert pedidos[-1] is None
