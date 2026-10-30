"""Adaptador de WhatsApp Cloud API para el motor del bot.

El motor (app/engine.py) no sabe que existe WhatsApp: aquí se traduce
entrada de Meta a texto del motor y respuestas del motor a payloads de Graph.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from . import config, engine, store, wa_cliente, whatsapp_formato

log = logging.getLogger(__name__)

router = APIRouter()

Enviador = Callable[[str, dict[str, Any]], Awaitable[bool]]

AVISO_SIN_TEXTO = (
    "Por ahora solo entiendo mensajes de texto. Escribe *menu* y te muestro las opciones."
)


def firma_valida(cuerpo: bytes, cabecera: str) -> bool:
    """Valida X-Hub-Signature-256: HMAC-SHA256 del cuerpo crudo con el app secret."""
    if not config.WA_APP_SECRET or not cabecera.startswith("sha256="):
        return False
    esperado = hmac.new(config.WA_APP_SECRET.encode(), cuerpo, hashlib.sha256).hexdigest()
    recibido = cabecera.split("=", 1)[1].strip()
    return hmac.compare_digest(esperado.encode(), recibido.encode())
