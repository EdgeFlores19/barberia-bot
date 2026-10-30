"""Normalización de teléfonos. Módulo puro: lo importan el motor y el adaptador."""
from __future__ import annotations


def _digitos(texto: str | None) -> str:
    return "".join(ch for ch in (texto or "") if ch.isdigit())


def t10(texto: str | None) -> str:
    """Últimos 10 dígitos: '5215500000011' y '525500000011' dan '5500000011'."""
    return _digitos(texto)[-10:]


def t4(texto: str | None) -> str:
    """Últimos 4 dígitos, para logs."""
    return _digitos(texto)[-4:] or "----"
