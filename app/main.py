"""FastAPI: chat de demo con estética WhatsApp."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (acceso, anticipo, avisos, calendar_sync, concentrado, config, engine, reloj,
               seed, store, whatsapp_webhook)

log = logging.getLogger(__name__)

# Falla cerrado: sin código el chat web quedaría abierto a cualquiera y con él el tope
# diario de mensajes se agota solo. Para abrirlo a propósito (solo local): ALLOW_OPEN_DEMO=1.
if not config.DEMO_CODE and not config.ALLOW_OPEN_DEMO:
    raise RuntimeError(
        "DEMO_CODE no está definido. Defínelo (en Render: variable de entorno en el panel) "
        "o usa ALLOW_OPEN_DEMO=1 solo en desarrollo local."
    )
if config.SESSION_SECRET_EFIMERA:
    log.warning("SESSION_SECRET no está definido: se generó uno temporal y las sesiones "
                "abiertas se invalidan en cada reinicio.")

# ---------- concentrado de depósitos: quién lo dispara ----------
# Render no tiene cron en este plan y `resumen_hora` del JSON no programa nada por sí solo:
# lo dispara este bucle, dentro del mismo proceso. Cada minuto pregunta si ya es la hora y si
# hoy no se ha mandado (la respuesta vive en `resumen_envios`, así el reinicio del plan free
# no lo repite ni lo pierde).
async def _bucle_concentrado() -> None:
    while True:
        try:
            await asyncio.to_thread(_concentrado_si_toca)
        except asyncio.CancelledError:
            raise
        except Exception:                       # un ciclo roto no puede matar el bucle
            log.exception("concentrado: falló el ciclo")
        await asyncio.sleep(config.RESUMEN_CICLO_S)


def _concentrado_si_toca() -> None:
    ahora = reloj.ahora()
    dia = ahora.date().isoformat()
    if not concentrado.toca_enviar(anticipo.cfg(DATOS), ahora, store.resumen_hecho(dia)):
        return
    if not avisos.concentrado_configurado():
        log.warning("concentrado: toca mandarlo y Telegram no está configurado")
        return
    # Desde el último corte mandado (o desde hoy a las 00:00 si es el primero): así un depósito que
    # llegó después de la hora de ayer sale hoy, y un día sin resumen se recupera en el siguiente.
    # El corte es el instante en que se ARMA el reporte, no en que termina de salir.
    corte = ahora.isoformat(timespec="seconds")
    r = concentrado.reporte_ventana(DATOS, store.listar(), despues=store.ultimo_corte() or f"{dia}T00:00:00",
                                    hasta=corte)
    if avisos.notificar_concentrado_partes(r["partes"], r["n"]):
        store.resumen_marcar(dia, r["n"], corte=corte)       # solo si de verdad salió
    else:
        log.error("concentrado: no salió; se reintenta en el próximo ciclo")


@asynccontextmanager
async def _vida(_: FastAPI):
    tarea = asyncio.create_task(_bucle_concentrado()) if config.RESUMEN_AUTO else None
    try:
        yield
    finally:
        if tarea is not None:
            tarea.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarea


app = FastAPI(title="Ambar — bot demo", lifespan=_vida)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.include_router(whatsapp_webhook.router)

DATOS = engine.cargar_datos()
# Falla cerrado: con el anticipo activo, un dato de pago faltante o mal escrito (una CLABE con un
# dígito de más o de menos) mandaría el dinero de las clientas a ninguna parte.
if anticipo.activo(DATOS) and (_problemas := anticipo.problemas_de_pago()):
    raise RuntimeError("El anticipo está activo pero los datos de pago no están bien: " + "; ".join(_problemas))
store.init()
if config.SEED_DEMO:
    # El plan free de Render reinicia el disco: sin esto la agenda abre vacía.
    seed.sembrar_si_vacio(DATOS)


class Entrada(BaseModel):
    sid: str = Field(max_length=64)
    texto: str = Field(max_length=500)
    codigo: str = Field(default="", max_length=100)


class NuevaSesion(BaseModel):
    codigo: str = ""


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/privacidad")
def privacidad():
    """Aviso de privacidad LFPDPPP. Meta exige una URL pública para publicar la app."""
    return FileResponse(config.STATIC_DIR / "privacidad.html")


@app.get("/terminos")
def terminos():
    """Condiciones de uso del asistente y del agendado de citas."""
    return FileResponse(config.STATIC_DIR / "terminos.html")


@app.get("/api/negocio")
def negocio():
    return {
        "nombre": DATOS["nombre"],
        "slogan": DATOS["slogan"],
        "direccion": DATOS["direccion"],
        "telefono": DATOS["telefono"],
        "codigo_requerido": bool(config.DEMO_CODE),
    }


def _tope(request: Request, clave: str, maximo: int) -> None:
    """429 si esta IP pasó el tope por minuto. Es best-effort: X-Forwarded-For se puede falsear."""
    ip = acceso.ip_cliente(
        request.headers.get("x-forwarded-for", ""),
        request.client.host if request.client else "",
        config.IP_DESDE_DERECHA,
    )
    if not acceso.permitir(clave, ip, maximo):
        raise HTTPException(
            status_code=429,
            detail="Demasiadas solicitudes. Espera un minuto.",
            headers={"Retry-After": "60"},
        )


def _codigo_valido(codigo: str) -> bool:
    return not config.DEMO_CODE or acceso.codigo_ok(codigo)


# `async def` a propósito: así toca SESIONES en el hilo del event loop, el mismo que
# recorre el diccionario en engine.manejar, y no en el pool de hilos.
@app.post("/api/sesion")
async def nueva_sesion(request: Request, body: NuevaSesion):
    _tope(request, "sesion", config.RATE_SESION_POR_MIN)
    if not _codigo_valido(body.codigo):
        raise HTTPException(status_code=401, detail="Código de demo inválido.")
    # Memoria acotada: purga las vencidas y desaloja las más viejas del chat web.
    engine._purgar_sesiones()
    engine.recortar_sesiones(config.MAX_SESIONES_WEB - 1)
    sid = acceso.nuevo_sid()
    engine.SESIONES[sid] = engine.Sesion(sid)
    return {"sid": sid, "respuestas": engine.bienvenida(DATOS)}


@app.post("/api/mensaje")
async def mensaje(request: Request, body: Entrada):
    _tope(request, "mensaje", config.RATE_MENSAJE_POR_MIN)
    if not _codigo_valido(body.codigo):
        raise HTTPException(status_code=401, detail="Código de demo inválido.")
    # El chat web no puede entrar a la sesión de un cliente de WhatsApp.
    if body.sid.startswith("wa:"):
        raise HTTPException(status_code=400, detail="sid inválido.")
    if not body.texto.strip():
        raise HTTPException(status_code=422, detail="Mensaje vacío.")
    # Solo valen los sid que emitió /api/sesion: inventar uno por mensaje esquivaba el
    # tope por sesión, agotaba el tope diario y llenaba la memoria de sesiones.
    if not acceso.sid_valido(body.sid):
        raise HTTPException(status_code=401, detail="Sesión inválida o vencida. Recarga la página.")
    respuestas, sesion = await engine.manejar(body.sid, body.texto.strip(), DATOS)
    return {
        "respuestas": respuestas,
        "estado": {
            "paso": sesion.paso,
            "resuelta": sesion.resuelta,
            "handoff": sesion.handoff,
            "mensajes": sesion.mensajes,
        },
    }


_SIN_DATOS_PERSONALES = ("telefono", "calendar_event_id", "comprobante_media_id")


def _exige_admin(x_admin_token: str | None) -> None:
    """503 si la ruta no está configurada; 401 si el token falta o no coincide."""
    if not config.ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN no configurado")
    if not x_admin_token or not hmac.compare_digest(
        x_admin_token.encode(), config.ADMIN_TOKEN.encode()
    ):
        raise HTTPException(status_code=401, detail="no autorizado")


@app.get("/api/agendas")
def agendas(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    """Quién tiene agenda y cuántas citas trae cada una. Sin `calendar_id` a propósito.

    La clave corta es la que viaja en la sesión de la clienta; el `calendar_id` es la
    identidad de Google y no sale del servidor. `huerfanas` son las citas futuras de una
    agenda dada de baja: hay clientas esperando a alguien que ya no está.
    """
    _exige_admin(x_admin_token)
    futuras: dict[str, int] = {}
    for c in store.listar():
        if c.get("slot", "") >= store._ahora() and c.get("estado") in ("confirmada", "pendiente"):
            futuras[c.get("agenda_id", "")] = futuras.get(c.get("agenda_id", ""), 0) + 1
    return {
        "agendas": [
            {
                "clave": a["clave"],
                "nombre": a["nombre"],
                "activa": bool(a["activa"]),
                "citas_futuras": futuras.get(a["clave"], 0),
            }
            for a in store.agendas()
        ],
        "huerfanas": store.citas_huerfanas(),
    }


@app.get("/api/citas")
def citas(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    _exige_admin(x_admin_token)
    return {
        "citas": [
            {k: v for k, v in c.items() if k not in _SIN_DATOS_PERSONALES}
            for c in store.listar()
        ]
    }


@app.get("/api/depositos")
def depositos(desde: str | None = None, hasta: str | None = None, formato: str = "json",
              x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    """El concentrado de depósitos: qué entró, de quién y cuánto. Para cuadrar a mano.

    `/api/citas` filtra teléfono y monto a propósito; aquí NO: sin teléfono no se reconoce el
    depósito en el banco y sin monto no se cuadra. Por eso lleva el mismo candado (ADMIN_TOKEN).
    """
    _exige_admin(x_admin_token)
    try:
        r = concentrado.reporte(DATOS, store.listar(), desde=desde, hasta=hasta)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if formato == "texto":
        return PlainTextResponse(r["texto"])
    return r


@app.post("/api/depositos/enviar")
def depositos_enviar(desde: str | None = None, hasta: str | None = None,
                     x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    """Manda el concentrado por Telegram ahora mismo, sin esperar la hora.

    Disparo manual: no marca el día como enviado, así el resumen de la noche sale igual.
    """
    _exige_admin(x_admin_token)
    if not avisos.concentrado_configurado():
        raise HTTPException(status_code=503,
                            detail="Telegram no configurado: define TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID.")
    try:
        r = concentrado.reporte(DATOS, store.listar(), desde=desde, hasta=hasta)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    enviado = avisos.notificar_concentrado(r["texto"], r["n"])
    return {"enviado": enviado, "n": r["n"], "desde": r["desde"], "hasta": r["hasta"],
            "total_anticipo": r["total_anticipo"], "total_resta": r["total_resta"]}


@app.get("/health")
def health():
    # `commit` deja ver qué versión está sirviendo Render (lo inyecta la plataforma).
    # `wa` son contadores del canal: dicen si Meta llama y si los envíos salen, sin
    # depender del log de Render (que se pierde en cada reinicio del plan gratuito).
    return {
        "ok": True,
        "llm": bool(config.DEEPSEEK_API_KEY),
        "commit": os.getenv("RENDER_GIT_COMMIT", "local")[:7],
        "wa": whatsapp_webhook.salud(),
        "calendar": calendar_sync.salud(),
        "agendas": {**calendar_sync.salud_agendas(), **store.agendas_resumen()},
    }
