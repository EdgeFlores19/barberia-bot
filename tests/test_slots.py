"""Pruebas de la capa de disponibilidad (app/slots.py).

Contexto fijo de calendario (octubre/noviembre 2026):
  2026-10-26 lun · 2026-10-31 sáb (10:00–18:00) · 2026-11-01 dom (cerrado)
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import slots  # noqa: E402

# Lunes 26 de octubre de 2026, 08:00 (antes de abrir).
AHORA = datetime(2026, 10, 26, 8, 0)
LUNES = "2026-10-26"
SABADO = "2026-10-31"
DOMINGO = "2026-11-01"


@pytest.fixture(scope="module")
def datos() -> dict:
    """Datos reales del negocio: es contra ellos que corre el motor."""
    with open(RAIZ / "data" / "barberia.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def un_dia(datos: dict) -> dict:
    """Copia con la ventana reducida a hoy: útil para escenarios de 'sin huecos'."""
    d = copy.deepcopy(datos)
    d["reglas_agenda"]["dias_adelante"] = 0
    return d


def cita(slot: str, servicio_id: str = "corte", estado: str = "confirmada") -> dict:
    return {"slot": slot, "servicio_id": servicio_id, "estado": estado}


def del_dia(slots_iso: list[str], fecha: str) -> list[str]:
    return [s for s in slots_iso if s.startswith(fecha)]


# --------------------------------------------------------------- etiqueta
def test_etiqueta_formato_lunes(datos: dict) -> None:
    assert slots.etiqueta("2026-10-26T11:00") == "Lunes 26 oct · 11:00"


def test_etiqueta_dia_capitalizado_y_mes_minusculas(datos: dict) -> None:
    assert slots.etiqueta("2026-10-31T18:00") == "Sábado 31 oct · 18:00"
    assert slots.etiqueta("2026-11-01T09:30") == "Domingo 1 nov · 09:30"


def test_etiqueta_todos_los_meses() -> None:
    esperado = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    for mes, abrev in enumerate(esperado, start=1):
        texto = slots.etiqueta(f"2026-{mes:02d}-15T10:00")
        assert texto.split()[2] == abrev
        assert texto[-5:] == "10:00"


def test_etiqueta_acepta_iso_con_segundos() -> None:
    assert slots.etiqueta("2026-10-26T11:00:00") == "Lunes 26 oct · 11:00"


# --------------------------------------------------------- generar_slots
def test_generar_slots_corte_arranca_al_abrir(datos: dict) -> None:
    libres = slots.generar_slots(datos, "corte", [], AHORA, 3)
    assert libres == ["2026-10-26T10:00", "2026-10-26T10:30", "2026-10-26T11:00"]


def test_generar_slots_orden_cronologico_y_limite(datos: dict) -> None:
    libres = slots.generar_slots(datos, "corte", [], AHORA, 24)
    assert len(libres) == 24
    assert libres == sorted(libres)
    assert len(set(libres)) == 24


def test_generar_slots_ultimo_corte_entra_antes_del_cierre(datos: dict) -> None:
    """Cierre 20:00 con bloques de 30 min: 19:30 es el último válido."""
    dia = del_dia(slots.generar_slots(datos, "corte", [], AHORA, 1000), LUNES)
    assert dia[0] == f"{LUNES}T10:00"
    assert dia[-1] == f"{LUNES}T19:30"
    assert len(dia) == 20


def test_generar_slots_servicio_de_60_no_cabe_al_final(datos: dict) -> None:
    """corte_barba dura 60 min: no puede empezar 19:30 si cierran a las 20:00."""
    dia = del_dia(slots.generar_slots(datos, "corte_barba", [], AHORA, 1000), LUNES)
    assert f"{LUNES}T19:00" in dia
    assert f"{LUNES}T19:30" not in dia
    assert dia[-1] == f"{LUNES}T19:00"
    assert len(dia) == 19


def test_generar_slots_sabado_cierra_18(datos: dict) -> None:
    dia = del_dia(slots.generar_slots(datos, "corte", [], AHORA, 1000), SABADO)
    assert dia[0] == f"{SABADO}T10:00"
    assert dia[-1] == f"{SABADO}T17:30"
    assert len(dia) == 16


def test_generar_slots_salta_domingo(datos: dict) -> None:
    todos = slots.generar_slots(datos, "corte", [], AHORA, 1000)
    assert not any(s.startswith(DOMINGO) for s in todos)


def test_generar_slots_desde_domingo_empieza_el_lunes(datos: dict) -> None:
    ahora = datetime(2026, 11, 1, 9, 0)  # domingo
    libres = slots.generar_slots(datos, "corte", [], ahora, 3)
    assert libres == ["2026-11-02T10:00", "2026-11-02T10:30", "2026-11-02T11:00"]


def test_generar_slots_ventana_de_7_dias_incluye_hoy(datos: dict) -> None:
    todos = slots.generar_slots(datos, "corte", [], AHORA, 1000)
    fechas = sorted({s[:10] for s in todos})
    assert fechas[0] == LUNES
    assert fechas[-1] == "2026-11-02"  # hoy + 7
    assert fechas == [
        "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
        "2026-10-30", "2026-10-31", "2026-11-02",
    ]


def test_generar_slots_respeta_anticipacion_minima(datos: dict) -> None:
    """A las 10:45, con 1 h de anticipación, lo primero posible es 12:00."""
    libres = slots.generar_slots(datos, "corte", [], datetime(2026, 10, 26, 10, 45), 3)
    assert libres[0] == "2026-10-26T12:00"


def test_generar_slots_hoy_ya_cerrado_salta_al_dia_siguiente(datos: dict) -> None:
    ahora = datetime(2026, 10, 26, 20, 30)
    libres = slots.generar_slots(datos, "corte", [], ahora, 2)
    assert libres == ["2026-10-27T10:00", "2026-10-27T10:30"]


def test_cita_confirmada_bloquea_su_slot(datos: dict) -> None:
    citas = [cita(f"{LUNES}T10:00")]
    dia = del_dia(slots.generar_slots(datos, "corte", citas, AHORA, 1000), LUNES)
    assert f"{LUNES}T10:00" not in dia
    assert f"{LUNES}T10:30" in dia
    assert len(dia) == 19


def test_cita_cancelada_no_bloquea(datos: dict) -> None:
    citas = [cita(f"{LUNES}T10:00", estado="cancelada")]
    dia = del_dia(slots.generar_slots(datos, "corte", citas, AHORA, 1000), LUNES)
    assert f"{LUNES}T10:00" in dia
    assert len(dia) == 20


def test_cita_de_60_min_ocupa_dos_bloques(datos: dict) -> None:
    citas = [cita(f"{LUNES}T11:00", servicio_id="corte_barba")]
    dia = del_dia(slots.generar_slots(datos, "corte", citas, AHORA, 1000), LUNES)
    assert f"{LUNES}T11:00" not in dia
    assert f"{LUNES}T11:30" not in dia
    assert f"{LUNES}T10:30" in dia
    assert f"{LUNES}T12:00" in dia


def test_servicio_de_60_evita_cita_de_30_solapada(datos: dict) -> None:
    """Con cita de 30 min a las 11:00, corte_barba no cabe ni 10:30 ni 11:00."""
    citas = [cita(f"{LUNES}T11:00", servicio_id="corte")]
    dia = del_dia(slots.generar_slots(datos, "corte_barba", citas, AHORA, 1000), LUNES)
    assert f"{LUNES}T10:30" not in dia
    assert f"{LUNES}T11:00" not in dia
    # tocarse en el borde no es traslape: 10:00 termina justo a las 11:00
    assert f"{LUNES}T10:00" in dia
    assert f"{LUNES}T11:30" in dia


def test_varias_citas_y_servicios_distintos(datos: dict) -> None:
    citas = [
        cita(f"{LUNES}T10:00", servicio_id="barba"),
        cita(f"{LUNES}T12:00", servicio_id="corte_barba"),
        cita(f"{LUNES}T12:00", servicio_id="corte", estado="cancelada"),
        cita(f"{SABADO}T10:00", servicio_id="afeitado"),
    ]
    dia = del_dia(slots.generar_slots(datos, "corte", citas, AHORA, 1000), LUNES)
    for ocupado in ("10:00", "12:00", "12:30"):
        assert f"{LUNES}T{ocupado}" not in dia
    for libre in ("10:30", "11:30", "13:00"):
        assert f"{LUNES}T{libre}" in dia
    sab = del_dia(slots.generar_slots(datos, "corte", citas, AHORA, 1000), SABADO)
    assert f"{SABADO}T10:00" not in sab
    assert f"{SABADO}T10:30" in sab


def test_sin_huecos_devuelve_lista_vacia(datos: dict, un_dia: dict) -> None:
    """Día completo de citas de 30 min: no queda nada."""
    ocupadas = [cita(f"{LUNES}T{hora:02d}:{minuto:02d}") for hora in range(10, 20) for minuto in (0, 30)]
    assert slots.generar_slots(un_dia, "corte", ocupadas, AHORA, 1000) == []


def test_dia_lleno_pero_servicio_de_60_no_cabe(datos: dict, un_dia: dict) -> None:
    ocupadas = [cita(f"{LUNES}T{hora:02d}:{minuto:02d}") for hora in range(10, 20) for minuto in (0, 30)]
    assert slots.generar_slots(un_dia, "corte_barba", ocupadas, AHORA, 1000) == []


def test_citas_corruptas_no_tumban_el_calculo(datos: dict) -> None:
    citas = [
        {"slot": "", "servicio_id": "corte", "estado": "confirmada"},
        {"slot": "el martes", "servicio_id": "corte", "estado": "confirmada"},
        {"slot": f"{LUNES}T10:00", "servicio_id": "servicio_fantasma", "estado": "confirmada"},
        {"slot": f"{LUNES}T15:00", "servicio_id": "corte"},
    ]
    dia = del_dia(slots.generar_slots(datos, "corte", citas, AHORA, 1000), LUNES)
    assert f"{LUNES}T10:00" not in dia  # sin servicio conocido ocupa un bloque
    assert f"{LUNES}T15:00" not in dia  # sin estado se asume confirmada
    assert f"{LUNES}T15:30" in dia


def test_servicio_inexistente_falla_claro(datos: dict) -> None:
    with pytest.raises(ValueError):
        slots.generar_slots(datos, "masaje", [], AHORA)
    with pytest.raises(ValueError):
        slots.validar(datos, "masaje", f"{LUNES}T11:00", [], AHORA)


# --------------------------------------------------------------- validar
def test_validar_slot_libre(datos: dict) -> None:
    assert slots.validar(datos, "corte", f"{LUNES}T11:00", [], AHORA) == (True, "")


def test_validar_domingo_cerrado(datos: dict) -> None:
    ok, motivo = slots.validar(datos, "corte", f"{DOMINGO}T11:00", [], datetime(2026, 11, 1, 9, 0))
    assert (ok, motivo) == (False, "el día está cerrado")


def test_validar_antes_de_abrir(datos: dict) -> None:
    ok, motivo = slots.validar(datos, "corte", f"{LUNES}T09:00", [], AHORA)
    assert (ok, motivo) == (False, "fuera del horario de atención")


def test_validar_despues_de_cerrar(datos: dict) -> None:
    ok, motivo = slots.validar(datos, "corte", f"{SABADO}T18:00", [], AHORA)
    assert ok is False
    assert motivo == "fuera del horario de atención"


def test_validar_no_cabe_completo(datos: dict) -> None:
    ok, motivo = slots.validar(datos, "corte_barba", f"{LUNES}T19:30", [], AHORA)
    assert (ok, motivo) == (False, "no cabe completo antes del cierre")


def test_validar_si_el_servicio_cabe_completo(datos: dict) -> None:
    assert slots.validar(datos, "corte", f"{LUNES}T19:30", [], AHORA)[0] is True
    assert slots.validar(datos, "corte_barba", f"{LUNES}T19:00", [], AHORA)[0] is True


def test_validar_anticipacion(datos: dict) -> None:
    ok, motivo = slots.validar(datos, "corte", f"{LUNES}T11:00", [], datetime(2026, 10, 26, 10, 30))
    assert (ok, motivo) == (False, "faltan menos de 1 h de anticipación")
    # justo en el límite: 12:00 con ahora 11:00 sí se puede
    assert slots.validar(datos, "corte", f"{LUNES}T12:00", [], datetime(2026, 10, 26, 11, 0))[0] is True


def test_validar_fuera_de_ventana(datos: dict) -> None:
    ok, motivo = slots.validar(datos, "corte", "2026-11-04T11:00", [], AHORA)
    assert (ok, motivo) == (False, "más de 7 días adelante")
    # hoy + 7 sí entra
    assert slots.validar(datos, "corte", "2026-11-02T11:00", [], AHORA)[0] is True


def test_validar_fecha_pasada(datos: dict) -> None:
    ok, motivo = slots.validar(datos, "corte", "2026-10-20T11:00", [], AHORA)
    assert (ok, motivo) == (False, "esa fecha ya pasó")


def test_validar_bloques_alineados(datos: dict) -> None:
    ok, motivo = slots.validar(datos, "corte", f"{LUNES}T10:15", [], AHORA)
    assert (ok, motivo) == (False, "el horario debe ir en bloques de 30 minutos")
    ok, motivo = slots.validar(datos, "corte", f"{LUNES}T11:00:30", [], AHORA)
    assert (ok, motivo) == (False, "el horario debe ir en bloques de 30 minutos")


def test_validar_formato_invalido(datos: dict) -> None:
    ok, motivo = slots.validar(datos, "corte", "el lunes en la mañana", [], AHORA)
    assert ok is False
    assert motivo


def test_validar_ocupado_por_cita(datos: dict) -> None:
    citas = [cita(f"{LUNES}T11:00", servicio_id="corte_barba")]
    ok, motivo = slots.validar(datos, "corte", f"{LUNES}T11:00", citas, AHORA)
    assert (ok, motivo) == (False, "ese horario ya está ocupado")
    # 11:30 cae dentro del corte+barba de 11:00 a 12:00
    assert slots.validar(datos, "corte", f"{LUNES}T11:30", citas, AHORA)[0] is False
    # 12:00 está libre
    assert slots.validar(datos, "corte", f"{LUNES}T12:00", citas, AHORA)[0] is True


def test_validar_cancelada_libera_el_slot(datos: dict) -> None:
    citas = [cita(f"{LUNES}T11:00", estado="cancelada")]
    assert slots.validar(datos, "corte", f"{LUNES}T11:00", citas, AHORA) == (True, "")


# --------------------------------------------- consistencia entre funciones
@pytest.mark.parametrize("servicio", ["corte", "corte_barba", "barba", "nino", "afeitado"])
def test_consistencia_generar_y_validar(datos: dict, servicio: str) -> None:
    """Todo lo que se ofrece se puede reservar; lo que se rechaza no se ofrece."""
    citas = [
        cita(f"{LUNES}T11:00", servicio_id="corte_barba"),
        cita(f"{LUNES}T16:00", servicio_id="corte"),
        cita(f"{SABADO}T12:00", servicio_id="barba", estado="cancelada"),
    ]
    libres = slots.generar_slots(datos, servicio, citas, AHORA, 200)
    assert libres, "debe haber huecos en el escenario"
    for slot in libres:
        assert slots.validar(datos, servicio, slot, citas, AHORA) == (True, ""), slot

    # un slot no ofrecido nunca debe validar como reservable
    for slot in (f"{LUNES}T23:00", f"{DOMINGO}T11:00", f"{LUNES}T09:30", "2026-11-09T11:00"):
        if slot in libres:
            continue
        assert slots.validar(datos, servicio, slot, citas, AHORA)[0] is False, slot
