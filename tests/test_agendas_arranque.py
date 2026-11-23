"""El primer arranque: las agendas se descubren aunque la tabla esté vacía y no haya GOOGLE_CALENDAR_ID.

Es la configuración que indica la guía de alta (prefijo + token). `habilitado()` exige agendas ya
dadas de alta; si el descubrimiento también lo exigiera, la primera lectura jamás ocurriría y la
tabla nunca se llenaría. Estas pruebas parten de la tabla vacía, que es lo que las otras no hacían.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import calendar_bucle, calendar_sync, config, engine, main, store

PREFIJO = "Agenda · "
CAL_ANA = "cal-ana@group.calendar.google.com"
CAL_LAURA = "cal-laura@group.calendar.google.com"


@pytest.fixture()
def google(bd, monkeypatch):
    """Google falso, la tabla de agendas vacía y la configuración de la guía de alta."""
    class Google:
        llamadas_lista = 0
        calendarios: list[dict] = []
        falla: Exception | None = None

    g = Google()
    g.calendarios = [
        {"id": CAL_ANA, "summary": f"{PREFIJO}Ana", "accessRole": "owner"},
        {"id": CAL_LAURA, "summary": f"{PREFIJO}Laura", "accessRole": "owner"},
        {"id": "otro@group.calendar.google.com", "summary": "Cumpleaños", "accessRole": "owner"},
    ]

    def listar_calendarios():
        g.llamadas_lista += 1
        if g.falla:
            raise g.falla
        return g.calendarios

    monkeypatch.setattr(config, "CALENDAR_ID", "")
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", json.dumps(
        {"token": "t", "refresh_token": "r", "client_id": "c", "client_secret": "s"}))
    monkeypatch.setattr(config, "CALENDAR_BLOQUEO", True)
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", PREFIJO)
    monkeypatch.setattr(calendar_sync, "_access_token", lambda: "tok")
    monkeypatch.setattr(calendar_sync, "_listar_calendarios", listar_calendarios)
    monkeypatch.setattr(calendar_sync, "_listar_eventos", lambda *a, **k: [])
    calendar_sync.reiniciar_agendas()
    calendar_sync.reiniciar_bloqueos()
    yield g
    calendar_sync.reiniciar_agendas()
    calendar_sync.reiniciar_bloqueos()


def _nombres() -> list[str]:
    return [a["nombre"] for a in store.agendas(solo_activas=True)]


# ---------- el primer arranque ----------
def test_con_la_tabla_vacia_se_descubren_las_agendas_y_se_ignora_lo_demas(google):
    datos = engine.cargar_datos()
    assert store.agendas() == []

    calendar_bucle.sincronizar(datos)

    assert _nombres() == ["Ana", "Laura"]               # "Cumpleaños" no tiene el prefijo


def test_antes_de_descubrir_no_hay_espejo_pero_si_descubrimiento(google):
    assert calendar_sync.habilitado() is False           # aún no hay a qué calendario escribir
    assert calendar_sync.multiagenda() is True           # pero sí hay que ir a buscarlos
    assert calendar_sync.necesita_bucle() is True


def test_tras_descubrirlas_el_espejo_y_los_bloqueos_se_encienden(google):
    calendar_bucle.sincronizar(engine.cargar_datos())

    assert calendar_sync.habilitado() is True
    assert calendar_sync.bloqueo_activo() is True


def test_sin_problema_de_configuracion_una_vez_descubiertas(google):
    calendar_bucle.sincronizar(engine.cargar_datos())

    assert not calendar_sync.salud()["problema_config"]


# ---------- lo que NO debe encenderse ----------
def test_sin_prefijo_y_sin_calendario_unico_no_hay_nada_que_hacer(google, monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", "")

    calendar_bucle.sincronizar(engine.cargar_datos())

    assert calendar_sync.necesita_bucle() is False
    assert google.llamadas_lista == 0 and store.agendas() == []


def test_sin_token_no_se_intenta_descubrir_nada(google, monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TOKEN_FILE", "")

    calendar_bucle.sincronizar(engine.cargar_datos())

    assert calendar_sync.multiagenda() is False and calendar_sync.necesita_bucle() is False
    assert google.llamadas_lista == 0


def test_el_calendario_unico_de_siempre_sigue_encendiendo_el_bucle(google, monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", "")
    monkeypatch.setattr(config, "CALENDAR_ID", "unico@group.calendar.google.com")

    assert calendar_sync.necesita_bucle() is True
    assert calendar_sync.multiagenda() is False


def test_un_fallo_en_la_primera_lectura_no_inventa_agendas_ni_tumba_el_bucle(google):
    google.falla = httpx.HTTPError("sin red")

    calendar_bucle.sincronizar(engine.cargar_datos())        # no lanza

    assert store.agendas() == []


# ---------- el arranque de la app ----------
def test_el_arranque_de_la_app_inicia_el_bucle_con_la_configuracion_de_la_guia(google, monkeypatch):
    arrancado: list[bool] = []

    async def bucle_falso(datos):
        arrancado.append(True)
        await asyncio.sleep(3600)

    async def concentrado_falso():
        await asyncio.sleep(3600)

    monkeypatch.setattr(calendar_bucle, "bucle", bucle_falso)
    monkeypatch.setattr(main, "_bucle_concentrado", concentrado_falso)

    async def arrancar():
        async with main._vida(main.app):
            await asyncio.sleep(0)                           # deja correr las tareas recién creadas

    asyncio.run(arrancar())

    assert arrancado == [True]


def test_el_arranque_no_inicia_el_bucle_si_no_hay_nada_que_mantener(google, monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_AGENDAS_PREFIJO", "")
    arrancado: list[bool] = []

    async def bucle_falso(datos):
        arrancado.append(True)

    async def concentrado_falso():
        await asyncio.sleep(3600)

    monkeypatch.setattr(calendar_bucle, "bucle", bucle_falso)
    monkeypatch.setattr(main, "_bucle_concentrado", concentrado_falso)

    async def arrancar():
        async with main._vida(main.app):
            await asyncio.sleep(0)

    asyncio.run(arrancar())

    assert arrancado == []
