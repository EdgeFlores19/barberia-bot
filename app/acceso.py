"""Acceso al chat web: sid firmados, código de demo y rate limit en memoria.

Sin dependencias del framework: se prueba solo. Todo vive en el proceso (Render corre
un solo worker), así que un reinicio vacía los contadores.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections import deque

from . import config

_VENTANA_S = 60.0
_MAX_CLAVES = 20000          # tope de memoria del limitador; al llegar se vacía
_REGISTRO: dict[str, deque[float]] = {}


# ---------- sid firmado ----------
def _firma(base: str) -> str:
    return hmac.new(config.SESSION_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()[:20]


def nuevo_sid() -> str:
    """'<12 hex>.<firma>': solo /api/sesion puede emitirlos."""
    base = uuid.uuid4().hex[:12]
    return f"{base}.{_firma(base)}"


def sid_valido(sid: str) -> bool:
    """True si el sid lo emitió este servidor. Inventar un sid ya no crea sesiones."""
    base, sep, firma = sid.partition(".")
    if not sep or len(base) != 12 or not firma:
        return False
    return hmac.compare_digest(firma.encode(), _firma(base).encode())


# ---------- código de demo ----------
def codigo_ok(recibido: str) -> bool:
    """Comparación en tiempo constante (y sin TypeError con acentos)."""
    return hmac.compare_digest(recibido.strip().encode(), config.DEMO_CODE.encode())


# ---------- IP y rate limit ----------
def ip_cliente(xff: str, peer: str, desde_derecha: int = 0) -> str:
    """IP para el limitador. Detrás de Render `peer` es el proxy, no el cliente.

    X-Forwarded-For lo puede falsear el cliente en sus posiciones de la izquierda:
    por eso `desde_derecha` permite fiarse solo de lo que agrega un proxy propio.
    """
    partes = [p.strip() for p in xff.split(",") if p.strip()]
    if partes:
        try:
            return partes[-desde_derecha if desde_derecha > 0 else 0][:64]
        except IndexError:
            pass
    return (peer or "?")[:64]


def permitir(clave: str, ip: str, maximo: int, ahora: float | None = None) -> bool:
    """Ventana deslizante de 60 s por (clave, ip). False = pasó el tope."""
    t = time.monotonic() if ahora is None else ahora
    k = f"{clave}:{ip}"
    cola = _REGISTRO.get(k)
    if cola is None:
        if len(_REGISTRO) >= _MAX_CLAVES:
            _REGISTRO.clear()
        cola = _REGISTRO[k] = deque()
    while cola and t - cola[0] > _VENTANA_S:
        cola.popleft()
    if len(cola) >= maximo:
        return False
    cola.append(t)
    return True
