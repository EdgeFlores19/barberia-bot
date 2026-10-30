"""Topes, allowlist y textos propios del canal WhatsApp (Tarea 16).

El canal WhatsApp no comparte el presupuesto ni los textos de la demo web:
tiene su propia tabla de uso (`wa_uso`), allowlist por teléfono y avisos
redactados para un cliente real, no para un visitante de la demo.
"""
from __future__ import annotations

import asyncio

from app import config, engine

from tests.telefonos import TEL_A, TEL_B, TEL_C

DATOS = engine.cargar_datos()


def _wa(texto: str, tel: str = TEL_A):
    return asyncio.run(engine.manejar(f"wa:{tel}", texto, DATOS, canal="wa", telefono=tel))


def _texto(resp) -> str:
    return " ".join(r.get("texto", "") for r in resp).lower()


def test_wa_usa_su_propio_tope_de_sesion(bd, monkeypatch):
    # El tope de la demo web (1) no aplica: manda MAX_MENSAJES_POR_SESION_WA (2).
    monkeypatch.setattr(config, "MAX_MENSAJES_POR_SESION", 1)
    monkeypatch.setattr(config, "MAX_MENSAJES_POR_SESION_WA", 2)

    _wa("hola")
    _wa("hola")
    resp, _ = _wa("hola")

    assert "mañana" in _texto(resp)
    assert "demo" not in _texto(resp) and "recarga" not in _texto(resp)


def test_web_bloqueado_no_gasta_presupuesto(bd, monkeypatch):
    # El presupuesto global se incrementa después del gate de sesión: un
    # mensaje bloqueado no debe consumirlo (hoy se incrementa antes: bug).
    monkeypatch.setattr(config, "MAX_MENSAJES_POR_SESION", 1)
    engine.SESIONES["web-x"] = engine.Sesion("web-x", mensajes=5)
    antes = engine.USO_DIA["n"]

    asyncio.run(engine.manejar("web-x", "hola", DATOS))

    assert engine.USO_DIA["n"] == antes


def test_allowlist_deja_pasar_a_los_permitidos_y_bloquea_al_resto(bd, monkeypatch):
    # La allowlist compara el teléfono normalizado (t10): un número de 13
    # dígitos en la lista no puede bloquear a su propio dueño.
    monkeypatch.setattr(config, "WA_ALLOWLIST", config._lista_telefonos(TEL_A))

    permitido, _ = _wa("hola", tel=TEL_A)
    bloqueado, _ = _wa("hola", tel=TEL_B)

    assert "no puedo atender" not in _texto(permitido)
    assert "no puedo atender" in _texto(bloqueado)


def test_limite_por_telefono_por_hora(bd, monkeypatch):
    monkeypatch.setattr(config, "WA_MAX_POR_HORA", 2)

    _wa("hola")
    _wa("hola")
    resp, _ = _wa("hola")

    # 2 pasan y el 3.º se bloquea: el mensaje bloqueado no se suma antes de
    # decidir, así que el contador no se "autodispara".
    assert "espera" in _texto(resp)


def test_tope_diario_por_telefono_y_global(bd, monkeypatch):
    # Primero el tope diario por teléfono: el segundo mensaje de TEL_A se
    # bloquea aunque el global tenga margen de sobra.
    monkeypatch.setattr(config, "WA_MAX_POR_HORA", 100)
    monkeypatch.setattr(config, "WA_MAX_POR_DIA", 1)
    monkeypatch.setattr(config, "WA_MAX_GLOBAL_POR_DIA", 100)

    _wa("hola", tel=TEL_A)
    resp, _ = _wa("hola", tel=TEL_A)

    assert "mañana" in _texto(resp)

    # Ahora el tope global: el día ya lleva 1 mensaje (el de TEL_A), así que
    # con un global de 2 pasa TEL_B y se bloquea TEL_C.
    monkeypatch.setattr(config, "WA_MAX_POR_DIA", 100)
    monkeypatch.setattr(config, "WA_MAX_GLOBAL_POR_DIA", 2)

    _wa("hola", tel=TEL_B)
    resp, _ = _wa("hola", tel=TEL_C)

    assert "mañana" in _texto(resp)
