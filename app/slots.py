"""Capa de cálculo de disponibilidad de horarios (slots).

Todo aquí es determinista y sin dependencias del framework: recibe los datos
del negocio (`data/barberia.json` ya cargado como dict), la lista de citas y el
"ahora" explícito. Nada de relojes globales ni de estado mutable.

Reglas de negocio implementadas:
  * La agenda se abre de hoy (incluido) a hoy + `dias_adelante` (incluido).
  * Los días sin horario (domingo) se saltan.
  * Un servicio debe caber COMPLETO antes del cierre.
  * Existe una anticipación mínima antes de poder reservar.
  * Un slot está ocupado si su intervalo [inicio, inicio+duración) se traslapa
    con el de cualquier cita vigente (las canceladas no bloquean).
  * Los slots van cada `slots_minutos` minutos y se devuelven en orden
    cronológico.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# Estados de cita que liberan el horario (el resto bloquea).
ESTADOS_LIBERAN = {"cancelada", "cancelado", "cancelled", "canceled"}


# ---------------------------------------------------------------- utilidades
def _num(valor: float) -> str:
    """1.0 -> '1', 0.5 -> '0.5' (para mensajes legibles)."""
    return f"{valor:g}"


def _hora(txt: Any) -> time | None:
    """'10:00' -> time(10, 0). Devuelve None si el formato no es válido."""
    try:
        return datetime.strptime(str(txt), "%H:%M").time()
    except (TypeError, ValueError):
        return None


def _reglas(datos: dict[str, Any]) -> dict[str, Any]:
    return datos.get("reglas_agenda") or {}


def _paso(datos: dict[str, Any]) -> int:
    """Tamaño del bloque de agenda en minutos (nunca menor a 1)."""
    try:
        return max(1, int(_reglas(datos).get("slots_minutos", 30)))
    except (TypeError, ValueError):
        return 30


def _mapa_servicios(datos: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(s.get("id")): s for s in (datos.get("servicios") or []) if isinstance(s, dict)}


def _duracion(datos: dict[str, Any], servicio_id: str, servicios: dict[str, dict[str, Any]] | None = None) -> int:
    """Duración en minutos del servicio. ValueError si el servicio no existe."""
    catalogo = servicios if servicios is not None else _mapa_servicios(datos)
    sv = catalogo.get(str(servicio_id))
    if sv is None:
        raise ValueError(f"servicio desconocido: {servicio_id!r}")
    try:
        minutos = int(sv.get("minutos") or 0)
    except (TypeError, ValueError):
        minutos = 0
    return minutos if minutos > 0 else _paso(datos)


def _horario_del_dia(datos: dict[str, Any], fecha: date) -> tuple[time, time] | None:
    """Rango (abre, cierra) del día; None si el día está cerrado."""
    horario = datos.get("horario") or {}
    dia = fecha.weekday()
    clave = "lunes_viernes" if dia <= 4 else "sabado" if dia == 5 else "domingo"
    rango = horario.get(clave)
    if not isinstance(rango, dict):
        return None
    abre, cierra = _hora(rango.get("abre")), _hora(rango.get("cierra"))
    if abre is None or cierra is None or cierra <= abre:
        return None
    return abre, cierra


def _estado(cita: dict[str, Any]) -> str:
    return str(cita.get("estado") or "confirmada").strip().lower()


def _intervalos_ocupados(
    datos: dict[str, Any],
    citas: list[dict[str, Any]] | None,
    servicios: dict[str, dict[str, Any]] | None = None,
) -> list[tuple[datetime, datetime]]:
    """Intervalos [inicio, fin) de las citas vigentes. Ignora las canceladas."""
    catalogo = servicios if servicios is not None else _mapa_servicios(datos)
    paso = _paso(datos)
    ocupados: list[tuple[datetime, datetime]] = []
    for cita in citas or []:
        if not isinstance(cita, dict) or _estado(cita) in ESTADOS_LIBERAN:
            continue
        try:
            inicio = datetime.fromisoformat(str(cita.get("slot") or ""))
        except (TypeError, ValueError):
            continue  # cita corrupta: no debe tumbar el cálculo
        if inicio.tzinfo is not None:  # normalizamos a naive (hora local)
            inicio = inicio.replace(tzinfo=None)
        sv = catalogo.get(str(cita.get("servicio_id")))
        try:
            minutos = int(sv.get("minutos") or 0) if sv else 0
        except (TypeError, ValueError):
            minutos = 0
        ocupados.append((inicio, inicio + timedelta(minutes=minutos or paso)))
    return ocupados


def _se_traslapa(a_ini: datetime, a_fin: datetime, b_ini: datetime, b_fin: datetime) -> bool:
    """Intervalos semiabiertos: tocarse en el borde NO es traslape."""
    return a_ini < b_fin and b_ini < a_fin


# ------------------------------------------------------------------ público
def etiqueta(iso: str) -> str:
    """'2026-10-26T11:00' -> 'Lunes 26 oct · 11:00'."""
    d = datetime.fromisoformat(str(iso))
    return f"{DIAS[d.weekday()].capitalize()} {d.day} {MESES[d.month - 1]} · {d:%H:%M}"


def generar_slots(
    datos: dict[str, Any],
    servicio_id: str,
    citas: list[dict[str, Any]],
    ahora: datetime,
    limite: int = 24,
) -> list[str]:
    """Slots ISO donde el servicio COMPLETO cabe libre, en orden cronológico."""
    servicios = _mapa_servicios(datos)
    duracion = timedelta(minutes=_duracion(datos, servicio_id, servicios))
    reglas = _reglas(datos)
    paso = timedelta(minutes=_paso(datos))

    try:
        anticipacion = timedelta(hours=float(reglas.get("anticipacion_minima_horas", 0) or 0))
    except (TypeError, ValueError):
        anticipacion = timedelta(0)
    try:
        dias = int(reglas.get("dias_adelante", 7))
    except (TypeError, ValueError):
        dias = 7

    if limite is not None and limite <= 0:
        return []

    minimo = ahora + anticipacion
    ocupados = _intervalos_ocupados(datos, citas, servicios)
    hoy = ahora.date()
    slots: list[str] = []

    for dia in range(dias + 1):
        fecha = hoy + timedelta(days=dia)
        rango = _horario_del_dia(datos, fecha)
        if rango is None:
            continue
        abre, cierra = datetime.combine(fecha, rango[0]), datetime.combine(fecha, rango[1])
        inicio = abre
        # mientras el servicio completo quepa antes del cierre
        while inicio + duracion <= cierra:
            fin = inicio + duracion
            if inicio >= minimo and not any(_se_traslapa(inicio, fin, o_ini, o_fin) for o_ini, o_fin in ocupados):
                slots.append(inicio.isoformat(timespec="minutes"))
                if limite is not None and len(slots) >= limite:
                    return slots
            inicio += paso

    return slots


def validar(
    datos: dict[str, Any],
    servicio_id: str,
    slot_iso: str,
    citas: list[dict[str, Any]],
    ahora: datetime,
) -> tuple[bool, str]:
    """(True, '') si el slot se puede reservar; si no, el motivo en español."""
    servicios = _mapa_servicios(datos)
    duracion = timedelta(minutes=_duracion(datos, servicio_id, servicios))
    reglas = _reglas(datos)
    paso_min = _paso(datos)

    # 1) formato
    try:
        inicio = datetime.fromisoformat(str(slot_iso))
    except (TypeError, ValueError):
        return False, "el horario no tiene un formato válido (usa AAAA-MM-DDTHH:MM)"
    if inicio.tzinfo is not None:
        inicio = inicio.replace(tzinfo=None)
    fin = inicio + duracion

    # 2) alineado a bloques, sin segundos
    if inicio.second or inicio.microsecond or (inicio.hour * 60 + inicio.minute) % paso_min:
        return False, f"el horario debe ir en bloques de {paso_min} minutos"

    # 3) dentro de la ventana de agenda
    hoy = ahora.date()
    try:
        dias = int(reglas.get("dias_adelante", 7))
    except (TypeError, ValueError):
        dias = 7
    fecha = inicio.date()
    if fecha < hoy:
        return False, "esa fecha ya pasó"
    if fecha > hoy + timedelta(days=dias):
        return False, f"más de {dias} días adelante"

    # 4) el día abre
    rango = _horario_del_dia(datos, fecha)
    if rango is None:
        return False, "el día está cerrado"
    abre, cierra = datetime.combine(fecha, rango[0]), datetime.combine(fecha, rango[1])
    if inicio < abre or inicio >= cierra:
        return False, "fuera del horario de atención"

    # 5) el servicio completo cabe antes del cierre
    if fin > cierra:
        return False, "no cabe completo antes del cierre"

    # 6) anticipación mínima
    try:
        horas = float(reglas.get("anticipacion_minima_horas", 0) or 0)
    except (TypeError, ValueError):
        horas = 0.0
    if inicio < ahora + timedelta(hours=horas):
        return False, f"faltan menos de {_num(horas)} h de anticipación"

    # 7) choque con otra cita vigente
    for o_ini, o_fin in _intervalos_ocupados(datos, citas, servicios):
        if _se_traslapa(inicio, fin, o_ini, o_fin):
            return False, "ese horario ya está ocupado"

    return True, ""
