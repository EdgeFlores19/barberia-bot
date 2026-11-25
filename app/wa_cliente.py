"""Envío saliente a la WhatsApp Cloud API. Única puerta a Meta."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from . import config, telefono

log = logging.getLogger(__name__)

# Último `error.code` que devolvió Meta, para el diagnóstico de `/health`.
# 0 = sin error registrado. Nunca guarda teléfonos ni texto de clientes.
ULTIMO_CODIGO_META = 0


def ultimo_codigo() -> int:
    return ULTIMO_CODIGO_META


def _url() -> str:
    return f"https://graph.facebook.com/{config.WA_API_VERSION}/{config.WA_PHONE_ID}/messages"


async def descargar_media(media_id: str) -> tuple[bytes, str] | None:
    """Baja un archivo que la clienta mandó (el comprobante). (bytes, mime) o None si no se pudo.

    Son dos pasos: primero los metadatos (URL temporal, tipo y tamaño) y luego los bytes, ambos con
    el token. Esa URL caduca en minutos: hay que bajar el archivo al recibirlo.
    VERIFICAR contra la documentación vigente de Meta (ruta, campos y encabezados).
    """
    if config.WA_MODO == "consola" or not (config.WA_TOKEN and media_id):
        return None
    cabeceras = {"Authorization": f"Bearer {config.WA_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            meta = await cli.get(f"https://graph.facebook.com/{config.WA_API_VERSION}/{media_id}",
                                 headers=cabeceras)
            meta.raise_for_status()
            info = meta.json()
            url, mime = str(info.get("url") or ""), str(info.get("mime_type") or "")
            if not url.startswith("https://") or int(info.get("file_size") or 0) > config.COMPROBANTE_MAX_BYTES:
                return None
            r = await cli.get(url, headers=cabeceras)
            r.raise_for_status()
            contenido = r.content
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        log.warning("wa: no pude bajar el archivo (%s)", type(exc).__name__)
        return None
    if not contenido or len(contenido) > config.COMPROBANTE_MAX_BYTES:
        return None
    return contenido, mime


async def enviar(wa_id: str, payload: dict[str, Any]) -> bool:
    """Manda un payload ya armado. Devuelve True si Meta lo aceptó."""
    global ULTIMO_CODIGO_META
    ULTIMO_CODIGO_META = 0

    cuerpo = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": telefono.a_whatsapp(wa_id),
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
        try:
            codigo = int((json.loads(r.text).get("error") or {}).get("code") or 0)
        except (AttributeError, TypeError, ValueError):
            codigo = 0
        if codigo:
            ULTIMO_CODIGO_META = codigo
        if r.status_code < 500:
            # 400/401/404 no se arreglan reintentando.
            return False
    return False
