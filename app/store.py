"""Citas en SQLite. Fuente de verdad; Calendar es solo una copia opcional.

La interfaz pública arranca de la que usaba el store en memoria de F1
(ocupados, crear, listar, reset) y añade folio, cancelar y reagendar.
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any

from . import config, reloj
from .telefono import t10

# Sin caracteres que se confundan al dictarlos por teléfono (0/O, 1/I/L).
ALFABETO_FOLIO = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
PREFIJO_FOLIO = "AMB"

_candado = threading.Lock()
_inicializada = False


class SlotOcupado(Exception):
    """Otra cita confirmada ya tomó ese horario."""


class FolioNoEncontrado(Exception):
    """No hay cita con ese folio."""


class EstadoInvalido(Exception):
    """La cita no está en un estado que admita esa operación."""


# Estados: confirmada | cancelada | pendiente (apartada, esperando el comprobante del
# anticipo) | expirada (el apartado venció sin comprobante).
_CITAS_SQL = """
CREATE TABLE IF NOT EXISTS citas (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    folio             TEXT NOT NULL UNIQUE,
    slot              TEXT NOT NULL,
    servicio_id       TEXT NOT NULL,
    nombre            TEXT NOT NULL,
    telefono          TEXT NOT NULL DEFAULT '',
    estado            TEXT NOT NULL DEFAULT 'confirmada'
                      CHECK (estado IN ('confirmada', 'cancelada', 'pendiente', 'expirada')),
    creada            TEXT NOT NULL,
    actualizada       TEXT NOT NULL,
    calendar_event_id TEXT,
    anticipo_monto       INTEGER NOT NULL DEFAULT 0,
    vence                TEXT,
    aceptado_en          TEXT,
    comprobante_en       TEXT,
    comprobante_tipo     TEXT,
    comprobante_media_id TEXT,
    reagendadas          INTEGER NOT NULL DEFAULT 0
)
"""
_INDICES_CITAS = (
    "CREATE INDEX IF NOT EXISTS ix_citas_slot ON citas (slot)",
    "CREATE INDEX IF NOT EXISTS ix_citas_estado ON citas (estado)",
)
_COLUMNAS_VIEJAS = "id, folio, slot, servicio_id, nombre, telefono, estado, creada, actualizada, calendar_event_id"

ESQUEMA = _CITAS_SQL + ";\n" + ";\n".join(_INDICES_CITAS) + ";\n\n"
ESQUEMA += """
CREATE TABLE IF NOT EXISTS wa_eventos (
    id        TEXT PRIMARY KEY,
    recibido  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoffs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sid      TEXT NOT NULL,
    mensaje  TEXT NOT NULL,
    creado   TEXT NOT NULL
);

-- Mensajes atendidos por el canal WhatsApp: topes por teléfono y global.
CREATE TABLE IF NOT EXISTS wa_uso (
    telefono TEXT NOT NULL,
    momento  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wa_uso_momento ON wa_uso (momento);
CREATE INDEX IF NOT EXISTS ix_wa_uso_telefono_momento ON wa_uso (telefono, momento);

-- Concentrado de depósitos ya mandado, por día. Evita que el resumen salga dos veces si el
-- proceso se reinicia (o si el bucle de app/main.py vuelve a pasar por la misma hora).
CREATE TABLE IF NOT EXISTS resumen_envios (
    dia          TEXT PRIMARY KEY,
    enviado      TEXT NOT NULL,
    total_citas  INTEGER NOT NULL DEFAULT 0,
    corte        TEXT
);
"""


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_FILE, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _migrar_citas(con: sqlite3.Connection) -> None:
    """Bases creadas antes del anticipo: reconstruye `citas` con los estados y columnas nuevos.

    SQLite no deja cambiar un CHECK, así que se renombra, se crea la nueva y se copia, todo en
    UNA transacción: si algo falla se deshace y la tabla vieja queda intacta (hay disco persistente).
    """
    fila = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'citas'").fetchone()
    if fila is None or "'pendiente'" in fila["sql"]:
        return
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("ALTER TABLE citas RENAME TO citas_vieja")
        con.execute("DROP INDEX IF EXISTS ix_citas_slot")
        con.execute("DROP INDEX IF EXISTS ix_citas_estado")
        con.execute(_CITAS_SQL)
        for sql in _INDICES_CITAS:
            con.execute(sql)
        con.execute(f"INSERT INTO citas ({_COLUMNAS_VIEJAS}) SELECT {_COLUMNAS_VIEJAS} FROM citas_vieja")
        con.execute("DROP TABLE citas_vieja")
        con.commit()
    except BaseException:
        con.rollback()
        raise


def _agregar_columna(con: sqlite3.Connection, tabla: str, columna: str, tipo: str) -> None:
    """ALTER TABLE ... ADD COLUMN si falta (SQLite no tiene IF NOT EXISTS para columnas)."""
    existentes = {f["name"] for f in con.execute(f"PRAGMA table_info({tabla})").fetchall()}
    if columna not in existentes:
        con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")


def init() -> None:
    """Crea el esquema la primera vez (y migra bases viejas). Idempotente."""
    global _inicializada
    if _inicializada:
        return
    with _candado:
        if _inicializada:
            return
        config.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _con() as con:
            _migrar_citas(con)
            con.executescript(ESQUEMA)
            _agregar_columna(con, "resumen_envios", "corte", "TEXT")
        _inicializada = True


def _ahora() -> str:
    return reloj.ahora().isoformat(timespec="seconds")


def _fila(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def _nuevo_folio(con: sqlite3.Connection) -> str:
    for _ in range(40):
        cuerpo = "".join(secrets.choice(ALFABETO_FOLIO) for _ in range(4))
        folio = f"{PREFIJO_FOLIO}-{cuerpo}"
        if not con.execute("SELECT 1 FROM citas WHERE folio = ?", (folio,)).fetchone():
            return folio
    raise RuntimeError("No se pudo generar un folio único")


# Una cita bloquea el horario si está confirmada o si es un apartado que aún no vence.
_BLOQUEA = "(estado = 'confirmada' OR (estado = 'pendiente' AND vence > ?))"


def ocupados(ahora: str | None = None) -> set[str]:
    """Slots ISO con una cita confirmada o un apartado vigente."""
    init()
    with _con() as con:
        filas = con.execute(f"SELECT slot FROM citas WHERE {_BLOQUEA}", (ahora or _ahora(),)).fetchall()
    return {f["slot"] for f in filas}


def confirmadas(ahora: str | None = None) -> list[dict[str, Any]]:
    """Citas que ocupan agenda: las confirmadas y los apartados vigentes (lo que consume slots)."""
    init()
    with _con() as con:
        filas = con.execute(
            f"SELECT folio, slot, servicio_id, nombre, estado FROM citas WHERE {_BLOQUEA}",
            (ahora or _ahora(),),
        ).fetchall()
    return [dict(f) for f in filas]


def crear(
    slot: str, servicio_id: str, nombre: str, telefono: str = "", *,
    pendiente_hasta: str | None = None, anticipo: int = 0, aceptado_en: str | None = None,
    ahora: str | None = None,
) -> dict[str, Any]:
    """Crea la cita. Lanza SlotOcupado si el horario ya está tomado.

    Con `pendiente_hasta` (ISO) queda apartada, esperando el comprobante del anticipo,
    y el horario se libera solo cuando esa hora pasa.
    """
    init()
    ahora = ahora or _ahora()
    with _candado, _con() as con:
        choque = con.execute(
            f"SELECT folio FROM citas WHERE slot = ? AND {_BLOQUEA}", (slot, ahora)
        ).fetchone()
        if choque:
            raise SlotOcupado(slot)
        folio = _nuevo_folio(con)
        estado = "pendiente" if pendiente_hasta else "confirmada"
        con.execute(
            "INSERT INTO citas (folio, slot, servicio_id, nombre, telefono, estado, creada, actualizada,"
            " anticipo_monto, vence, aceptado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (folio, slot, servicio_id, nombre, telefono, estado, ahora, ahora,
             anticipo, pendiente_hasta, aceptado_en),
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


def reagendar(folio: str, nuevo_slot: str, ahora: str | None = None) -> dict[str, Any]:
    """Mueve la cita a otro horario y cuenta el cambio. Lanza SlotOcupado si ya está tomado."""
    init()
    ahora = ahora or _ahora()
    with _candado, _con() as con:
        fila = con.execute(
            "SELECT * FROM citas WHERE folio = ?", (folio.strip().upper(),)
        ).fetchone()
        if not fila or fila["estado"] in ("cancelada", "expirada"):
            raise FolioNoEncontrado(folio)
        choque = con.execute(
            f"SELECT folio FROM citas WHERE slot = ? AND {_BLOQUEA} AND folio <> ?",
            (nuevo_slot, ahora, fila["folio"]),
        ).fetchone()
        if choque:
            raise SlotOcupado(nuevo_slot)
        con.execute(
            "UPDATE citas SET slot = ?, reagendadas = reagendadas + 1, actualizada = ? WHERE folio = ?",
            (nuevo_slot, ahora, fila["folio"]),
        )
        nueva = con.execute("SELECT * FROM citas WHERE folio = ?", (fila["folio"],)).fetchone()
    return _fila(nueva) or {}


def registrar_comprobante(folio: str, tipo: str, media_id: str, ahora: str | None = None) -> dict[str, Any]:
    """El apartado pasa a cita confirmada. Funciona también si el apartado ya venció,
    siempre que nadie más haya tomado el horario (si lo tomaron, SlotOcupado)."""
    init()
    ahora = ahora or _ahora()
    with _candado, _con() as con:
        fila = con.execute("SELECT * FROM citas WHERE folio = ?", (folio.strip().upper(),)).fetchone()
        if not fila:
            raise FolioNoEncontrado(folio)
        if fila["estado"] not in ("pendiente", "expirada"):
            raise EstadoInvalido(fila["estado"])
        choque = con.execute(
            f"SELECT folio FROM citas WHERE slot = ? AND {_BLOQUEA} AND folio <> ?",
            (fila["slot"], ahora, fila["folio"]),
        ).fetchone()
        if choque:
            raise SlotOcupado(fila["slot"])
        con.execute(
            "UPDATE citas SET estado = 'confirmada', comprobante_en = ?, comprobante_tipo = ?,"
            " comprobante_media_id = ?, actualizada = ? WHERE folio = ?",
            (ahora, tipo, media_id, ahora, fila["folio"]),
        )
        nueva = con.execute("SELECT * FROM citas WHERE folio = ?", (fila["folio"],)).fetchone()
    return _fila(nueva) or {}


def expirar_vencidas(ahora: str | None = None) -> list[dict[str, Any]]:
    """Marca como expiradas las citas pendientes cuyo apartado ya venció y las devuelve."""
    init()
    ahora = ahora or _ahora()
    with _candado, _con() as con:
        filas = con.execute(
            "SELECT * FROM citas WHERE estado = 'pendiente' AND vence <= ?", (ahora,)
        ).fetchall()
        for f in filas:
            con.execute("UPDATE citas SET estado = 'expirada', actualizada = ? WHERE folio = ?", (ahora, f["folio"]))
    return [{**dict(f), "estado": "expirada"} for f in filas]


def apartado_reciente(telefono: str, ahora: str | None = None, gracia_horas: int = 24) -> dict[str, Any] | None:
    """Cita pendiente (o expirada hace poco) de esa clienta: a la que corresponde un comprobante."""
    init()
    ahora_dt = reloj.ahora() if ahora is None else datetime.fromisoformat(ahora)
    limite = (ahora_dt - timedelta(hours=gracia_horas)).isoformat(timespec="seconds")
    with _con() as con:
        filas = con.execute(
            "SELECT * FROM citas WHERE estado IN ('pendiente', 'expirada') AND vence > ? ORDER BY creada DESC, id DESC",
            (limite,),
        ).fetchall()
    for f in filas:
        if f["telefono"] and t10(f["telefono"]) == t10(telefono):
            return dict(f)
    return None


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
    """Vacía las tablas de trabajo. Solo para pruebas y para reiniciar la demo.

    Incluye `resumen_envios`: un reinicio de la demo también reinicia el día, y así el
    concentrado se puede volver a mandar para verlo funcionar.
    """
    init()
    with _candado, _con() as con:
        con.execute("DELETE FROM citas")
        con.execute("DELETE FROM resumen_envios")


def registrar_evento(evento_id: str) -> bool:
    """True la primera vez que se ve el id del mensaje; False si Meta lo reintentó.

    Meta reintenta el webhook si no recibe 200 en 5 s. Sin esta tabla un
    reintento vuelve a procesar el mismo mensaje y puede agendar dos citas.
    """
    init()
    with _candado, _con() as con:
        try:
            con.execute(
                "INSERT INTO wa_eventos (id, recibido) VALUES (?, ?)", (evento_id, _ahora())
            )
        except sqlite3.IntegrityError:
            return False
    return True


def registrar_handoff(sid: str, mensaje: str) -> None:
    """Deja rastro de cada conversación que pidió humano (es el KPI del bot)."""
    init()
    with _candado, _con() as con:
        con.execute(
            "INSERT INTO handoffs (sid, mensaje, creado) VALUES (?, ?, ?)",
            (sid, (mensaje or "")[:500], _ahora()),
        )


def handoffs() -> list[dict[str, Any]]:
    init()
    with _con() as con:
        return [dict(f) for f in con.execute("SELECT * FROM handoffs ORDER BY id DESC").fetchall()]


def wa_sumar(telefono: str, momento: str) -> None:
    """Anota un mensaje atendido del canal WhatsApp (teléfono ya normalizado)."""
    init()
    with _candado, _con() as con:
        con.execute("INSERT INTO wa_uso (telefono, momento) VALUES (?, ?)", (telefono, momento))


def wa_contar_desde(desde: str, telefono: str | None = None) -> int:
    """Mensajes atendidos desde `desde` (ISO). Sin teléfono cuenta todos."""
    init()
    sql = "SELECT COUNT(*) FROM wa_uso WHERE momento >= ?"
    args: list[Any] = [desde]
    if telefono:
        sql += " AND telefono = ?"
        args.append(telefono)
    with _con() as con:
        return con.execute(sql, args).fetchone()[0]


def resumen_hecho(dia: str) -> bool:
    """¿Ya salió el concentrado de ese día? Evita mandarlo dos veces."""
    init()
    with _con() as con:
        return con.execute("SELECT 1 FROM resumen_envios WHERE dia = ?", (dia,)).fetchone() is not None


def resumen_marcar(dia: str, total: int = 0, corte: str | None = None) -> None:
    """Anota que el concentrado de `dia` ya se mandó (idempotente).

    `corte` es el instante hasta el que llegaba ESE concentrado (se toma al armarlo, no al
    mandarlo): el siguiente cubre desde ahí, sin dejar huecos ni repetir depósitos.
    """
    init()
    with _candado, _con() as con:
        con.execute(
            "INSERT INTO resumen_envios (dia, enviado, total_citas, corte) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (dia) DO UPDATE SET enviado = excluded.enviado,"
            " total_citas = excluded.total_citas, corte = excluded.corte",
            (dia, _ahora(), int(total), corte),
        )


def ultimo_corte() -> str | None:
    """Hasta cuándo llegó el último concentrado mandado (None si nunca se ha mandado uno).

    Las filas anteriores a la columna `corte` no la tienen: se usa su hora de envío.
    """
    init()
    with _con() as con:
        fila = con.execute(
            "SELECT COALESCE(corte, enviado) AS corte FROM resumen_envios ORDER BY dia DESC LIMIT 1"
        ).fetchone()
    return fila["corte"] if fila else None
