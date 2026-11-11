"""Los eventos que la manicurista crea a mano en Google Calendar bloquean horarios del bot.

Calendar sigue siendo opcional: si falla, el bot agenda con lo que sabe (SQLite) y avisa una vez.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app import calendar_sync, config, engine, reloj
from app import slots as horarios

pytestmark = pytest.mark.usefixtures("bd")


@pytest.fixture()
def datos() -> dict:
    return json.loads(Path(config.DATA_FILE).read_text(encoding="utf-8"))


@pytest.fixture()
def calendario(monkeypatch):
    """Calendar 'encendido' con eventos falsos. `cal.eventos` es lo que devolvería Google."""
    class Cal:
        eventos: list[dict] = []
        falla: Exception | None = None
        llamadas = 0
        alertas: list[str] = []
        reloj_mono = 1000.0

    cal = Cal()
    cal.eventos, cal.alertas = [], []
    monkeypatch.setattr(config, "CALENDAR_ID", "cal-secreto@example.com")
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "{}")
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO", True)
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO_TTL_S", 60.0)
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO_REINTENTO_S", 120.0)

    def listar(desde, hasta):
        cal.llamadas += 1
        if cal.falla:
            raise cal.falla
        return list(cal.eventos)

    monkeypatch.setattr(calendar_sync, "_listar_eventos", listar)
    monkeypatch.setattr(calendar_sync, "_mono", lambda: cal.reloj_mono)
    monkeypatch.setattr(calendar_sync.avisos, "notificar_alerta", cal.alertas.append)
    monkeypatch.setattr(calendar_sync, "crear", lambda cita, d: None)      # sin red
    monkeypatch.setattr(calendar_sync, "mover", lambda ev, cita, d: ev)
    monkeypatch.setattr(calendar_sync, "borrar", lambda ev: True)
    calendar_sync.reiniciar_bloqueos()
    yield cal
    calendar_sync.reiniciar_bloqueos()


def _evento(inicio: str, minutos: int = 60, **extra) -> dict:
    """Evento con hora y offset (CDMX es UTC-6 fijo desde 2022)."""
    ini = datetime.fromisoformat(inicio)
    fin = ini + timedelta(minutes=minutos)
    ev = {
        "start": {"dateTime": ini.isoformat(timespec="seconds") + "-06:00"},
        "end": {"dateTime": fin.isoformat(timespec="seconds") + "-06:00"},
    }
    ev.update(extra)
    return ev


def _ultimo_hueco(datos: dict) -> str:
    return engine.slots_disponibles(datos, "manicura")[-1]


def _huecos_con_margen(datos: dict, dias: int = 2) -> list[str]:
    """Huecos a partir de pasado mañana: mover una cita de las próximas 2 h se escala a la
    manicurista (`cancelacion_libre_hasta_horas`) y no llega a elegir horario."""
    umbral = (reloj.ahora() + timedelta(days=dias)).strftime("%Y-%m-%d")
    return [s for s in engine.slots_disponibles(datos, "manicura") if s[:10] >= umbral]


def _refrescar(forzar: bool = False) -> bool:
    return calendar_sync.refrescar_bloqueos(reloj.ahora(), 7, forzar)


def _decir(sid: str, texto: str, datos: dict):
    return asyncio.run(engine.manejar(sid, texto, datos))


# ------------------------------------------------------------ qué eventos bloquean
def test_un_evento_manual_bloquea_sus_horarios(datos, calendario):
    slot = _ultimo_hueco(datos)
    calendario.eventos = [_evento(slot, 60)]

    _refrescar()
    libres = engine.slots_disponibles(datos, "manicura")

    inicio = datetime.fromisoformat(slot)
    media = (inicio + timedelta(minutes=30)).isoformat(timespec="minutes")
    assert slot not in libres and media not in libres


def test_el_borde_del_evento_no_bloquea_el_hueco_contiguo(datos, calendario):
    slot = _ultimo_hueco(datos)
    calendario.eventos = [_evento(slot, 60)]

    _refrescar()
    libres = engine.slots_disponibles(datos, "manicura")

    despues = (datetime.fromisoformat(slot) + timedelta(minutes=60)).isoformat(timespec="minutes")
    antes = (datetime.fromisoformat(slot) - timedelta(minutes=30)).isoformat(timespec="minutes")
    # El cierre puede caer fuera de horario: solo se afirma lo que sí debería estar libre.
    assert antes in libres or datetime.fromisoformat(antes).hour < 10
    assert despues in libres or datetime.fromisoformat(despues).hour >= 20


def test_un_evento_de_todo_el_dia_bloquea_el_dia(datos, calendario):
    slot = _ultimo_hueco(datos)
    dia = slot[:10]
    siguiente = (datetime.fromisoformat(dia) + timedelta(days=1)).date().isoformat()
    calendario.eventos = [{"start": {"date": dia}, "end": {"date": siguiente}}]

    _refrescar()
    libres = engine.slots_disponibles(datos, "manicura")

    assert not [s for s in libres if s[:10] == dia]
    assert [s for s in libres if s[:10] != dia]          # los demás días siguen abiertos


@pytest.mark.parametrize(
    "extra",
    [
        {"status": "cancelled"},
        {"transparency": "transparent"},                                    # marcado "libre"
        {"description": "Folio ABC1. Reservado por el bot de Ámbar."},      # el espejo del propio bot
        {"attendees": [{"self": True, "responseStatus": "declined"}]},
    ],
)
def test_eventos_que_no_ocupan_a_la_manicurista_no_bloquean(datos, calendario, extra):
    slot = _ultimo_hueco(datos)
    calendario.eventos = [_evento(slot, 60, **extra)]

    _refrescar()

    assert slot in engine.slots_disponibles(datos, "manicura")


def test_el_espejo_del_bot_usa_la_misma_marca_que_se_ignora(datos):
    cita = {"servicio_id": "manicura", "slot": "2026-11-16T10:00", "nombre": "Ana", "folio": "AMB1"}

    evento = calendar_sync._evento(cita, datos)

    assert calendar_sync.MARCA_BOT in evento["description"]


# ------------------------------------------------------------ al reservar
def test_un_evento_manual_de_ultimo_momento_frena_la_reserva(datos, calendario):
    """El cache aún no lo conoce: se revalida al confirmar, que es donde importa."""
    slot = _ultimo_hueco(datos)
    _decir("c1", "agendar", datos)
    _decir("c1", "manicura", datos)            # el menú se calcula con Calendar vacío
    _decir("c1", slot, datos)
    calendario.eventos = [_evento(slot, 60)]   # la manicurista lo anota a mano ahora

    respuestas, s = _decir("c1", "Ana", datos)

    assert s.paso == "agendar_slot"
    assert "acaba de ocupar" in respuestas[0]["texto"]
    assert engine.store.confirmadas() == []


def test_sin_eventos_manuales_la_reserva_sigue_igual(datos, calendario):
    slot = _ultimo_hueco(datos)
    for texto in ("agendar", "manicura", slot, "Ana"):
        _, s = _decir("c2", texto, datos)

    assert s.paso == "fin" and len(engine.store.confirmadas()) == 1


# ------------------------------------------------------------ caché y fallos
def test_dos_consultas_seguidas_usan_el_cache(calendario):
    _refrescar()
    _refrescar()

    assert calendario.llamadas == 1


def test_forzar_consulta_aunque_el_cache_este_vigente(calendario):
    _refrescar()
    _refrescar(forzar=True)

    assert calendario.llamadas == 2


def test_el_cache_vence(calendario):
    _refrescar()
    calendario.reloj_mono += 61

    _refrescar()

    assert calendario.llamadas == 2


def test_si_calendar_falla_el_bot_sigue_agendando_y_avisa_una_vez(datos, calendario):
    calendario.falla = httpx.HTTPError("boom")
    slot = _ultimo_hueco(datos)

    for texto in ("agendar", "manicura", slot, "Ana"):
        _, s = _decir("f1", texto, datos)
    calendario.reloj_mono += 500
    _refrescar()                                             # un segundo fallo, otra ventana

    assert s.paso == "fin" and len(engine.store.confirmadas()) == 1
    assert len(calendario.alertas) == 1


def test_tras_un_fallo_no_se_reintenta_hasta_pasar_el_periodo(calendario):
    calendario.falla = httpx.HTTPError("boom")
    _refrescar()
    _refrescar()
    _refrescar(forzar=True)                  # forzar salta el cache, no el reintento

    assert calendario.llamadas == 1

    calendario.reloj_mono += 121
    _refrescar()

    assert calendario.llamadas == 2


def test_al_recuperarse_avisa_y_vuelve_a_bloquear(datos, calendario):
    calendario.falla = httpx.HTTPError("boom")
    _refrescar()
    calendario.falla = None
    slot = _ultimo_hueco(datos)
    calendario.eventos = [_evento(slot, 60)]
    calendario.reloj_mono += 121

    _refrescar()

    assert slot not in engine.slots_disponibles(datos, "manicura")
    assert len(calendario.alertas) == 2 and "volvió" in calendario.alertas[1]


def test_el_error_expuesto_no_filtra_el_id_del_calendario(calendario):
    calendario.falla = httpx.HTTPError("https://x/calendars/cal-secreto@example.com/events")

    _refrescar()
    bloqueo = calendar_sync.salud()["bloqueo"]

    assert "cal-secreto" not in bloqueo["ultimo_error"]
    assert bloqueo["caido"] is True
    assert all("cal-secreto" not in a for a in calendario.alertas)


def test_sin_calendar_configurado_no_consulta_nada(monkeypatch, datos, calendario):
    monkeypatch.setattr(config, "CALENDAR_ID", "")

    asyncio.run(engine._sincronizar_calendar(datos))

    assert calendario.llamadas == 0 and calendar_sync.bloqueos() == []


def test_se_puede_apagar_el_bloqueo_sin_apagar_el_espejo(monkeypatch, datos, calendario):
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO", False)

    asyncio.run(engine._sincronizar_calendar(datos))

    assert calendario.llamadas == 0 and calendar_sync.habilitado()


# ------------------------------------------------------------ la llamada a Google
def test_la_consulta_a_google_pide_el_rango_con_offset_y_pagina(monkeypatch):
    paginas = [
        {"items": [{"id": "a"}], "nextPageToken": "p2"},
        {"items": [{"id": "b"}]},
    ]
    pedidos: list[dict] = []

    class Resp:
        def __init__(self, cuerpo): self.cuerpo = cuerpo
        def raise_for_status(self): pass
        def json(self): return self.cuerpo

    def falso_get(url, params=None, headers=None, timeout=None):
        pedidos.append({"url": url, "params": dict(params or {})})
        return Resp(paginas[len(pedidos) - 1])

    monkeypatch.setattr(config, "CALENDAR_ID", "cal@example.com")
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "{}")
    monkeypatch.setattr(calendar_sync, "_cabeceras", lambda: {"Authorization": "Bearer x"})
    monkeypatch.setattr(calendar_sync.httpx, "get", falso_get)

    eventos = calendar_sync._listar_eventos(datetime(2026, 11, 10, 9, 0), datetime(2026, 11, 18, 0, 0))

    assert [e["id"] for e in eventos] == ["a", "b"]
    p = pedidos[0]["params"]
    assert p["timeMin"] == "2026-11-10T09:00:00-06:00" and p["timeMax"] == "2026-11-18T00:00:00-06:00"
    assert p["singleEvents"] == "true"                  # expande los recurrentes
    assert pedidos[1]["params"]["pageToken"] == "p2"
    assert "summary" not in p["fields"]                 # no se descargan los títulos


def test_mover_una_cita_revalida_con_calendar_al_elegir_el_horario(datos, calendario):
    libres = _huecos_con_margen(datos)
    viejo, nuevo = libres[0], libres[-1]
    cita = engine.store.crear(viejo, "manicura", "Ana", "")
    _decir("m1", f"mover {cita['folio']}", datos)
    calendario.eventos = [_evento(nuevo, 60)]            # la manicurista lo anota mientras tanto

    respuestas, _ = _decir("m1", nuevo, datos)

    assert "no está disponible" in respuestas[0]["texto"]
    assert engine.store.obtener(cita["folio"])["slot"] == viejo


def test_mover_una_cita_sobre_su_propio_espejo_no_se_bloquea_a_si_misma(datos, calendario):
    """El evento espejo del bot trae la marca: no cuenta como un bloqueo externo."""
    libres = _huecos_con_margen(datos)
    viejo = next(
        s for s in libres
        if (datetime.fromisoformat(s) + timedelta(minutes=30)).isoformat(timespec="minutes") in libres
    )
    cita = engine.store.crear(viejo, "manicura", "Ana", "")
    calendario.eventos = [_evento(viejo, 60, description=f"Folio {cita['folio']}. {calendar_sync.MARCA_BOT} de X.")]
    nuevo = (datetime.fromisoformat(viejo) + timedelta(minutes=30)).isoformat(timespec="minutes")
    assert nuevo in engine.slots_disponibles(datos, "manicura", excluir_folio=cita["folio"])
    _decir("m2", f"mover {cita['folio']}", datos)

    respuestas, _ = _decir("m2", nuevo, datos)

    assert "Movida" in respuestas[0]["texto"]
