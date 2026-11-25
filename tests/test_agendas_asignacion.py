"""Disponibilidad por agenda y regla de asignación (M8). Puro: sin red ni base."""

from __future__ import annotations

from datetime import datetime

import pytest

from app import agendas as ag


def _datos(**extra: object) -> dict:
    base = {
        "servicios": [{"id": "corte", "nombre": "Corte", "minutos": 30, "precio": 150}],
        "horario": {
            "lunes_viernes": {"abre": "10:00", "cierra": "13:00"},
            "sabado": {"abre": "10:00", "cierra": "13:00"},
            "domingo": {"abre": "10:00", "cierra": "13:00"},
        },
        "reglas_agenda": {
            "slots_minutos": 30,
            "dias_adelante": 3,
            "anticipacion_minima_horas": 0,
        },
    }
    base.update(extra)
    return base


def _agenda(clave: str, orden: int, activa: bool = True) -> dict:
    return {"calendar_id": f"cal-{clave}", "clave": clave, "nombre": clave.title(), "orden": orden, "activa": activa}


# 2026-11-23 es lunes
AHORA = datetime(2026, 11, 23, 9, 0)
ANA = _agenda("ana", 1)
LAURA = _agenda("laura", 2)


def test_unir_horarios_quita_repetidos_y_ordena() -> None:
    assert ag.unir_horarios(
        [
            ["2026-11-23T11:00", "2026-11-23T10:00"],
            ["2026-11-23T10:00", "2026-11-23T10:30"],
        ]
    ) == ["2026-11-23T10:00", "2026-11-23T10:30", "2026-11-23T11:00"]
    assert ag.unir_horarios([]) == []


def test_candidatas_respeta_ocupados_y_bloqueos_de_su_agenda() -> None:
    ocupados = {
        "ana": [{"slot": "2026-11-23T10:00", "servicio_id": "corte", "estado": "confirmada"}],
        "laura": [],
    }

    def bloqueos(clave: str) -> list[dict]:
        if clave == "laura":
            return [{"slot": "2026-11-23T10:00", "fin": "2026-11-23T10:30", "estado": "confirmada"}]
        return []

    out = ag.candidatas(
        _datos(),
        "corte",
        [ANA, LAURA],
        AHORA,
        ocupados_por=lambda c: ocupados[c],
        bloqueos_por=bloqueos,
        limite=4,
    )
    assert list(out) == ["ana", "laura"]
    # las dos pierden las 10:00, cada una por su propio motivo
    assert "2026-11-23T10:00" not in out["ana"]
    assert "2026-11-23T10:00" not in out["laura"]
    # y las dos conservan el resto
    assert out["ana"] == out["laura"] == [
        "2026-11-23T10:30", "2026-11-23T11:00", "2026-11-23T11:30", "2026-11-23T12:00"
    ]


def test_candidatas_ignora_agendas_inactivas() -> None:
    out = ag.candidatas(
        _datos(),
        "corte",
        [ANA, _agenda("laura", 2, activa=False)],
        AHORA,
        ocupados_por=lambda c: [],
        bloqueos_por=lambda c: [],
        limite=1,
    )
    assert list(out) == ["ana"]


def test_elegir_prefiere_la_agenda_de_la_ultima_cita_del_cliente() -> None:
    citas = [
        {"slot": "2026-11-17T10:00", "agenda_id": "cal-laura", "telefono": "555", "estado": "asistio"},
        {"slot": "2026-11-10T10:00", "agenda_id": "cal-ana", "telefono": "555", "estado": "asistio"},
    ]
    cand = {"ana": ["2026-11-23T10:00"], "laura": ["2026-11-23T10:00"]}
    assert ag.elegir("2026-11-23T10:00", cand, citas, [ANA, LAURA], telefono="555") == "laura"
    # quien nunca ha venido no arrastra preferencia
    assert ag.elegir("2026-11-23T10:00", cand, citas, [ANA, LAURA], telefono="999") == "ana"


def test_elegir_reparte_por_menos_citas_ese_dia_y_luego_por_orden() -> None:
    citas = [
        {"slot": "2026-11-23T10:00", "agenda_id": "cal-ana", "telefono": "111", "estado": "confirmada"},
    ]
    cand = {"ana": ["2026-11-23T11:00"], "laura": ["2026-11-23T11:00"]}
    assert ag.elegir("2026-11-23T11:00", cand, citas, [ANA, LAURA]) == "laura"
    # empate: gana el menor `orden`
    assert ag.elegir("2026-11-23T11:00", cand, [], [ANA, LAURA]) == "ana"


def test_elegir_es_determinista_y_una_sola_candidata_se_devuelve() -> None:
    cand = {"laura": ["2026-11-23T10:00"], "ana": ["2026-11-23T10:00"]}
    assert ag.elegir("2026-11-23T10:00", cand, [], [ANA, LAURA]) == ag.elegir(
        "2026-11-23T10:00", cand, [], [ANA, LAURA]
    )
    assert ag.elegir("2026-11-23T10:00", {"laura": ["2026-11-23T10:00"]}, [], [ANA, LAURA]) == "laura"
    # el horario no está libre en ninguna
    assert ag.elegir("2026-11-23T12:00", cand, [], [ANA, LAURA]) is None


def test_horarios_libres_une_las_agendas_activas() -> None:
    ocupados = {"ana": [{"slot": "2026-11-23T10:00", "servicio_id": "corte", "estado": "confirmada"}]}
    libre = ag.horarios_libres(
        _datos(),
        "corte",
        [ANA, LAURA],
        AHORA,
        ocupados_por=lambda c: ocupados.get(c, []),
        bloqueos_por=lambda c: [],
        limite=3,
    )
    assert libre == ["2026-11-23T10:00", "2026-11-23T10:30", "2026-11-23T11:00"]


def test_candidatas_con_una_agenda_sin_datos_no_revienta() -> None:
    assert ag.horarios_libres(
        _datos(), "corte", [], AHORA, ocupados_por=lambda c: [], bloqueos_por=lambda c: []
    ) == []


def test_candidatas_usa_los_lectores_por_defecto_si_no_se_inyectan(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.calendar_sync as sync
    import app.store as store

    monkeypatch.setattr(store, "confirmadas", lambda **_: [])
    monkeypatch.setattr(sync, "bloqueos", lambda *_: [])
    out = ag.candidatas(_datos(), "corte", [ANA], AHORA, limite=1)
    assert out == {"ana": ["2026-11-23T10:00"]}


# ---------- con la forma real de los datos: citas.agenda_id guarda la CLAVE ----------
CANDIDATAS = {"ana": ["2026-11-23T10:00"], "laura": ["2026-11-23T10:00"]}


def _c(agenda: str, slot: str, tel: str = "1", estado: str = "confirmada") -> dict:
    return {"agenda_id": agenda, "slot": slot, "telefono": tel, "estado": estado}


def test_elegir_cuenta_las_citas_que_traen_la_clave_de_la_agenda() -> None:
    citas = [_c("ana", "2026-11-23T11:00"), _c("ana", "2026-11-23T12:00", tel="2")]

    assert ag.elegir("2026-11-23T10:00", CANDIDATAS, citas, [ANA, LAURA]) == "laura"


def test_elegir_sigue_aceptando_el_id_del_calendario() -> None:
    citas = [_c("cal-ana", "2026-11-23T11:00"), _c("cal-ana", "2026-11-23T12:00", tel="2")]

    assert ag.elegir("2026-11-23T10:00", CANDIDATAS, citas, [ANA, LAURA]) == "laura"


def test_elegir_la_misma_de_la_ultima_vez_con_la_clave() -> None:
    citas = [_c("laura", "2026-11-20T10:00", tel="555")]

    assert ag.elegir("2026-11-23T10:00", CANDIDATAS, citas, [ANA, LAURA], telefono="555") == "laura"


@pytest.mark.parametrize("estado", ["cancelada", "expirada"])
def test_elegir_ignora_las_citas_que_ya_no_ocupan_a_nadie(estado: str) -> None:
    citas = [_c("ana", "2026-11-23T11:00", estado=estado), _c("ana", "2026-11-23T12:00", tel="2", estado=estado),
             _c("laura", "2026-11-23T13:00", tel="3")]

    assert ag.elegir("2026-11-23T10:00", CANDIDATAS, citas, [ANA, LAURA]) == "ana"    # 0 activas contra 1


def test_elegir_una_cita_cancelada_no_es_la_ultima_vez() -> None:
    citas = [_c("laura", "2026-11-20T10:00", tel="555", estado="cancelada")]

    assert ag.elegir("2026-11-23T10:00", CANDIDATAS, citas, [ANA, LAURA], telefono="555") == "ana"
