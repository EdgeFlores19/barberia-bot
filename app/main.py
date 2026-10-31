"""FastAPI: chat de demo con estética WhatsApp."""
from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import acceso, config, engine, seed, store, whatsapp_webhook

app = FastAPI(title="Estudio de Uñas Ámbar — bot demo")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.include_router(whatsapp_webhook.router)

DATOS = engine.cargar_datos()
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


_SIN_DATOS_PERSONALES = ("telefono", "calendar_event_id")


def _exige_admin(x_admin_token: str | None) -> None:
    """503 si la ruta no está configurada; 401 si el token falta o no coincide."""
    if not config.ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN no configurado")
    if not x_admin_token or not hmac.compare_digest(
        x_admin_token.encode(), config.ADMIN_TOKEN.encode()
    ):
        raise HTTPException(status_code=401, detail="no autorizado")


@app.get("/api/citas")
def citas(x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")):
    _exige_admin(x_admin_token)
    return {
        "citas": [
            {k: v for k, v in c.items() if k not in _SIN_DATOS_PERSONALES}
            for c in store.listar()
        ]
    }


@app.get("/health")
def health():
    # `commit` deja ver qué versión está sirviendo Render (lo inyecta la plataforma).
    return {
        "ok": True,
        "llm": bool(config.DEEPSEEK_API_KEY),
        "commit": os.getenv("RENDER_GIT_COMMIT", "local")[:7],
    }
