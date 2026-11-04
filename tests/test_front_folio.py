"""El folio de los mensajes del bot se copia con un toque en el chat web (prueba de front-end)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def test_folio_copiable_en_el_chat_web():
    node = shutil.which("node")
    if not node:
        pytest.skip("node no está instalado (el CI de GitHub sí lo trae)")

    r = subprocess.run(
        [node, "--test", str(RAIZ / "tests" / "front" / "folio_copiable.test.js")],
        capture_output=True, text=True, timeout=60,
    )

    assert r.returncode == 0, r.stdout + r.stderr
