"""Anticipo al agendar: configuración, políticas y textos. Sin I/O: se prueba solo.

El bloque "anticipo" del JSON del negocio manda; lo que no traiga toma los valores
acordados (ver DEFECTOS). Los textos que ve la clienta salen de esa misma configuración,
así el aviso previo al pago dice siempre lo que el sistema de verdad hará.

Datos de pago (banco, CLABE, titular): variables de entorno, nunca en el repo.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from . import config

DEFECTOS: dict[str, Any] = {
    "activo": False,
    "monto": 200,
    "descuenta_del_total": True,
    "apartado_minutos": 60,
    "apartado_minutos_mismo_dia": 30,
    "alerta_inmediata_horas": 24,
    # False = todo comprobante se avisa en el momento (hasta que exista el resumen diario).
    # True = solo lo urgente (cita en menos de `alerta_inmediata_horas`); el resto, al resumen.
    "resumen_diario": False,
    "cancelar": {"reembolso": "nunca", "horas": 0},        # "nunca" | "hasta_horas"
    "reagendar": {"permitido": True, "max_veces": 1, "horas_minimas": 24, "conserva_anticipo": True},
    "resumen_hora": "20:00",
}


def _mezclar(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _mezclar(base[k], v)
        else:
            base[k] = v
    return base


def cfg(datos: dict[str, Any]) -> dict[str, Any]:
    """Configuración efectiva: defectos + lo que ponga el negocio."""
    return _mezclar(copy.deepcopy(DEFECTOS), datos.get("anticipo") or {})


def activo(datos: dict[str, Any]) -> bool:
    if config.ANTICIPO_ACTIVO is not None:
        return config.ANTICIPO_ACTIVO
    return bool((datos.get("anticipo") or {}).get("activo", False))


def monto(c: dict[str, Any]) -> int:
    return int(c["monto"])


def restante(precio: int | float, c: dict[str, Any]) -> int:
    """Lo que falta pagar al llegar. Nunca negativo."""
    precio = int(precio)
    return max(precio - monto(c), 0) if c["descuenta_del_total"] else precio


# ---------- datos de pago ----------
def clabe_valida(clabe: str) -> bool:
    """18 dígitos con el dígito de control de la CLABE (pesos 3, 7, 1)."""
    if len(clabe) != 18 or not clabe.isdigit():
        return False
    suma = sum((int(d) * p) % 10 for d, p in zip(clabe[:17], [3, 7, 1] * 6))
    return int(clabe[17]) == (10 - suma % 10) % 10


def problemas_de_pago() -> list[str]:
    """Qué falta o está mal en los datos de pago (vacío = todo bien)."""
    problemas = []
    if not config.ANTICIPO_BANCO:
        problemas.append("falta ANTICIPO_BANCO")
    if not config.ANTICIPO_TITULAR:
        problemas.append("falta ANTICIPO_TITULAR")
    if not clabe_valida(config.ANTICIPO_CLABE):
        problemas.append("ANTICIPO_CLABE falta o no es una CLABE válida (18 dígitos con dígito de control)")
    return problemas


# ---------- tiempos ----------
def horas_para(slot_iso: str, ahora: datetime) -> float:
    return (datetime.fromisoformat(slot_iso) - ahora).total_seconds() / 3600


def apartado_minutos(c: dict[str, Any], slot_iso: str, ahora: datetime) -> int:
    hoy = datetime.fromisoformat(slot_iso).date() == ahora.date()
    return int(c["apartado_minutos_mismo_dia"] if hoy else c["apartado_minutos"])


def es_urgente(c: dict[str, Any], slot_iso: str, ahora: datetime) -> bool:
    """¿La cita es tan pronto que el dueño debe enterarse ya y no en el resumen?"""
    return horas_para(slot_iso, ahora) < float(c["alerta_inmediata_horas"])


# ---------- políticas ----------
def cancelacion_devuelve(c: dict[str, Any], horas_restantes: float) -> bool:
    cancelar = c["cancelar"]
    return cancelar["reembolso"] == "hasta_horas" and horas_restantes >= float(cancelar["horas"])


def reagendar_permitido(c: dict[str, Any], veces_ya: int, horas_restantes: float) -> tuple[bool, str]:
    """(True, "") o (False, motivo): no_permitido | sin_conservar | max_veces | poco_tiempo."""
    r = c["reagendar"]
    if not r["permitido"]:
        return False, "no_permitido"
    if not r["conserva_anticipo"]:
        return False, "sin_conservar"
    if veces_ya >= int(r["max_veces"]):
        return False, "max_veces"
    if horas_restantes < float(r["horas_minimas"]):
        return False, "poco_tiempo"
    return True, ""


# ---------- textos para la clienta ----------
def _pesos(n: int | float) -> str:
    return f"${int(n):,}"


def _horas(n: float) -> str:
    return f"{int(n)} h" if float(n).is_integer() else f"{n:g} h"


def _veces(n: int) -> str:
    return "1 vez" if int(n) == 1 else f"{int(n)} veces"


def _politica(c: dict[str, Any]) -> str:
    cancelar, r = c["cancelar"], c["reagendar"]
    if cancelar["reembolso"] == "hasta_horas":
        partes = [f"si cancelas con {_horas(cancelar['horas'])} o más de anticipación se te devuelve; "
                  "si cancelas después o no te presentas, no se devuelve"]
    else:
        partes = ["si cancelas o no te presentas, el anticipo no se devuelve"]
    if r["permitido"] and r["conserva_anticipo"]:
        partes.append(f"puedes mover tu cita {_veces(r['max_veces'])} con {_horas(r['horas_minimas'])} "
                      "o más de anticipación sin perder el anticipo")
    else:
        partes.append("la cita no se puede mover (para cambiarla hay que cancelarla)")
    return "; ".join(partes)


def texto_aviso(c: dict[str, Any], precio: int | float) -> str:
    """Lo que se le dice a la clienta ANTES de mostrarle la cuenta (y se guarda su aceptación)."""
    cuenta = (f" Se descuenta del total de tu servicio (te resta pagar {_pesos(restante(precio, c))})."
              if c["descuenta_del_total"] else " No forma parte del precio de tu servicio.")
    return (f"Para apartar tu cita pedimos un anticipo de {_pesos(monto(c))}.{cuenta}\n"
            f"Importante: {_politica(c)}.\n¿Estás de acuerdo?")


def texto_pago(c: dict[str, Any], folio: str, minutos: int, banco: str, clabe: str, titular: str) -> str:
    return (f"Transfiere {_pesos(monto(c))} a:\n"
            f"• Banco: {banco}\n• CLABE: {clabe}\n• Titular: {titular}\n"
            f"• Concepto: tu nombre y {folio}\n\n"
            f"Tienes {minutos} min para enviarme aquí el comprobante (foto, captura o PDF). "
            "Pasado ese tiempo el horario se libera.")


def texto_comprobante_ok(c: dict[str, Any], servicio: str, etiqueta: str, folio: str, precio: int | float) -> str:
    resta = (f" Resta por pagar: {_pesos(restante(precio, c))}." if c["descuenta_del_total"] else "")
    return (f"Recibí tu comprobante ✅ Tu cita queda agendada: {servicio}, {etiqueta}. Folio {folio}.\n"
            f"Anticipo: {_pesos(monto(c))}.{resta}\n"
            "La manicurista verificará tu pago; si algo no cuadra, te escribirá.")
