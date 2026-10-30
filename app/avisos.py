"""Avisos al dueño. Log por defecto; Telegram si está configurado."""
from __future__ import annotations

import logging

import httpx

from . import config
from .telefono import t4

log = logging.getLogger(__name__)


def _telegram(texto: str) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": texto},
            timeout=5,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.error("aviso: Telegram respondió %s", exc.response.status_code)   # nunca loguear exc: lleva la URL con el token
    except httpx.HTTPError as exc:
        log.error("aviso: error de red al avisar por Telegram (%s)", type(exc).__name__)


def notificar_handoff(sid: str, telefono: str, mensaje: str) -> None:
    log.warning("handoff: cliente …%s pide hablar con una persona", t4(telefono))
    if telefono:
        contacto = f"Tel: +{telefono}\nWhatsApp: https://wa.me/{telefono}"
    else:
        contacto = "Canal: chat web (sin teléfono)"
    _telegram(f"Un cliente pide hablar con una persona.\n{contacto}\nDijo: {mensaje[:200]}")


def notificar_alerta(texto: str) -> None:
    log.error("alerta: %s", texto)
    _telegram(f"⚠️ {texto}")
