"""FastAPI: chat de demo con estética WhatsApp."""
from __future__ import annotations

import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, engine, seed, store

app = FastAPI(title="Barbería El Navajazo — bot demo")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

DATOS = engine.cargar_datos()
store.init()
if config.SEED_DEMO:
    # El plan free de Render reinicia el disco: sin esto la agenda abre vacía.
    seed.sembrar_si_vacio(DATOS)


class Entrada(BaseModel):
    sid: str
    texto: str
    codigo: str = ""


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


@app.post("/api/sesion")
def nueva_sesion(body: NuevaSesion):
    if config.DEMO_CODE and body.codigo.strip() != config.DEMO_CODE:
        raise HTTPException(status_code=401, detail="Código de demo inválido.")
    sid = uuid.uuid4().hex[:12]
    engine.SESIONES[sid] = engine.Sesion(sid)
    return {"sid": sid, "respuestas": engine.bienvenida(DATOS)}


@app.post("/api/mensaje")
async def mensaje(body: Entrada):
    if config.DEMO_CODE and body.codigo.strip() != config.DEMO_CODE:
        raise HTTPException(status_code=401, detail="Código de demo inválido.")
    if not body.texto.strip():
        raise HTTPException(status_code=422, detail="Mensaje vacío.")
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


@app.get("/api/citas")
def citas():
    return {"citas": store.listar()}


@app.get("/health")
def health():
    # `commit` deja ver qué versión está sirviendo Render (lo inyecta la plataforma).
    return {
        "ok": True,
        "llm": bool(config.DEEPSEEK_API_KEY),
        "commit": os.getenv("RENDER_GIT_COMMIT", "local")[:7],
    }
