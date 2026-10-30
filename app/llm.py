"""Capa LLM: solo interpreta texto libre y redacta respuestas ancladas al JSON.

Regla dura: el LLM nunca inventa precios, horarios ni disponibilidad.
Si no hay key o la API falla, se cae a matching local por tags (siempre funciona).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

from . import config


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9\s]", " ", t)


def match_faq_local(texto: str, datos: dict[str, Any]) -> str | None:
    """Matching por tags con desempate por número de coincidencias."""
    palabras = set(_norm(texto).split())
    mejor, mejor_score = None, 0
    for entrada in datos["faq"]:
        score = sum(1 for tag in entrada["tags"] if _norm(tag) in palabras or _norm(tag) in _norm(texto))
        if score > mejor_score:
            mejor, mejor_score = entrada["respuesta"], score
    return mejor


def _contexto(datos: dict[str, Any]) -> str:
    servicios = "\n".join(
        f"- {s['nombre']}: ${s['precio']} ({s['minutos']} min)" for s in datos["servicios"]
    )
    h = datos["horario"]
    horario = (
        f"L-V {h['lunes_viernes']['abre']}-{h['lunes_viernes']['cierra']}, "
        f"Sábado {h['sabado']['abre']}-{h['sabado']['cierra']}, domingo cerrado"
    )
    faq = "\n".join(f"- P: {e['tags'][0]} → R: {e['respuesta']}" for e in datos["faq"])
    return (
        f"Negocio: {datos['nombre']}\nDirección: {datos['direccion']}\n"
        f"Teléfono: {datos['telefono']}\nHorario: {horario}\n"
        f"Pagos: {', '.join(datos['metodos_pago'])}\nServicios:\n{servicios}\n"
        f"Preguntas conocidas:\n{faq}"
    )


async def responder(pregunta: str, datos: dict[str, Any]) -> dict[str, Any]:
    """Devuelve {"texto": str, "fuente": "llm"|"reglas"|"fallback"}."""
    local = match_faq_local(pregunta, datos)

    if not config.DEEPSEEK_API_KEY:
        return {"texto": local or datos["fallback"], "fuente": "reglas" if local else "fallback"}

    sistema = (
        "Eres el asistente de un estudio de uñas en Aguascalientes por WhatsApp. Responde en español mexicano, "
        "máximo 3 líneas, tono cercano y directo, sin emojis de más.\n"
        "Reglas: usa SOLO los datos del contexto. Si falta el dato, di que lo confirmas "
        "con la manicurista; nunca inventes precios, horarios ni disponibilidad. "
        "No prometas descuentos ni servicios que no estén en la lista.\n\n"
        f"CONTEXTO:\n{_contexto(datos)}"
    )
    try:
        async with httpx.AsyncClient(timeout=config.LLM_TIMEOUT_S) as client:
            r = await client.post(
                config.DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"},
                json={
                    "model": config.DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": sistema},
                        {"role": "user", "content": pregunta},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 220,
                },
            )
            r.raise_for_status()
            texto = r.json()["choices"][0]["message"]["content"].strip()
            return {"texto": texto, "fuente": "llm"}
    except Exception:
        return {"texto": local or datos["fallback"], "fuente": "reglas" if local else "fallback"}
