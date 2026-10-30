# AGENTS.md — barberia-bot

Instrucciones para agentes de código (Hermes, Claude Code, opencode…). Léelo completo antes de tocar nada.

## Qué es este proyecto

Bot de citas por chat (demo web + canal de WhatsApp en construcción) para negocios de servicios con cita: barberías, estudios de uñas, salones. FastAPI + SQLite + DeepSeek.

**Principio de diseño (no lo rompas):** el motor (`app/engine.py`) es determinista; el LLM (`app/llm.py`) solo redacta respuestas de FAQ ancladas al JSON del negocio. El LLM nunca inventa precios, horarios ni disponibilidad.

## Mapa del código

| Archivo | Qué es |
|---|---|
| `app/main.py` | FastAPI: chat web (`/api/sesion`, `/api/mensaje`), `/api/negocio`, `/api/citas` (protegida con `ADMIN_TOKEN`), `/health` |
| `app/engine.py` | Máquina de estados del bot (`async def manejar(...)`) |
| `app/slots.py` | Disponibilidad de horarios. Puro y determinista: recibe `ahora` por parámetro |
| `app/store.py` | SQLite (fuente de verdad de las citas) |
| `app/calendar_sync.py` | Espejo opcional en Google Calendar (httpx síncrono: llamarlo con `asyncio.to_thread`) |
| `app/llm.py` | FAQ con DeepSeek + fallback local por tags |
| `app/seed.py` | Citas de ejemplo para la demo |
| `app/config.py` | Toda la configuración sale de variables de entorno |
| `data/barberia.json` | Datos del negocio (servicios, horarios, FAQ, reglas de agenda) |
| `tests/` | pytest (+ `smoke_test.py`, que no corre en pytest) |

## Comandos

```bash
# Tests (Windows, git-bash, desde la raíz del repo)
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -3

# Contar tests sin correrlos
.venv/Scripts/python.exe -m pytest tests/ -q --collect-only --ignore=tests/smoke_test.py | tail -1
```

## Convenciones

- Rama por tarea grande (`feat/…`, `fix/…`). **Un commit por tarea**, en español, Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- **TDD:** escribe el test, córrelo y **ve el fallo**, implementa lo mínimo, ve el verde, commit.
- Comentarios y mensajes al usuario en español. Type hints y `from __future__ import annotations` en cada archivo nuevo.
- No hay `pytest-asyncio`: los tests async usan `asyncio.run(...)`.
- No agregues dependencias a `requirements.txt` salvo que el plan vigente lo autorice por escrito.
- Después de cada tarea, la suite completa debe seguir en verde.

## Reglas de seguridad y datos (innegociables)

1. **Nunca edites `.env`** ni imprimas su contenido. Variables nuevas van en `.env.example`, vacías.
2. **Ningún teléfono real ni token en el repo, en tests ni en docs.** Usa números de prueba ficticios (`tests/telefonos.py`).
3. **Ningún log con el teléfono completo:** usa los últimos 4 dígitos (`app.telefono.t4`).
4. **`/api/citas` no puede volver a ser público** ni devolver `telefono` o `calendar_event_id`.
5. No uses `datetime.now()` para la hora del negocio: usa `app.reloj.ahora()`.
6. No llames código síncrono lento (red, `calendar_sync`, Telegram) directo dentro de `async def`: usa `asyncio.to_thread`.
7. No toques `app/slots.py` ni `app/calendar_sync.py` salvo que la tarea lo pida.
8. No hagas `git add -A` a ciegas: revisa `git status --short` y agrega archivos por nombre. Nunca subas `*.db`, `.env`, `google_token.json`.
9. **No hagas `git push` ni abras PR sin que el dueño lo pida.**

## Cómo trabajar

- **Lee el código real antes de citar líneas o firmas.** Si algo del plan no coincide con el código, corrige el plan y dilo en el reporte; no improvises.
- No inventes datos de la API de Meta (versiones, tarifas, formatos de número). Márcalo **VERIFICAR** y sigue.
- Si un conteo de tests esperado no coincide, **para y reporta**; no ajustes el número para que cuadre.
- No hagas refactors fuera del alcance de la tarea.
- Si necesitas una decisión del dueño (medio de aviso, umbrales, textos), déjala listada en el reporte final, no la tomes tú.

## Push en Windows

`git push` se cuelga con el credential manager. Si el dueño pide empujar:

```bash
export GH_TOKEN=$("/c/Program Files/GitHub CLI/gh.exe" auth token)
git -c credential.helper= -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f' push -u origin <rama>
```

## Plan de trabajo vigente

Carpeta `docs/plans/`:

1. `adaptador-whatsapp-meta-v2.md` — plan base (tareas 0 a 23).
2. `plan-whatsapp-v3-correcciones.md` — **correcciones que mandan sobre v2** y orden de ejecución.

Si el v2 que tienes a la mano contiene un número de teléfono real, **no lo copies** a ningún archivo del repo.
