"""Con varias agendas, cada una lleva sus citas, su espejo y sus bloqueos.

Lo que se agenda con Ana no le quita el horario a Brenda, y el evento espejo se crea en
el calendario de la agenda de la cita. Sin agenda (el modo de siempre) todo sigue igual:
un solo calendario, un solo conjunto de horarios ocupados y ni una migración.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest

from app import calendar_sync, config, engine, reloj, store

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


class _Respuesta:
    status_code = 200

    def __init__(self, cuerpo: dict):
        self._cuerpo = cuerpo

    def json(self) -> dict:
        return self._cuerpo

    def raise_for_status(self) -> None:
        return None


class _GoogleFalso:
    """events.list de mentira: un evento manual por calendario, o un fallo de red."""

    def __init__(self, eventos: dict[str, list[dict]], fallar: tuple[str, ...] = ()):
        self.eventos = eventos
        self.fallar = set(fallar)
        self.llamadas: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        cal = ANA if ANA in url else BRENDA
        self.llamadas.append(cal)
        if cal in self.fallar:
            raise httpx.HTTPError("sin red")
        return _Respuesta({"items": list(self.eventos.get(cal, []))})


def _evento_manual(slot: str) -> dict:
    """Un evento escrito a mano en el calendario de esa agenda (no lo creó el bot)."""
    ini = datetime.fromisoformat(slot)
    return {
        "description": "cita en el consultorio",
        "start": {"dateTime": ini.isoformat(timespec="minutes")},
        "end": {"dateTime": (ini + timedelta(hours=1)).isoformat(timespec="minutes")},
    }


@pytest.fixture()
def bloqueos(monkeypatch):
    """Deja el bloqueo por calendario encendido y con Google de mentira."""
    monkeypatch.setattr(config, "CALENDAR_ID", ANA)
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO", True)
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "{}")
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO_TTL_S", 60.0)
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO_REINTENTO_S", 120.0)
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "tok")
    monkeypatch.setattr(calendar_sync, "_mono", lambda: reloj.ahora())
    monkeypatch.setattr(calendar_sync.avisos, "notificar_alerta", lambda texto: None)
    calendar_sync.reiniciar_bloqueos()
    yield
    calendar_sync.reiniciar_bloqueos()


def test_un_evento_manual_solo_bloquea_el_horario_de_su_agenda(datos, agenda, bloqueos, monkeypatch):
    slot_ana = _primer_slot(datos)
    google = _GoogleFalso({ANA: [_evento_manual(slot_ana)]})
    monkeypatch.setattr(calendar_sync.httpx, "get", google.get)

    assert calendar_sync.refrescar_bloqueos(reloj.ahora(), 7, forzar=True, calendar_id=ANA) is True
    calendar_sync.reiniciar_bloqueos(BRENDA)  # Brenda sigue sin leer nada de Google

    assert slot_ana not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"])
    assert slot_ana in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])


def test_si_el_calendario_de_ana_falla_brenda_sigue_disponible(datos, agenda, bloqueos, monkeypatch):
    slot = _primer_slot(datos)
    google = _GoogleFalso({BRENDA: [_evento_manual(slot)]}, fallar=(ANA,))
    monkeypatch.setattr(calendar_sync.httpx, "get", google.get)

    assert calendar_sync.refrescar_bloqueos(reloj.ahora(), 7, forzar=True, calendar_id=ANA) is False
    assert calendar_sync.refrescar_bloqueos(reloj.ahora(), 7, forzar=True, calendar_id=BRENDA) is True

    # Ana se queda sin bloqueos (cae a lo que hay en SQLite, que está vacío): no se inventa
    # un horario ocupado ni se cae el bot.
    assert slot in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["ana"])
    # Brenda sí ve su evento manual y no ofrece ese hueco.
    assert slot not in engine.slots_disponibles(datos, "manicura", agenda_id=agenda["brenda"])
