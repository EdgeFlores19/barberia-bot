"""Pruebas de las citas de ejemplo que siembran la demo."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from app import config, seed, store
from app import slots as horarios

DATOS = json.loads((Path(config.DATA_FILE)).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def bd_temporal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "seed.db")
    monkeypatch.setattr(store, "_inicializada", False)
    store.init()
    yield
    store.reset()


def test_siembra_dos_citas_y_respeta_el_horario():
    # Miércoles 28 de octubre de 2026, 09:00.
    ahora = datetime(2026, 10, 28, 9, 0)
    creadas = seed.sembrar_si_vacio(DATOS, ahora=ahora)

    citas = store.listar()
    assert creadas == 2
    assert len(citas) == 2
    for cita in citas:
        libres_del_dia = horarios.generar_slots(
            DATOS, cita["servicio_id"], store.confirmadas(), ahora, limite=500
        )
        assert cita["slot"] not in libres_del_dia
        assert cita["folio"].startswith(store.PREFIJO_FOLIO)


def test_no_siembra_dos_veces():
    ahora = datetime(2026, 10, 28, 9, 0)
    seed.sembrar_si_vacio(DATOS, ahora=ahora)
    assert seed.sembrar_si_vacio(DATOS, ahora=ahora) == 0
    assert len(store.listar()) == 2
