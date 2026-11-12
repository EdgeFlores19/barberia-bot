"""Agendar en dos pasos: servicio -> día -> horario de ese día."""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from app import config, engine, store

pytestmark = pytest.mark.usefixtures("bd")

MAX_FILAS_WA = 10


@pytest.fixture()
def datos() -> dict:
    return json.loads(Path(config.DATA_FILE).read_text(encoding="utf-8"))


def _enviar(sid: str, texto: str, datos: dict):
    return asyncio.run(engine.manejar(sid, texto, datos))


def _opciones(respuestas) -> list[dict]:
    return respuestas[0]["opciones"]


def _ids(respuestas) -> list[str]:
    return [o["id"] for o in _opciones(respuestas) if o["id"] != "humano"]


def _al_menu_de_dias(sid: str, datos: dict):
    _enviar(sid, "agendar", datos)
    return _enviar(sid, "manicura", datos)


def _dia_con_mas_horarios(datos: dict) -> tuple[str, list[str]]:
    libres = engine.slots_disponibles(datos, "manicura")
    por_dia = Counter(i[:10] for i in libres)
    fecha = por_dia.most_common(1)[0][0]
    return fecha, [i for i in libres if i[:10] == fecha]


# ------------------------------------------------------------ paso 1: el día
def test_tras_el_servicio_pregunta_el_dia_y_no_horarios(datos):
    respuestas, s = _al_menu_de_dias("a1", datos)

    assert s.paso == "agendar_dia"
    assert "día" in respuestas[0]["texto"].lower()
    ids = _ids(respuestas)
    assert all(len(i) == 10 for i in ids)                 # son fechas, no horarios
    assert len(set(ids)) == len(ids) >= 5                 # varios días distintos


def test_las_etiquetas_del_menu_de_dias_no_llevan_hora(datos):
    respuestas, _ = _al_menu_de_dias("a2", datos)

    for o in _opciones(respuestas):
        if o["id"] != "humano":
            assert ":" not in o["label"]


def test_las_etiquetas_de_los_dias_caben_en_una_fila_de_whatsapp(datos):
    respuestas, _ = _al_menu_de_dias("a3", datos)

    assert len(_opciones(respuestas)) <= MAX_FILAS_WA
    assert all(len(o["label"]) <= 24 for o in _opciones(respuestas) if o["id"] != "humano")


def test_el_primer_dia_se_etiqueta_hoy_o_mañana_segun_corresponda(datos):
    respuestas, _ = _al_menu_de_dias("a4", datos)

    etiquetas = [o["label"] for o in _opciones(respuestas)]
    primero = _ids(respuestas)[0]
    esperado = "Hoy" if date.fromisoformat(primero) == engine.reloj.ahora().date() else None
    if esperado:
        assert etiquetas[0].startswith("Hoy")


# ------------------------------------------------------------ paso 2: la hora
def test_elegir_un_dia_muestra_los_horarios_de_ese_dia(datos):
    fecha, del_dia = _dia_con_mas_horarios(datos)
    _al_menu_de_dias("b1", datos)

    respuestas, s = _enviar("b1", fecha, datos)

    assert s.paso == "agendar_slot"
    ids = _ids(respuestas)
    horarios_ids = [i for i in ids if len(i) == 16]
    assert horarios_ids and all(i[:10] == fecha for i in horarios_ids)
    assert horarios_ids == del_dia[: len(horarios_ids)]   # en orden, desde el primero


def test_los_horarios_se_etiquetan_solo_con_la_hora(datos):
    fecha, _ = _dia_con_mas_horarios(datos)
    _al_menu_de_dias("b2", datos)

    respuestas, _ = _enviar("b2", fecha, datos)

    for o in _opciones(respuestas):
        if len(o["id"]) == 16:
            assert o["label"] == o["id"][11:16]


def test_un_dia_con_muchos_horarios_se_pagina_sin_perder_ninguno(datos):
    fecha, del_dia = _dia_con_mas_horarios(datos)
    assert len(del_dia) > 8, "el día de prueba debería tener más de una página de horarios"
    _al_menu_de_dias("b3", datos)
    vistos: list[str] = []

    respuestas, _ = _enviar("b3", fecha, datos)
    for _ in range(10):                                   # tope de seguridad
        assert len(_opciones(respuestas)) <= MAX_FILAS_WA
        vistos += [i for i in _ids(respuestas) if len(i) == 16]
        if "mas_horarios" not in [o["id"] for o in _opciones(respuestas)]:
            break
        respuestas, _ = _enviar("b3", "mas_horarios", datos)

    assert vistos == del_dia
    assert "otro_dia" in [o["id"] for o in _opciones(respuestas)]   # la última página lo ofrece


def test_elegir_un_horario_pide_el_nombre_y_agenda(datos):
    fecha, del_dia = _dia_con_mas_horarios(datos)
    _al_menu_de_dias("b4", datos)
    _enviar("b4", fecha, datos)

    _, s = _enviar("b4", del_dia[0], datos)
    resp, s = _enviar("b4", "Ana", datos)

    assert s.paso == "fin" and "folio" in resp[0]["texto"].lower()
    assert store.confirmadas()[0]["slot"] == del_dia[0]


def test_otro_dia_vuelve_al_menu_de_dias(datos):
    fecha, _ = _dia_con_mas_horarios(datos)
    _al_menu_de_dias("b5", datos)
    _enviar("b5", fecha, datos)

    respuestas, s = _enviar("b5", "otro_dia", datos)

    assert s.paso == "agendar_dia" and all(len(i) == 10 for i in _ids(respuestas))


def test_un_numero_escrito_en_horarios_es_de_los_visibles(datos):
    fecha, del_dia = _dia_con_mas_horarios(datos)
    _al_menu_de_dias("b6", datos)
    _enviar("b6", fecha, datos)

    _, s = _enviar("b6", "3", datos)

    assert s.slot == del_dia[2]


def test_escribir_solo_la_hora_elige_dentro_del_dia_ya_escogido(datos):
    fecha, del_dia = _dia_con_mas_horarios(datos)
    objetivo = next(i for i in del_dia if i.endswith("16:00"))
    _al_menu_de_dias("b7", datos)
    _enviar("b7", fecha, datos)

    _, s = _enviar("b7", "a las 4 de la tarde", datos)

    assert s.slot == objetivo


# ------------------------------------------------------------ otras formas de elegir el día
def test_un_numero_escrito_elige_el_dia_correspondiente(datos):
    respuestas, _ = _al_menu_de_dias("c1", datos)
    segundo = _ids(respuestas)[1]

    resp, s = _enviar("c1", "2", datos)

    assert s.paso == "agendar_slot"
    assert all(i[:10] == segundo for i in _ids(resp) if len(i) == 16)


def test_la_etiqueta_escrita_elige_el_dia(datos):
    respuestas, _ = _al_menu_de_dias("c2", datos)
    etiqueta = _opciones(respuestas)[1]["label"]

    resp, s = _enviar("c2", etiqueta, datos)

    assert s.paso == "agendar_slot"


def test_escribir_el_nombre_de_un_dia_lleva_a_sus_horarios(datos):
    libres = engine.slots_disponibles(datos, "manicura")
    dia_semana = engine.datetime.fromisoformat(libres[-1]).weekday()
    esperado = next(i[:10] for i in libres if engine.datetime.fromisoformat(i).weekday() == dia_semana)
    _al_menu_de_dias("c3", datos)

    resp, s = _enviar("c3", engine.DIAS[dia_semana], datos)

    assert s.paso == "agendar_slot"
    assert all(i[:10] == esperado for i in _ids(resp) if len(i) == 16)


def test_un_id_de_horario_exacto_se_acepta_aun_en_el_paso_del_dia(datos):
    """Compatibilidad con la API y con clientes que ya mandan el horario completo."""
    slot = engine.slots_disponibles(datos, "manicura")[-1]
    _al_menu_de_dias("c4", datos)

    _, s = _enviar("c4", slot, datos)

    assert s.slot == slot and s.paso == "agendar_nombre"


def test_dia_y_hora_en_una_frase_siguen_saltando_pasos(datos):
    libres = engine.slots_disponibles(datos, "manicura")
    objetivo = next(i for i in libres if i.endswith("16:00"))
    dia = engine.DIAS[engine.datetime.fromisoformat(objetivo).weekday()]

    _, s = _enviar("c5", f"quiero manicura el {dia} a las 4", datos)

    assert s.paso == "agendar_nombre" and s.slot is not None


def test_texto_que_no_es_un_dia_vuelve_a_preguntar_el_dia(datos):
    _al_menu_de_dias("c6", datos)

    resp, s = _enviar("c6", "no sé", datos)

    assert s.paso == "agendar_dia" and "día" in resp[0]["texto"].lower()


def test_si_antes_dijo_el_dia_salta_directo_a_sus_horarios(datos):
    libres = engine.slots_disponibles(datos, "manicura")
    dia = engine.datetime.fromisoformat(libres[-1]).weekday()
    _enviar("c7", f"quiero una cita el {engine.DIAS[dia]}", datos)

    resp, s = _enviar("c7", "manicura", datos)

    assert s.paso == "agendar_slot"
    assert f"El {engine.DIAS[dia]} tengo" in resp[0]["texto"]


def test_si_el_dia_elegido_se_llena_mientras_tanto_ofrece_otros_dias(datos, monkeypatch):
    fecha, _ = _dia_con_mas_horarios(datos)
    _al_menu_de_dias("c8", datos)
    original = engine.slots_disponibles
    monkeypatch.setattr(
        engine, "slots_disponibles",
        lambda *a, **k: [i for i in original(*a, **k) if i[:10] != fecha],
    )

    resp, s = _enviar("c8", fecha, datos)

    assert s.paso == "agendar_dia" and fecha not in _ids(resp)
