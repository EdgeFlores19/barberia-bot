"""Lector de comprobantes (`app.llm.leer_comprobante`): qué sale y qué no, sin red."""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import date

import httpx
import pytest

from app import config, llm

IMAGEN = b"\xff\xd8\xff\xe0jpeg-de-prueba"

LECTURA = {
    "es_comprobante": True,
    "monto": "200.00",
    "fecha": "2026-11-24",
    "clave_rastreo": "hu-1234 abcd-5678",
    "cuenta_destino": "****1234",
    "beneficiario": "Ambar Estudio",
    "concepto": "Anticipo AMB-1234",
}


@pytest.fixture()
def modelo(monkeypatch):
    """Doble de la API: guarda lo que sale y devuelve el `content` que pida el test."""
    estado = {"content": json.dumps(LECTURA), "enviados": [], "headers": [],
              "llamadas": 0, "falla": None}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": estado["content"]}}]}

    class Cliente:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            estado["llamadas"] += 1
            estado["headers"].append(headers)
            estado["enviados"].append(json)
            if estado["falla"]:
                raise estado["falla"]
            return Resp()

    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "clave-de-prueba")
    monkeypatch.setattr(llm.httpx, "AsyncClient", Cliente)
    return estado


def _leer(mime: str = "image/jpeg"):
    return asyncio.run(llm.leer_comprobante(IMAGEN, mime))


def test_la_peticion_lleva_la_imagen_completa_y_el_modelo_de_vision(modelo):
    _leer()

    assert modelo["llamadas"] == 1
    peticion = modelo["enviados"][0]
    assert peticion["model"] == config.DEEPSEEK_VISION_MODEL
    assert peticion["temperature"] == 0
    assert modelo["headers"][0]["Authorization"] == "Bearer clave-de-prueba"

    bloques = peticion["messages"][0]["content"]
    imagen = next(b for b in bloques if b["type"] == "image_url")
    # Los dígitos chicos (clave de rastreo, cuenta) no sobreviven a una reducción:
    # se pide la imagen tal como llega.
    assert imagen["image_url"]["detail"] == "original"
    assert imagen["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(IMAGEN).decode()
    )


def test_un_error_de_red_devuelve_none_sin_propagar(modelo):
    modelo["falla"] = httpx.HTTPError("se cayó la red")

    assert _leer() is None


def test_respuesta_que_no_es_json_devuelve_none(modelo):
    modelo["content"] = "No es un comprobante, es una foto de mi gato."

    assert _leer() is None


def test_mime_no_legible_no_hace_la_peticion(modelo):
    assert _leer("application/pdf") is None
    assert modelo["llamadas"] == 0


def test_sin_key_no_hace_la_peticion(modelo, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")

    assert _leer() is None
    assert modelo["llamadas"] == 0


def test_lectura_valida_vuelve_saneada(modelo):
    lectura = _leer()

    assert lectura is not None
    assert lectura["monto"] == 200.0
    assert isinstance(lectura["monto"], float)
    assert lectura["fecha"] == date(2026, 11, 24)
    assert lectura["clave_rastreo"] == "HU1234ABCD5678"
    assert lectura["cuenta_digitos"] == "1234"
    assert lectura["beneficiario"] == "Ambar Estudio"
