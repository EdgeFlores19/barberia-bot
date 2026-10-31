"""app.main no arranca sin DEMO_CODE (falla cerrado) salvo ALLOW_OPEN_DEMO=1."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _importar_main(tmp_path: Path, **entorno: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in {"DEMO_CODE", "ALLOW_OPEN_DEMO"}}
    env.update(DB_FILE=str(tmp_path / "arranque.db"), SEED_DEMO="0", **entorno)
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=RAIZ, env=env, capture_output=True, text=True, timeout=60,
    )


def test_sin_demo_code_no_arranca(tmp_path):
    r = _importar_main(tmp_path)

    assert r.returncode != 0
    assert "DEMO_CODE" in r.stderr


def test_demo_code_vacio_tampoco_arranca(tmp_path):
    r = _importar_main(tmp_path, DEMO_CODE="   ")

    assert r.returncode != 0


def test_con_demo_code_arranca(tmp_path):
    r = _importar_main(tmp_path, DEMO_CODE="un-codigo")

    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("valor", ["1", "true", "si"])
def test_allow_open_demo_permite_arrancar_sin_codigo(tmp_path, valor):
    r = _importar_main(tmp_path, ALLOW_OPEN_DEMO=valor)

    assert r.returncode == 0, r.stderr
