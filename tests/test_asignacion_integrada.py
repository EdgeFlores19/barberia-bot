"""«Cualquiera disponible» con las citas reales de la base (no con diccionarios armados a mano).

`citas.agenda_id` guarda la CLAVE de la agenda. Las pruebas unitarias de `agendas.elegir` armaban las citas
con el id del calendario, así que las reglas de carga y de "la misma de la última vez" pasaban en el
aire y no se aplicaban nunca con datos reales (siempre ganaba la primera agenda).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import agendas, engine, reloj, store

TEL_NUEVA = "5210000000009"
TEL_HABITUAL = "5214491112233"


@pytest.fixture()
def equipo(bd):
    store.agendas_sincronizar([
        {"calendar_id": "cal-ana", "nombre": "Ana", "escritura": True},
        {"calendar_id": "cal-laura", "nombre": "Laura", "escritura": True},
        {"calendar_id": "cal-sofia", "nombre": "Sofía", "escritura": True},
    ])
    activas = store.agendas(solo_activas=True)
    claves = {a["nombre"]: a["clave"] for a in activas}
    datos = engine.cargar_datos()
    sv = datos["servicios"][0]["id"]
    libres = agendas.horarios_libres(datos, sv, activas, reloj.ahora(), limite=300)
    lejanos = [h for h in libres if datetime.fromisoformat(h) > reloj.ahora() + timedelta(days=2)]
    por_dia: dict[str, list[str]] = {}
    for h in lejanos:
        por_dia.setdefault(h[:10], []).append(h)
    # Un día con horarios de sobra (un sábado tiene menos): la prueba no debe depender del día en que corra.
    dia = next(d for d, hs in por_dia.items() if len(hs) >= 8)
    horas = por_dia[dia]
    otro_dia = next(hs[0] for d, hs in por_dia.items() if d != dia)

    class Equipo:
        pass
    e = Equipo()
    e.datos, e.sv, e.claves, e.horas, e.otro_dia = datos, sv, claves, horas, otro_dia
    e.nombre = {v: k for k, v in claves.items()}
    return e


def _cita(e, slot, agenda, tel="5210000000001", nombre="Cliente"):
    return store.crear(slot, e.sv, nombre, tel, agenda_id=e.claves[agenda])


def _primera(e, slot, tel=TEL_NUEVA) -> str:
    candidatas = engine._candidatas_al_slot(e.datos, e.sv, slot, "*", telefono=tel)
    return e.nombre[candidatas[0]]


# ---------- carga ----------
def test_gana_la_que_tiene_menos_citas_ese_dia(equipo):
    _cita(equipo, equipo.horas[0], "Ana")
    _cita(equipo, equipo.horas[1], "Ana", tel="5210000000002")
    _cita(equipo, equipo.horas[2], "Laura", tel="5210000000003")

    assert _primera(equipo, equipo.horas[5]) == "Sofía"             # 0 citas contra 2 y 1


def test_las_citas_canceladas_no_cuentan_como_carga(equipo):
    for i in range(2):
        c = _cita(equipo, equipo.horas[i], "Ana", tel=f"521000000010{i}")
        store.cancelar(c["folio"])
    _cita(equipo, equipo.horas[2], "Laura", tel="5210000000003")
    _cita(equipo, equipo.horas[3], "Sofía", tel="5210000000004")

    assert _primera(equipo, equipo.horas[5]) == "Ana"               # sus 2 canceladas no pesan


def test_un_apartado_vencido_no_cuenta_como_carga(equipo):
    ayer = (reloj.ahora() - timedelta(days=1)).isoformat(timespec="seconds")
    store.crear(equipo.horas[0], equipo.sv, "Sin pagar", "5210000000005", agenda_id=equipo.claves["Ana"],
                pendiente_hasta=ayer, anticipo=200)
    _cita(equipo, equipo.horas[1], "Laura", tel="5210000000003")
    _cita(equipo, equipo.horas[2], "Sofía", tel="5210000000004")

    assert _primera(equipo, equipo.horas[5]) == "Ana"


def test_un_apartado_vigente_si_cuenta_como_carga(equipo):
    manana = (reloj.ahora() + timedelta(hours=1)).isoformat(timespec="seconds")
    store.crear(equipo.horas[0], equipo.sv, "Pagando", "5210000000005", agenda_id=equipo.claves["Ana"],
                pendiente_hasta=manana, anticipo=200)

    assert _primera(equipo, equipo.horas[5]) == "Laura"             # Ana ya tiene uno; Laura y Sofía, 0


def test_el_empate_total_va_a_la_primera_por_orden(equipo):
    assert _primera(equipo, equipo.horas[5]) == "Ana"


# ---------- la misma de la última vez ----------
def test_la_de_la_ultima_vez_gana_si_esta_libre(equipo):
    _cita(equipo, equipo.otro_dia, "Laura", tel=TEL_HABITUAL)

    assert _primera(equipo, equipo.horas[5], tel=TEL_HABITUAL) == "Laura"


def test_la_de_la_ultima_vez_gana_aunque_tenga_mas_carga_ese_dia(equipo):
    _cita(equipo, equipo.otro_dia, "Laura", tel=TEL_HABITUAL)
    _cita(equipo, equipo.horas[0], "Laura", tel="5210000000002")
    _cita(equipo, equipo.horas[1], "Laura", tel="5210000000003")

    assert _primera(equipo, equipo.horas[5], tel=TEL_HABITUAL) == "Laura"      # primero la persona, luego la carga


def test_si_la_de_siempre_esta_ocupada_a_esa_hora_manda_la_de_menos_carga(equipo):
    _cita(equipo, equipo.otro_dia, "Laura", tel=TEL_HABITUAL)
    _cita(equipo, equipo.horas[5], "Laura", tel="5210000000002")               # Laura no puede a esa hora
    _cita(equipo, equipo.horas[0], "Ana", tel="5210000000003")

    assert _primera(equipo, equipo.horas[5], tel=TEL_HABITUAL) == "Sofía"


def test_una_cita_cancelada_no_cuenta_como_la_ultima_vez(equipo):
    c = _cita(equipo, equipo.otro_dia, "Laura", tel=TEL_HABITUAL)
    store.cancelar(c["folio"])

    assert _primera(equipo, equipo.horas[5], tel=TEL_HABITUAL) == "Ana"        # sin preferencia: por orden


def test_otra_clienta_no_hereda_la_preferencia(equipo):
    _cita(equipo, equipo.otro_dia, "Laura", tel=TEL_HABITUAL)

    assert _primera(equipo, equipo.horas[5], tel=TEL_NUEVA) == "Ana"


def test_si_su_agenda_de_siempre_ya_no_esta_activa_se_ignora(equipo):
    _cita(equipo, equipo.otro_dia, "Laura", tel=TEL_HABITUAL)
    with store._con() as con:
        con.execute("UPDATE agendas SET activa = 0 WHERE calendar_id = 'cal-laura'")

    assert _primera(equipo, equipo.horas[5], tel=TEL_HABITUAL) == "Ana"


# ---------- el efecto que se nota: reparte ----------
def test_cualquiera_reparte_la_carga_en_vez_de_llenar_a_la_primera(equipo):
    asignadas = []
    for i in range(6):
        quien = _primera(equipo, equipo.horas[i], tel=f"52100000020{i}")
        asignadas.append(quien)
        _cita(equipo, equipo.horas[i], quien, tel=f"52100000020{i}")

    assert asignadas[:3] == ["Ana", "Laura", "Sofía"]
    assert sorted(asignadas) == ["Ana", "Ana", "Laura", "Laura", "Sofía", "Sofía"]
