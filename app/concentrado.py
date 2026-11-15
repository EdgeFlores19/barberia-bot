"""Concentrado de depósitos: el cuadre a mano que hace la dueña.

No hay sistema de confirmación de depósitos y no se quiere. El aviso instantáneo
(`app/avisos.py`) le dice "llegó un comprobante de esta clienta"; este concentrado le da la
lista con teléfono y monto para que la cuadre contra su estado de cuenta, y el resumen que
sale todos los días a la hora configurada.

La ventana de fechas es la FECHA DEL DEPÓSITO (`comprobante_en`), no la de la cita: el
reporte es el corte del día contra el banco. La fecha de la cita va en cada línea, que es lo
que le dice a qué servicio corresponde el dinero.

Sin I/O: se arma con los datos del negocio y las filas que le pasen, y se prueba solo.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from . import anticipo, reloj
from .slots import DIAS, MESES
from .slots import etiqueta as _etiqueta_slot

# sendMessage de Telegram corta en 4096 caracteres. Margen para el pie.
LIMITE_TELEGRAM = 3800

# Estados cuya fila tiene dinero adentro. `cancelada` entra a propósito: el depósito existe y
# la dueña tiene que decidir la devolución según su política.
CON_DEPOSITO = ("confirmada", "cancelada")

_HORA_OK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ---------- fechas ----------
def _dia(txt: str | None, nombre: str, defecto: date) -> date:
    if txt is None or not str(txt).strip():
        return defecto
    try:
        return date.fromisoformat(str(txt).strip()[:10])
    except ValueError:
        raise ValueError(f"{nombre}: '{txt}' no es una fecha AAAA-MM-DD") from None


def rango(desde: str | None = None, hasta: str | None = None, hoy: date | None = None) -> tuple[date, date]:
    """(desde, hasta) como fechas. Sin `hasta`, el mismo día. `hasta` nunca antes de `desde`."""
    hoy = hoy or reloj.ahora().date()
    primero = _dia(desde, "desde", hoy)
    ultimo = _dia(hasta, "hasta", primero)
    if ultimo < primero:
        raise ValueError("hasta es anterior a desde")
    return primero, ultimo


def _fecha_de(txt: Any) -> date | None:
    try:
        return date.fromisoformat(str(txt)[:10])
    except (TypeError, ValueError):
        return None


# ---------- filas ----------
def _servicio(datos: dict[str, Any], servicio_id: Any) -> dict[str, Any]:
    """Nombre y precio del servicio. Una cita de un servicio que ya no está: nombre = id, precio 0."""
    for s in datos.get("servicios") or []:
        if str(s.get("id")) == str(servicio_id):
            return {"nombre": s.get("nombre") or str(servicio_id), "precio": int(s.get("precio") or 0)}
    return {"nombre": str(servicio_id), "precio": 0}


def filas(datos: dict[str, Any], citas: list[dict[str, Any]],
          desde: date, hasta: date) -> list[dict[str, Any]]:
    """Una fila por depósito dentro de la ventana, en orden de cita."""
    c = anticipo.cfg(datos)
    descuenta = bool(c["descuenta_del_total"])
    salida: list[dict[str, Any]] = []
    for cita in citas:
        if cita.get("estado") not in CON_DEPOSITO:
            continue
        depositado = _fecha_de(cita.get("comprobante_en"))
        if depositado is None or not (desde <= depositado <= hasta):
            continue
        monto = int(cita.get("anticipo_monto") or 0)
        if monto <= 0:
            continue
        sv = _servicio(datos, cita.get("servicio_id"))
        slot = str(cita.get("slot") or "")
        salida.append({
            "folio": cita.get("folio"),
            "nombre": cita.get("nombre") or "",
            "telefono": cita.get("telefono") or "",
            "servicio": sv["nombre"],
            "slot": slot,
            "etiqueta": _etiqueta_slot(slot) if slot else "",
            "precio": sv["precio"],
            "anticipo": monto,
            "resta": max(sv["precio"] - monto, 0) if descuenta else sv["precio"],
            "depositado": str(cita.get("comprobante_en")),
            "estado": cita.get("estado"),
            "anulada": cita.get("estado") == "cancelada",
        })
    return sorted(salida, key=lambda f: (f["slot"], str(f["folio"])))


# ---------- reporte ----------
def reporte(datos: dict[str, Any], citas: list[dict[str, Any]], desde: str | None = None,
            hasta: str | None = None, hoy: date | None = None) -> dict[str, Any]:
    """El concentrado completo: filas, totales y el texto listo para mandar."""
    primero, ultimo = rango(desde, hasta, hoy)
    fs = filas(datos, citas, primero, ultimo)
    r: dict[str, Any] = {
        "desde": primero.isoformat(),
        "hasta": ultimo.isoformat(),
        "n": len(fs),
        "total_anticipo": sum(f["anticipo"] for f in fs),
        "total_resta": sum(f["resta"] for f in fs),
        "filas": fs,
    }
    r["texto"] = texto(r)
    return r


def _fecha_corta(d: date) -> str:
    return f"{DIAS[d.weekday()][:3]} {d.day} {MESES[d.month - 1]}"


def _cabecera(r: dict[str, Any]) -> str:
    primero, ultimo = date.fromisoformat(r["desde"]), date.fromisoformat(r["hasta"])
    cuando = _fecha_corta(primero) if primero == ultimo else f"{_fecha_corta(primero)} a {_fecha_corta(ultimo)}"
    if not r["n"]:
        return f"🧾 Concentrado de depósitos · {cuando}\nSin depósitos registrados en esa fecha."
    cuanto = f"{r['n']} depósito" if r["n"] == 1 else f"{r['n']} depósitos"
    return (f"🧾 Concentrado de depósitos · {cuando}\n"
            f"{cuanto} · ${r['total_anticipo']:,} en anticipos · ${r['total_resta']:,} por cobrar al llegar\n"
            "Cuádralo contra tu banco.")


def _linea(f: dict[str, Any]) -> str:
    """Un depósito: cuándo es la CITA (no cuándo cayó el dinero), quién y cuánto entró."""
    cuando = ""
    if len(f["slot"]) >= 16:
        cuando = f"{_fecha_corta(date.fromisoformat(f['slot'][:10]))} {f['slot'][11:16]}"
    cabeza = f"• {cuando} · {f['servicio']}".replace("•  · ", "• ")
    if f["anulada"]:
        cabeza += " (cancelada: revisa la devolución)"
    deposito = _fecha_corta(date.fromisoformat(f["depositado"][:10]))
    tel = _con_mas(f["telefono"])
    return (f"{cabeza}\n"
            f"  {f['nombre']} · Folio {f['folio']}\n"
            f"  Anticipo ${f['anticipo']:,} · resta ${f['resta']:,}\n"
            f"  Tel {tel} · depositado {deposito}")


def _con_mas(telefono: str) -> str:
    """El número como se marca, venga `521449…` (WhatsApp) o `+52…`. Sin él, se dice."""
    limpio = (telefono or "").strip()
    if not limpio:
        return "(sin teléfono: pídelo por el chat web)"
    return limpio if limpio.startswith("+") else f"+{limpio}"


def texto(r: dict[str, Any]) -> str:
    """El mensaje tal cual se manda. Si no cabe en Telegram, corta y dice cuántos faltan."""
    partes = [_cabecera(r)]
    usados = len(partes[0])
    puestas = 0
    for f in r["filas"]:
        linea = _linea(f)
        if usados + len(linea) + 2 > LIMITE_TELEGRAM:
            break
        partes.append(linea)
        usados += len(linea) + 2
        puestas += 1
    if puestas < r["n"]:
        faltan = r["n"] - puestas
        partes.append(f"… y {faltan} depósito{'s' if faltan != 1 else ''} más (pide el reporte completo).")
    return "\n\n".join(partes)


# ---------- quién lo dispara ----------
def hhmm(momento: datetime) -> str:
    return momento.strftime("%H:%M")


def toca_enviar(c: dict[str, Any], ahora: datetime, ya_enviado: bool) -> bool:
    """¿Hay que mandar el concentrado en este ciclo?

    Sí cuando ya pasó `resumen_hora` y hoy no se ha mandado: si el proceso estaba abajo a las
    20:00, sale al volver. Una hora mal escrita (o vacía) no manda nada: `resumen_hora` sin
    valor válido es config muerta, no una invitación a mandar a media noche.
    """
    hora = str(c.get("resumen_hora") or "").strip()
    if not _HORA_OK.match(hora) or ya_enviado:
        return False
    return hhmm(ahora) >= hora
