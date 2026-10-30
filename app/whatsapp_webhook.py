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


# Tipos que Meta manda y que no son un mensaje del cliente: se descartan sin avisar.
# El aviso de "solo entiendo texto" queda solo para image, audio, video, document,
# sticker y location (Tarea 18).
TIPOS_IGNORADOS = frozenset({"reaction", "unsupported", "system", "errors", "ephemeral"})


def extraer_mensajes(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Mensajes del payload. `texto` queda vacío si el tipo no está soportado."""
    salida: list[dict[str, str]] = []
    for entrada in payload.get("entry") or []:
        for cambio in entrada.get("changes") or []:
            valor = cambio.get("value") or {}
            for msg in valor.get("messages") or []:
                msg_id = str(msg.get("id") or "")
                wa_id = str(msg.get("from") or "")
                tipo = str(msg.get("type") or "")
                if tipo in TIPOS_IGNORADOS:
                    continue          # reaction, unsupported, system, errors, ...
                texto = ""
                if tipo == "text":
                    texto = ((msg.get("text") or {}).get("body") or "").strip()
                elif tipo == "interactive":
                    inter = msg.get("interactive") or {}
                    elegido = inter.get("button_reply") or inter.get("list_reply") or {}
                    texto = str(elegido.get("id") or "").strip()
                if msg_id and wa_id:
                    salida.append({"id": msg_id, "wa_id": wa_id, "texto": texto, "tipo": tipo})
    return salida


# Los datos del negocio no cambian en caliente: se leen una sola vez.
_DATOS: dict[str, Any] | None = None


def _datos() -> dict[str, Any]:
    global _DATOS
    if _DATOS is None:
        _DATOS = engine.cargar_datos()
    return _DATOS


async def procesar_evento(payload: dict[str, Any], enviar: Enviador | None = None) -> int:
    """Atiende cada mensaje nuevo del payload. Devuelve cuántos mensajes respondió."""
    enviar = enviar or wa_cliente.enviar
    atendidos = 0

    for msg in extraer_mensajes(payload):
        if not store.registrar_evento(msg["id"]):
            log.info("wa: evento repetido (reintento de Meta)")
            continue

        if not msg["texto"]:
            await _enviar(msg["wa_id"], [{"tipo": "texto", "texto": AVISO_SIN_TEXTO}], enviar)
            atendidos += 1
            continue

        # El motor registra el KPI del handoff y deja la sesión con su teléfono.
        respuestas, _ = await engine.manejar(
            f"wa:{msg['wa_id']}", msg["texto"], _datos(), canal="wa", telefono=msg["wa_id"]
        )
        await _enviar(msg["wa_id"], respuestas, enviar)
        atendidos += 1

    return atendidos


async def _enviar(wa_id: str, respuestas: list[dict[str, Any]], enviar: Enviador) -> None:
    for payload in whatsapp_formato.a_payloads(respuestas):
        await enviar(wa_id, payload)
