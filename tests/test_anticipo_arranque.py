"""app.main no arranca con el anticipo activo si faltan o están mal los datos de pago."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CLABE = "002010077777777771"


def _importar_main(tmp_path: Path, **entorno: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in {"ANTICIPO_ACTIVO", "ANTICIPO_BANCO", "ANTICIPO_CLABE", "ANTICIPO_TITULAR"}}
    env.update(DB_FILE=str(tmp_path / "arranque.db"), SEED_DEMO="0", DEMO_CODE="un-codigo", **entorno)
    return subprocess.run([sys.executable, "-c", "import app.main"], cwd=RAIZ, env=env,
                          capture_output=True, text=True, timeout=60)


def test_con_el_anticipo_apagado_no_se_piden_datos_de_pago(tmp_path):
    r = _importar_main(tmp_path)

    assert r.returncode == 0, r.stderr


def test_activo_sin_datos_de_pago_no_arranca_y_dice_que_falta(tmp_path):
    r = _importar_main(tmp_path, ANTICIPO_ACTIVO="1")

    assert r.returncode != 0
    assert "ANTICIPO_CLABE" in r.stderr and "ANTICIPO_BANCO" in r.stderr


def test_una_clabe_con_el_digito_mal_no_arranca(tmp_path):
    r = _importar_main(tmp_path, ANTICIPO_ACTIVO="1", ANTICIPO_BANCO="Banco", ANTICIPO_TITULAR="Titular",
                       ANTICIPO_CLABE=CLABE[:-1] + "9")

    assert r.returncode != 0
    assert "CLABE" in r.stderr


def test_activo_con_todo_bien_arranca(tmp_path):
    r = _importar_main(tmp_path, ANTICIPO_ACTIVO="1", ANTICIPO_BANCO="Banco", ANTICIPO_TITULAR="Titular",
                       ANTICIPO_CLABE=CLABE)

    assert r.returncode == 0, r.stderr
