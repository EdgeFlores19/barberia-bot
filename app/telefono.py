"""Normalización de teléfonos. Módulo puro: lo importan el motor y el adaptador."""
from __future__ import annotations


def _digitos(texto: str | None) -> str:
    return "".join(ch for ch in (texto or "") if ch.isdigit())


def t10(texto: str | None) -> str:
    """Últimos 10 dígitos: '5215500000011' y '525500000011' dan '5500000011'."""
    return _digitos(texto)[-10:]


def a_whatsapp(texto: str | None) -> str:
    """Grafía que Meta acepta en `to`: dígitos, sin el `1` heredado de los móviles MX.

    '5215500000011' -> '525500000011'   (13 dígitos que empiezan con 521)
    '525500000011'  -> '525500000011'   (ya canónico, no se toca)
    '14155550100'   -> '14155550100'    (otro país, no se toca)
    """
    digitos = _digitos(texto)
    if len(digitos) == 13 and digitos.startswith("521"):
        return "52" + digitos[-10:]
    return digitos


def t4(texto: str | None) -> str:
    """Últimos 4 dígitos, para logs."""
    return _digitos(texto)[-4:] or "----"
