"""El aviso de privacidad debe servirse público: Meta lo exige para publicar la app."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

CLIENTE = TestClient(app)


def test_privacidad_se_sirve_y_cubre_lfpdppp():
    r = CLIENTE.get("/privacidad")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    texto = r.text
    # Piezas mínimas que un aviso integral de la LFPDPPP no puede omitir.
    for clave in (
        "Aviso de privacidad",
        "LFPDPPP",
        "Nodeva",
        "Vidal Roldán Ávila #105",
        "Acceso, Rectificación, Cancelación y Oposición",
        "Meta Platforms",
        "nodeva3010@gmail.com",
        "Última actualización",
    ):
        assert clave in texto, f"falta en el aviso: {clave}"
