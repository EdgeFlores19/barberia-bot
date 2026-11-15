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


def _contacto(telefono: str) -> str:
    return f"WhatsApp: https://wa.me/{telefono}" if telefono else "Canal: chat web (sin teléfono)"


def notificar_anticipo(*, cita: dict, servicio: str, etiqueta: str, restante: int,
                       telefono: str, urgente: bool, tardio: bool) -> None:
    """Llegó el comprobante de un anticipo: el dueño debe verificar el depósito."""
    log.warning("anticipo: comprobante de …%s para la cita %s%s", t4(telefono), cita["folio"],
                " (urgente)" if urgente else "")
    marca = "⚡ " if urgente else ""
    nota = "\n(Llegó después de vencer el apartado; el horario seguía libre.)" if tardio else ""
    _telegram(
        f"{marca}Comprobante de anticipo recibido.{nota}\n"
        f"Cita: {servicio} · {etiqueta}\n"
        f"Clienta: {cita['nombre']} · Folio {cita['folio']}\n"
        f"Anticipo: ${cita['anticipo_monto']:,} · Resta por pagar: ${restante:,}\n"
        f"Concepto esperado: {cita['nombre']} / {cita['folio']}\n"
        f"{_contacto(telefono)}\n"
        "Verifica el depósito en tu banco."
    )


def notificar_cancelacion_anticipo(*, cita: dict, servicio: str, etiqueta: str,
                                   telefono: str, devuelve: bool) -> None:
    """Una clienta canceló una cita con anticipo pagado."""
    log.warning("anticipo: cancelación de …%s (cita %s, %s)", t4(telefono), cita["folio"],
                "se devuelve" if devuelve else "se retiene")
    if devuelve:
        accion = f"Según tu política SE DEVUELVE el anticipo de ${cita['anticipo_monto']:,}: contáctala."
    else:
        accion = f"El anticipo de ${cita['anticipo_monto']:,} se queda, según tu política."
    _telegram(
        "❌ Cancelación de una cita con anticipo.\n"
        f"Cita: {servicio} · {etiqueta}\n"
        f"Clienta: {cita['nombre']} · Folio {cita['folio']}\n"
        f"{accion}\n{_contacto(telefono)}"
    )


def notificar_alerta(texto: str) -> None:
    log.error("alerta: %s", texto)
    _telegram(f"⚠️ {texto}")
