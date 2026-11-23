"""Citas en SQLite. Fuente de verdad; Calendar es solo una copia opcional.

La interfaz pública arranca de la que usaba el store en memoria de F1
(ocupados, crear, listar, reset) y añade folio, cancelar y reagendar.
"""
from __future__ import annotations

import hashlib
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
    reagendadas          INTEGER NOT NULL DEFAULT 0,
    -- Agenda (calendario de Google) donde vive la cita. '' = la agenda única del modo
    -- de un solo calendario, y también = "ocupa en todas" cuando hay varias agendas.
    agenda_id            TEXT NOT NULL DEFAULT '',
    agenda_nombre        TEXT NOT NULL DEFAULT ''
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

-- Un renglón por calendario de Google del negocio. El `calendar_id` es la identidad; la
-- `clave` (8 hex) es lo único que se puede mandar al cliente; el `nombre` es solo etiqueta.
CREATE TABLE IF NOT EXISTS agendas (
    calendar_id TEXT PRIMARY KEY,
    clave       TEXT NOT NULL UNIQUE,
    nombre      TEXT NOT NULL,
    orden       INTEGER NOT NULL DEFAULT 0,
    activa      INTEGER NOT NULL DEFAULT 1,
    ausencias   INTEGER NOT NULL DEFAULT 0,
    alta        TEXT NOT NULL
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
            # Bases que ya traían la tabla de citas: las columnas nuevas entran con su DEFAULT,
            # así que las citas viejas quedan con agenda_id='' y lo migrado no se pierde.
            _agregar_columna(con, "citas", "agenda_id", "TEXT NOT NULL DEFAULT ''")
            _agregar_columna(con, "citas", "agenda_nombre", "TEXT NOT NULL DEFAULT ''")
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


def _filtro_agenda(agenda_id: str | None) -> tuple[str, tuple[str, ...]]:
    """Filtro SQL por agenda. Sin agenda no se filtra; con agenda se ve lo suyo y lo viejo.

    Lo que quedó con `agenda_id=''` (citas anteriores a las agendas) se considera de todas
    las agendas: si no, al encender el modo multiagenda esos horarios ya vendidos volverían
    a aparecer libres.
    """
    if not agenda_id:
        return "", ()
    return " AND (agenda_id = ? OR agenda_id = '')", (agenda_id,)


def ocupados(ahora: str | None = None, *, agenda_id: str | None = None) -> set[str]:
    """Slots ISO con una cita confirmada o un apartado vigente (de esa agenda, si se pide)."""
    init()
    filtro, extra = _filtro_agenda(agenda_id)
    with _con() as con:
        filas = con.execute(
            f"SELECT slot FROM citas WHERE {_BLOQUEA}{filtro}", (ahora or _ahora(), *extra)
        ).fetchall()
    return {f["slot"] for f in filas}


def confirmadas(agenda_id: str | None = None, ahora: str | None = None) -> list[dict[str, Any]]:
    """Citas que ocupan agenda: las confirmadas y los apartados vigentes (lo que consume slots)."""
    init()
    filtro, extra = _filtro_agenda(agenda_id)
    with _con() as con:
        filas = con.execute(
            f"SELECT folio, slot, servicio_id, nombre, estado, agenda_id, agenda_nombre"
            f" FROM citas WHERE {_BLOQUEA}{filtro}",
            (ahora or _ahora(), *extra),
        ).fetchall()
    return [dict(f) for f in filas]


def vigentes(ahora: str | None = None) -> list[dict[str, Any]]:
    """Las citas que ocupan agenda (confirmadas y apartados que no vencen), con TODAS sus columnas.

    A diferencia de `confirmadas()` (que devuelve lo mínimo para calcular horarios) trae el teléfono y
    la agenda: es lo que necesita la asignación de «cualquiera». Las canceladas, las expiradas y los
    apartados vencidos no cuentan: ya no ocupan a nadie.
    """
    init()
    with _con() as con:
        filas = con.execute(f"SELECT * FROM citas WHERE {_BLOQUEA}", (ahora or _ahora(),)).fetchall()
    return [dict(f) for f in filas]


def crear(
    slot: str, servicio_id: str, nombre: str, telefono: str = "", *,
    pendiente_hasta: str | None = None, anticipo: int = 0, aceptado_en: str | None = None,
    agenda_id: str = "", agenda_nombre: str = "", ahora: str | None = None,
) -> dict[str, Any]:
    """Crea la cita. Lanza SlotOcupado si el horario ya está tomado *en esa agenda*.

    Con `pendiente_hasta` (ISO) queda apartada, esperando el comprobante del anticipo,
    y el horario se libera solo cuando esa hora pasa.
    """
    init()
    ahora = ahora or _ahora()
    with _candado, _con() as con:
        choque = con.execute(
            f"SELECT folio FROM citas WHERE slot = ? AND {_BLOQUEA}"
            " AND (agenda_id = ? OR agenda_id = '' OR ? = '')",
            (slot, ahora, agenda_id, agenda_id),
        ).fetchone()
        if choque:
            raise SlotOcupado(slot)
        folio = _nuevo_folio(con)
        estado = "pendiente" if pendiente_hasta else "confirmada"
        con.execute(
            "INSERT INTO citas (folio, slot, servicio_id, nombre, telefono, estado, creada, actualizada,"
            " anticipo_monto, vence, aceptado_en, agenda_id, agenda_nombre)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (folio, slot, servicio_id, nombre, telefono, estado, ahora, ahora,
             anticipo, pendiente_hasta, aceptado_en, agenda_id, agenda_nombre),
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
            f"SELECT folio FROM citas WHERE slot = ? AND {_BLOQUEA} AND folio <> ?"
            " AND (agenda_id = ? OR agenda_id = '' OR ? = '')",
            (nuevo_slot, ahora, fila["folio"], fila["agenda_id"], fila["agenda_id"]),
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
            f"SELECT folio FROM citas WHERE slot = ? AND {_BLOQUEA} AND folio <> ?"
            " AND (agenda_id = ? OR agenda_id = '' OR ? = '')",
            (fila["slot"], ahora, fila["folio"], fila["agenda_id"], fila["agenda_id"]),
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


# ---------------------------------------------------------------- agendas (varias personas)
# Nombre que el cliente nunca debe elegir como agenda: es el comodín "cualquiera" de la sesión.
NOMBRE_RESERVADO = "Cualquiera"
# Una agenda que falta dos lecturas seguidas se da de baja; con una sola ausencia no se toca
# (un error pasajero de Google no debe sacar a una persona de la lista).
AUSENCIAS_PARA_BAJA = 2


def _clave_agenda(con: sqlite3.Connection, calendar_id: str) -> str:
    """Clave corta y estable del calendario: lo único que se puede mandar al cliente."""
    for largo in (8, 10, 12, 16):
        clave = hashlib.sha1(calendar_id.encode("utf-8")).hexdigest()[:largo]
        ocupada = con.execute("SELECT 1 FROM agendas WHERE clave = ?", (clave,)).fetchone()
        if not ocupada:
            return clave
    raise RuntimeError("No se pudo generar una clave de agenda única")


def _nombre_agenda(base: str, usados: set[str]) -> str:
    """Nombre tal cual, con sufijo si se repite o si choca con el nombre reservado."""
    candidato = base.strip()
    if not candidato or candidato.lower() == NOMBRE_RESERVADO.lower():
        candidato = f"{NOMBRE_RESERVADO} (2)" if candidato else "Agenda"
    final, n = candidato, 2
    while final.lower() in usados:
        final = f"{candidato} ({n})"
        n += 1
    return final


def agendas(solo_activas: bool = False) -> list[dict[str, Any]]:
    """Agendas dadas de alta, en el orden en que se le presentan a la clienta."""
    init()
    sql = "SELECT * FROM agendas"
    if solo_activas:
        sql += " WHERE activa = 1"
    sql += " ORDER BY orden, calendar_id"
    with _con() as con:
        return [dict(f) for f in con.execute(sql).fetchall()]


def agenda_de(calendar_id: str) -> dict[str, Any] | None:
    """Agenda por su calendar_id (la identidad). Nunca se busca por nombre."""
    init()
    with _con() as con:
        fila = con.execute(
            "SELECT * FROM agendas WHERE calendar_id = ?", ((calendar_id or "").strip(),)
        ).fetchone()
    return _fila(fila)


def agenda_por_clave(clave: str) -> dict[str, Any] | None:
    """Agenda por la clave corta que viaja en la sesión del cliente."""
    init()
    with _con() as con:
        fila = con.execute(
            "SELECT * FROM agendas WHERE clave = ?", ((clave or "").strip().lower(),)
        ).fetchone()
    return _fila(fila)


def citas_huerfanas(ahora: str | None = None) -> list[dict[str, Any]]:
    """Citas que todavía se van a atender pero cuya agenda ya está dada de baja.

    Pasa cuando alguien sale del negocio y su calendario desaparece: la clienta tiene
    su cita, pero nadie la ve en la agenda de quien se queda. Hay que reubicarla a mano.
    Lo pasado no cuenta: ya se atendió (o ya no se atiende).
    """
    init()
    ahora = ahora or _ahora()
    with _con() as con:
        filas = con.execute(
            "SELECT c.folio, c.slot, c.estado, c.agenda_nombre FROM citas c"
            f" WHERE c.slot >= ? AND {_BLOQUEA.replace('vence', 'c.vence')}"
            " AND c.agenda_id <> '' AND NOT EXISTS ("
            "SELECT 1 FROM agendas a WHERE a.clave = c.agenda_id AND a.activa = 1"
            ") ORDER BY c.slot",
            (ahora, ahora),
        ).fetchall()
    return [dict(f) for f in filas]


def agendas_resumen(ahora: str | None = None) -> dict[str, int]:
    """Cuántas agendas hay y cuántas citas quedaron huérfanas. Números, no nombres."""
    init()
    with _con() as con:
        total = con.execute("SELECT COUNT(*) FROM agendas").fetchone()[0]
        activas = con.execute("SELECT COUNT(*) FROM agendas WHERE activa = 1").fetchone()[0]
    return {"activas": int(activas), "total": int(total), "huerfanas": len(citas_huerfanas(ahora))}


def agendas_sincronizar(
    calendarios: list[dict[str, Any]] | None, *, lectura_ok: bool = True, ahora: str | None = None
) -> dict[str, Any]:
    """Alinea la tabla `agendas` con la lista de calendarios que devolvió Google.

    `calendarios` son dicts con `calendar_id`, `nombre` y, si se sabe, `escritura`. Si la lectura
    falló (`lectura_ok=False`) o volvió vacía cuando ya había agendas, NO se da de baja a nadie:
    se marca `degradado` y se deja todo como estaba. Devuelve el reporte con lo que cambió.
    """
    init()
    ahora = ahora or _ahora()
    reporte: dict[str, Any] = {
        "altas": [], "renombres": [], "bajas": [], "reactivadas": [],
        "sin_permiso": [], "degradado": False,
    }
    with _candado, _con() as con:
        previas = {f["calendar_id"]: dict(f) for f in con.execute("SELECT * FROM agendas").fetchall()}
        if not lectura_ok or (calendarios is not None and not calendarios and previas):
            reporte["degradado"] = True
            return reporte
        vistos: set[str] = set()
        usados: set[str] = set()
        for cal in calendarios or []:
            cid = (cal.get("calendar_id") or "").strip()
            if not cid:
                continue
            if cal.get("escritura") is False:
                reporte["sin_permiso"].append({"calendar_id": cid, "nombre": cal.get("nombre") or ""})
                continue
            nombre = _nombre_agenda(cal.get("nombre") or "", usados)
            usados.add(nombre.lower())
            vistos.add(cid)
            previa = previas.get(cid)
            if previa is None:
                orden = con.execute("SELECT COALESCE(MAX(orden), 0) + 1 FROM agendas").fetchone()[0]
                con.execute(
                    "INSERT INTO agendas (calendar_id, clave, nombre, orden, activa, ausencias, alta)"
                    " VALUES (?, ?, ?, ?, 1, 0, ?)",
                    (cid, _clave_agenda(con, cid), nombre, orden, ahora),
                )
                reporte["altas"].append({"calendar_id": cid, "nombre": nombre})
                continue
            if previa["activa"] == 0:
                reporte["reactivadas"].append({"calendar_id": cid, "nombre": nombre})
            elif previa["nombre"] != nombre:
                reporte["renombres"].append({"calendar_id": cid, "nombre": nombre})
            con.execute(
                "UPDATE agendas SET nombre = ?, activa = 1, ausencias = 0 WHERE calendar_id = ?",
                (nombre, cid),
            )
        for cid, previa in previas.items():
            if cid in vistos or previa["activa"] == 0:
                continue
            ausencias = previa["ausencias"] + 1
            activa = 0 if ausencias >= AUSENCIAS_PARA_BAJA else 1
            con.execute(
                "UPDATE agendas SET ausencias = ?, activa = ? WHERE calendar_id = ?",
                (ausencias, activa, cid),
            )
            if activa == 0:
                reporte["bajas"].append({"calendar_id": cid, "nombre": previa["nombre"]})
    return reporte


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
