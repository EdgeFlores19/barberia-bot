"""Fixtures compartidas para los tests del canal WhatsApp."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config, engine, store


@pytest.fixture()
def bd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Base SQLite temporal (sin seed) y sesiones limpias por prueba.

    Sin el monkeypatch de SEED_DEMO, importar app.main siembra citas de ejemplo.
    """
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.db")
    monkeypatch.setattr(config, "SEED_DEMO", False)
    monkeypatch.setattr(store, "_inicializada", False)
    store.init()
    engine.SESIONES.clear()
    yield
    store.reset()
