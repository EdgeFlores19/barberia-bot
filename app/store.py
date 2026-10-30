"""Citas en SQLite. Fuente de verdad; Calendar es solo una copia opcional.

La interfaz pública arranca de la que usaba el store en memoria de F1
(ocupados, crear, listar, reset) y añade folio, cancelar y reagendar.
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime
from typing import Any

from . import config

# Sin caracteres que se confundan al dictarlos por teléfono (0/O, 1/I/L).
ALFABETO_FOLIO = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PREFIJO_FOLIO = "AMB"

_candado = threading.Lock()
_inicializada = False


class SlotOcupado(Exception):
    """Otra cita confirmada ya tomó ese horario."""


class FolioNoEncontrado(Exception):
    """No hay cita con ese folio."""


ESQUEMA = """
CREATE TABLE IF NOT EXISTS citas (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    folio             TEXT NOT NULL UNIQUE,
    slot              TEXT NOT NULL,
    servicio_id       TEXT NOT NULL,
    nombre            TEXT NOT NULL,
    telefono          TEXT NOT NULL DEFAULT '',
    estado            TEXT NOT NULL DEFAULT 'confirmada'
                      CHECK (estado IN ('confirmada', 'cancelada')),
    creada            TEXT NOT NULL,
    actualizada       TEXT NOT NULL,
    calendar_event_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_citas_slot ON citas (slot);
CREATE INDEX IF NOT EXISTS ix_citas_estado ON citas (estado);
"""


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_FILE, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init() -> None:
    """Crea el esquema la primera vez. Idempotente."""
    global _inicializada
    if _inicializada:
        return
    with _candado:
        if _inicializada:
            return
        config.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _con() as con:
            con.executescript(ESQUEMA)
        _inicializada = True


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _fila(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def _nuevo_folio(con: sqlite3.Connection) -> str:
    for _ in range(40):
        cuerpo = "".join(secrets.choice(ALFABETO_FOLIO) for _ in range(4))
        folio = f"{PREFIJO_FOLIO}-{cuerpo}"
        if not con.execute("SELECT 1 FROM citas WHERE folio = ?", (folio,)).fetchone():
            return folio
    raise RuntimeError("No se pudo generar un folio único")


def ocupados() -> set[str]:
    """Slots ISO con una cita confirmada."""
    init()
    with _con() as con:
        filas = con.execute("SELECT slot FROM citas WHERE estado = 'confirmada'").fetchall()
    return {f["slot"] for f in filas}


def confirmadas() -> list[dict[str, Any]]:
    """Citas confirmadas: lo que consume la capa de disponibilidad."""
    init()
    with _con() as con:
        filas = con.execute(
            "SELECT folio, slot, servicio_id, nombre, estado FROM citas WHERE estado = 'confirmada'"
        ).fetchall()
    return [dict(f) for f in filas]


def crear(slot: str, servicio_id: str, nombre: str, telefono: str = "") -> dict[str, Any]:
    """Crea la cita. Lanza SlotOcupado si el horario ya está tomado."""
    init()
    with _candado, _con() as con:
        choque = con.execute(
            "SELECT folio FROM citas WHERE slot = ? AND estado = 'confirmada'", (slot,)
        ).fetchone()
        if choque:
            raise SlotOcupado(slot)
        folio = _nuevo_folio(con)
        ahora = _ahora()
        con.execute(
            "INSERT INTO citas (folio, slot, servicio_id, nombre, telefono, estado, creada, actualizada)"
            " VALUES (?, ?, ?, ?, ?, 'confirmada', ?, ?)",
            (folio, slot, servicio_id, nombre, telefono, ahora, ahora),
        )
        fila = con.execute("SELECT * FROM citas WHERE folio = ?", (folio,)).fetchone()
    return _fila(fila) or {}


def obtener(folio: str) -> dict[str, Any] | None:
    init()
    with _con() as con:
        fila = con.execute(
            "SELECT * FROM citas WHERE folio = ?", (folio.strip().upper(),)
        ).fetchone()
    return _fila(fila)


def cancelar(folio: str) -> dict[str, Any]:
    """Cancela la cita y libera el slot al instante."""
    init()
    with _candado, _con() as con:
        fila = con.execute(
            "SELECT * FROM citas WHERE folio = ?", (folio.strip().upper(),)
        ).fetchone()
        if not fila:
            raise FolioNoEncontrado(folio)
        if fila["estado"] != "cancelada":
            con.execute(
                "UPDATE citas SET estado = 'cancelada', actualizada = ? WHERE folio = ?",
                (_ahora(), fila["folio"]),
            )
            fila = con.execute("SELECT * FROM citas WHERE folio = ?", (fila["folio"],)).fetchone()
    return _fila(fila) or {}


def reagendar(folio: str, nuevo_slot: str) -> dict[str, Any]:
    """Mueve la cita a otro horario. Lanza SlotOcupado si ya está tomado."""
    init()
    with _candado, _con() as con:
        fila = con.execute(
            "SELECT * FROM citas WHERE folio = ?", (folio.strip().upper(),)
        ).fetchone()
        if not fila or fila["estado"] == "cancelada":
            raise FolioNoEncontrado(folio)
        choque = con.execute(
            "SELECT folio FROM citas WHERE slot = ? AND estado = 'confirmada' AND folio <> ?",
            (nuevo_slot, fila["folio"]),
        ).fetchone()
        if choque:
            raise SlotOcupado(nuevo_slot)
        con.execute(
            "UPDATE citas SET slot = ?, actualizada = ? WHERE folio = ?",
            (nuevo_slot, _ahora(), fila["folio"]),
        )
        nueva = con.execute("SELECT * FROM citas WHERE folio = ?", (fila["folio"],)).fetchone()
    return _fila(nueva) or {}


def set_evento(folio: str, event_id: str | None) -> None:
    """Guarda (o limpia) el id del evento espejo en Google Calendar."""
    init()
    with _candado, _con() as con:
        con.execute(
            "UPDATE citas SET calendar_event_id = ?, actualizada = ? WHERE folio = ?",
            (event_id, _ahora(), folio.strip().upper()),
        )


def listar(solo_confirmadas: bool = False) -> list[dict[str, Any]]:
    init()
    sql = "SELECT * FROM citas"
    if solo_confirmadas:
        sql += " WHERE estado = 'confirmada'"
    sql += " ORDER BY slot"
    with _con() as con:
        return [dict(f) for f in con.execute(sql).fetchall()]


def reset() -> None:
    """Vacía la tabla. Solo para pruebas y para reiniciar la demo."""
    init()
    with _candado, _con() as con:
        con.execute("DELETE FROM citas")
