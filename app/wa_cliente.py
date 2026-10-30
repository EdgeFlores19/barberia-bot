"""Envío saliente a la WhatsApp Cloud API. Única puerta a Meta."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config

log = logging.getLogger(__name__)


def _url() -> str:
    return f"https://graph.facebook.com/{config.WA_API_VERSION}/{config.WA_PHONE_ID}/messages"


async def enviar(wa_id: str, payload: dict[str, Any]) -> bool:
    """Manda un payload ya armado. Devuelve True si Meta lo aceptó."""
    cuerpo = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": wa_id,
        **payload,
    }

    if config.WA_MODO == "consola":
        print(f"[wa:consola] -> {wa_id} {cuerpo}")
        return True

    if not (config.WA_TOKEN and config.WA_PHONE_ID):
        log.warning("wa: faltan WA_TOKEN o WA_PHONE_ID; no se envía nada")
        return False

    cabeceras = {"Authorization": f"Bearer {config.WA_TOKEN}"}
    for intento in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=10) as cli:
                r = await cli.post(_url(), json=cuerpo, headers=cabeceras)
        except httpx.HTTPError as exc:
            log.warning("wa: error de red (intento %s): %s", intento, exc)
            continue
        if r.status_code < 300:
            return True
        log.error("wa: Meta respondió %s %s", r.status_code, r.text[:300])
        if r.status_code < 500:
            # 400/401/404 no se arreglan reintentando.
            return False
    return False
