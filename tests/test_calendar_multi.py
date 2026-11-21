"""Con varias agendas, cada calendario de Google se lee y se escribe por separado.

El modo actual (una sola agenda) tiene que seguir comportándose igual: sin decir qué
calendario se quiere, todo va a `CALENDAR_ID`. Si uno de los calendarios falla, los demás
siguen leyéndose: el caché de bloqueos y sus errores no se mezclan.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import pytest

from app import calendar_sync, config

ANA = "ana@group.calendar.google.com"
BRENDA = "brenda@group.calendar.google.com"


@pytest.fixture()
def google(monkeypatch):
    """Google falso: apunta qué calendario pidió cada llamada y cuáles fallan."""

    class Google:
        llamadas: list[str] = []
        fallan: set[str] = set()
        eventos: dict[str, list[dict]] = {}
        reloj_mono = 1000.0

    g = Google()
    g.llamadas, g.fallan, g.eventos = [], set(), {}
    monkeypatch.setattr(config, "CALENDAR_ID", ANA)
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "{}")
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO", True)
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO_TTL_S", 60.0)
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO_REINTENTO_S", 120.0)
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "tok")
    monkeypatch.setattr(calendar_sync, "_mono", lambda: g.reloj_mono)
    monkeypatch.setattr(calendar_sync.avisos, "notificar_alerta", lambda texto: None)

    class Respuesta:
        status_code = 200

        def __init__(self, cuerpo: dict):
            self._cuerpo = cuerpo

        def json(self) -> dict:
            return self._cuerpo

        def raise_for_status(self) -> None:
            return None

    def calendario_de(url: str) -> str:
        return url.split("/calendars/", 1)[1].split("/events", 1)[0]

    def revisar(url: str) -> str:
        cal = calendario_de(url)
        g.llamadas.append(url)
        if cal in g.fallan:
            raise httpx.HTTPError("sin red")
        return cal

    def post(url, json=None, headers=None, timeout=None):
        return Respuesta({"id": f"ev-{revisar(url)}"})

    def patch(url, json=None, headers=None, timeout=None):
        revisar(url)
        return Respuesta({"id": "ev"})

    def delete(url, headers=None, timeout=None):
        revisar(url)
        return Respuesta({})

    def get(url, params=None, headers=None, timeout=None):
        return Respuesta({"items": list(g.eventos.get(revisar(url), []))})

    monkeypatch.setattr(calendar_sync.httpx, "post", post)
    monkeypatch.setattr(calendar_sync.httpx, "patch", patch)
    monkeypatch.setattr(calendar_sync.httpx, "delete", delete)
    monkeypatch.setattr(calendar_sync.httpx, "get", get)
    calendar_sync.reiniciar_bloqueos()
    yield g
    calendar_sync.reiniciar_bloqueos()


def _cita(folio: str = "AMB-AAAA") -> dict:
    return {"folio": folio, "slot": "2026-11-17T10:00", "servicio_id": "gelish", "nombre": "Ana"}


DATOS = {
    "nombre": "Ambar",
    "direccion": "Calle 1",
    "servicios": [{"id": "gelish", "nombre": "Gelish", "minutos": 60}],
}


def _evento(inicio: str, minutos: int = 60) -> dict:
    ini = datetime.fromisoformat(inicio)
    fin = ini + timedelta(minutes=minutos)
    return {
        "start": {"dateTime": ini.isoformat(timespec="seconds") + "-06:00"},
        "end": {"dateTime": fin.isoformat(timespec="seconds") + "-06:00"},
    }


# ------------------------------------------------------------------ espejo (bot -> Calendar)
def test_crear_usa_el_calendario_que_se_le_pide(google):
    calendar_sync.crear(_cita(), DATOS, BRENDA)

    assert len(google.llamadas) == 1
    assert BRENDA in google.llamadas[0]


def test_sin_decir_calendario_se_usa_el_de_siempre(google):
    calendar_sync.crear(_cita(), DATOS)

    assert ANA in google.llamadas[0]


def test_borrar_usa_el_calendario_de_la_cita(google):
    assert calendar_sync.borrar("ev-1", BRENDA) is True

    assert f"/calendars/{BRENDA}/events/ev-1" in google.llamadas[0]


def test_mover_usa_el_calendario_de_la_cita(google):
    calendar_sync.mover("ev-1", _cita(), DATOS, BRENDA)

    assert f"/calendars/{BRENDA}/events/ev-1" in google.llamadas[0]


# ------------------------------------------------------------------ bloqueo (Calendar -> bot)
def _refrescar(cal: str | None = None) -> bool:
    return calendar_sync.refrescar_bloqueos(datetime(2026, 11, 16, 10, 0), 7, False, cal)


def test_un_calendario_caido_no_vacia_el_otro(google):
    google.fallan = {ANA}
    google.eventos = {BRENDA: [_evento("2026-11-17T10:00")]}

    assert _refrescar() is False                 # la de siempre no responde
    assert _refrescar(BRENDA) is True            # la otra sí

    assert [b["slot"] for b in calendar_sync.bloqueos(BRENDA)] == ["2026-11-17T10:00"]
    assert calendar_sync.bloqueos() == []


def test_cada_agenda_guarda_su_propio_cache(google):
    google.eventos = {
        ANA: [_evento("2026-11-17T10:00")],
        BRENDA: [_evento("2026-11-17T12:00")],
    }

    assert _refrescar() is True
    assert _refrescar(BRENDA) is True

    assert [b["slot"] for b in calendar_sync.bloqueos()] == ["2026-11-17T10:00"]
    assert [b["slot"] for b in calendar_sync.bloqueos(BRENDA)] == ["2026-11-17T12:00"]


def test_el_reintento_tras_un_fallo_tambien_es_por_calendario(google):
    google.fallan = {ANA}
    google.eventos = {BRENDA: [_evento("2026-11-17T10:00")]}
    _refrescar()

    google.reloj_mono += 1                       # dentro del periodo de reintento
    assert _refrescar() is False
    assert _refrescar(BRENDA) is True            # el que nunca falló no espera


# ------------------------------------------------------------------ /health
def test_salud_resume_por_agenda_sin_ids(google):
    agendas = [
        {"clave": "a1b2c3d4", "nombre": "Ana", "calendar_id": ANA},
        {"clave": "e5f6a7b8", "nombre": "Brenda", "calendar_id": BRENDA},
    ]

    salud = calendar_sync.salud(agendas)

    assert [a["nombre"] for a in salud["agendas"]] == ["Ana", "Brenda"]
    assert [a["clave"] for a in salud["agendas"]] == ["a1b2c3d4", "e5f6a7b8"]
    assert "a1b2c3d4" in json.dumps(salud)
    assert "@group.calendar.google.com" not in json.dumps(salud)
