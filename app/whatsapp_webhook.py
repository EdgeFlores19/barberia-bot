"""Adaptador de WhatsApp Cloud API para el motor del bot.

El motor (app/engine.py) no sabe que existe WhatsApp: aquí se traduce
entrada de Meta a texto del motor y respuestas del motor a payloads de Graph.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from . import avisos, config, engine, store, wa_cliente, whatsapp_formato

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
        async with candado(msg["wa_id"]):
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


_ULTIMA_ALERTA: float | None = None


async def _alerta_envio_fallido() -> None:
    """Como máximo una alerta por hora."""
    global _ULTIMA_ALERTA
    ahora = time.monotonic()
    if _ULTIMA_ALERTA is not None and ahora - _ULTIMA_ALERTA < 3600:
        return
    _ULTIMA_ALERTA = ahora
    await asyncio.to_thread(
        avisos.notificar_alerta, "No pude enviar un mensaje de WhatsApp. Revisa WA_TOKEN (¿venció?)."
    )


async def _enviar(wa_id: str, respuestas: list[dict[str, Any]], enviar: Enviador) -> None:
    for payload in whatsapp_formato.a_payloads(respuestas):
        if not await enviar(wa_id, payload):
            await _alerta_envio_fallido()


@router.get("/wa/webhook")
def verificar(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
) -> PlainTextResponse:
    """Meta llama aquí una vez, al guardar la URL del webhook en la app."""
    if (
        hub_mode == "subscribe"
        and config.WA_VERIFY_TOKEN
        and hmac.compare_digest(hub_token.encode(), config.WA_VERIFY_TOKEN.encode())
    ):
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verificación fallida.")


@router.post("/wa/webhook")
async def recibir(request: Request, tareas: BackgroundTasks) -> dict[str, bool]:
    """Meta exige 200 en menos de 5 s: se contesta y el trabajo va en segundo plano."""
    if not config.wa_habilitado():
        raise HTTPException(status_code=503, detail="WhatsApp no configurado.")
    cuerpo = await request.body()
    if not firma_valida(cuerpo, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(status_code=403, detail="Firma inválida.")
    try:
        payload = json.loads(cuerpo or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="JSON inválido.") from exc
    tareas.add_task(_procesar_seguro, payload)
    return {"ok": True}


async def _procesar_seguro(payload: dict[str, Any]) -> None:
    try:
        await procesar_evento(payload)
    except Exception:  # noqa: BLE001 - un fallo aquí no debe tumbar el webhook
        log.exception("wa: fallo procesando el evento")


_CANDADOS: dict[str, asyncio.Lock] = {}


def candado(wa_id: str) -> asyncio.Lock:
    """Un lock por teléfono: serializa los mensajes de un mismo cliente."""
    if wa_id not in _CANDADOS:
        _CANDADOS[wa_id] = asyncio.Lock()
    return _CANDADOS[wa_id]
