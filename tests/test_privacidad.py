"""Aviso de privacidad y condiciones de uso públicos: Meta los exige al publicar la app."""
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


def test_terminos_se_sirven_y_son_coherentes_con_el_negocio():
    r = CLIENTE.get("/terminos")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    texto = r.text
    for clave in (
        "Condiciones de uso",
        "Nodeva",
        "449 548 5324",
        "nodeva3010@gmail.com",
        # Reglas reales de agenda: si cambian en data/barberia.json, este test lo delata.
        "30 minutos",
        "1 hora de anticipación",
        "7 días",
        "2 horas antes",
        "efectivo, transferencia y tarjeta",
        "/privacidad",
    ):
        assert clave in texto, f"falta en las condiciones: {clave}"


def test_ambas_paginas_se_enlazan_entre_si():
    privacidad = CLIENTE.get("/privacidad").text
    terminos = CLIENTE.get("/terminos").text
    assert 'href="/terminos"' in privacidad
    assert 'href="/privacidad"' in terminos
