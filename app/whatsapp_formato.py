"""Traduce las respuestas del motor a payloads de WhatsApp Cloud API.

El motor devuelve {'tipo': 'texto'|'botones', ...}. WhatsApp limita:
3 reply buttons o 10 filas de lista, títulos de 24 caracteres (filas) y
20 (botones). Todo el recorte de aquí abajo existe por esos límites.
"""
from __future__ import annotations

from typing import Any

MAX_TEXTO = 4096
MAX_CUERPO_INTERACTIVO = 1024
MAX_FILAS = 10
MAX_TITULO_FILA = 24
MAX_TITULO_BOTON = 20
MAX_BOTONES = 3


def _texto(cuerpo: str) -> dict[str, Any]:
    return {"type": "text", "text": {"body": (cuerpo or "")[:MAX_TEXTO]}}


def a_payloads(respuestas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte la lista de respuestas del motor en payloads para la Graph API."""
    payloads: list[dict[str, Any]] = []
    for r in respuestas or []:
        texto = (r.get("texto") or "").strip() or "Elige una opción:"
        payloads.append(_texto(texto))
    return payloads
