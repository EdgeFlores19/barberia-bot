# Brief de ejecución — adaptador WhatsApp (v3)

Última actualización: 2026-10-30. Lo escribe el agente revisor (Hermes) para OpenCode.

## Plan autorizado (no hay otro)

1. `docs/plans/plan-whatsapp-v3-correcciones.md` — **manda**. Corrige el plan base.
2. `docs/plans/adaptador-whatsapp-meta-v2.md` — plan base. Se aplica **solo** donde v3 no lo contradice.
3. `AGENTS.md` — reglas de trabajo y de seguridad. Innegociables.

Si algo del plan no coincide con el código real, **detente y repórtalo**; no improvises ni "arregles" el plan.

## Estado del repo al arrancar

- Rama base: `main` @ `e89aabb`.
- Suite: **51 passed, 4 failed**. Los 4 fallos son de reloj (Tarea 0 de v2, pendiente).
- `app/reloj.py`, `app/telefono.py`, `app/avisos.py`, `app/whatsapp_*.py`, `app/wa_cliente.py`, `tests/conftest.py`, `tests/telefonos.py`, `tests/test_engine.py::_primer_slot` con margen: **no existen todavía**.

## Bloques de ejecución (un run de OpenCode por bloque)

| Bloque | Qué | Rama |
|---|---|---|
| B1 | Hotfix `GET /api/citas` (v2 Tarea 4 + C1, C4, C5) | `fix/api-citas-admin` |
| B2 | Tarea 0 (flaky) en `main`, luego Tarea 1 + Tarea 2 (config) + C13-config + Tarea 3 (`reloj`) + C2 (`tzdata`) | `feat/whatsapp-meta` |
| B3 | Tareas 5–7 (`whatsapp_formato`) + C3 (`app/telefono.py`) | `feat/whatsapp-meta` |
| B4 | Tarea 8 (`wa_cliente`) | `feat/whatsapp-meta` |
| B5 | Tarea 9 (`conftest.py`, `wa_eventos`) + C5 (borrar fixture local) | `feat/whatsapp-meta` |
| B6 | Tareas 10–11 (webhook: firma + payload) con C6 | `feat/whatsapp-meta` |
| B7 | Tarea 12 (motor I) con C7 y C8 | `feat/whatsapp-meta` |
| B8 | Tarea 13 (motor II, folio) con C9 | `feat/whatsapp-meta` |
| B9 | Tarea 14 (handoff) con C10 + C11 (`avisos`) | `feat/whatsapp-meta` |
| B10 | Tarea 15 (`to_thread`) con C12 | `feat/whatsapp-meta` |
| B11 | Tarea 16 (topes) con C13 | `feat/whatsapp-meta` |
| B12 | Tareas 17–19 (evento, rutas, candado) con C10 y C14 | `feat/whatsapp-meta` |
| B13 | Tareas 20–21 (script de simulación, docs, `.env.example`, `render.yaml`) | `feat/whatsapp-meta` |
| B14 | Tarea 22 (suite completa, conteo real). **Sin push ni PR** | `feat/whatsapp-meta` |

## Reglas duras para cada bloque

- **TDD:** test primero, corre y **ve el fallo**, implementa lo mínimo, ve el verde, commit. Un commit por tarea, Conventional Commits en español.
- **No** edites `.env`. **No** imprimas su contenido.
- **Ningún teléfono real ni token** en código, tests ni docs. Usa `tests/telefonos.py` (`TEL_A`, `TEL_B`, `TEL_C`, `TEL_A_SIN_1`).
- **Ningún log con el teléfono completo:** `app.telefono.t4`.
- **No** uses `datetime.now()` para la hora del negocio: `app.reloj.ahora()`.
- **No** llames red/`calendar_sync`/Telegram dentro de `async def`: `asyncio.to_thread`.
- **No** toques `app/slots.py` ni `app/calendar_sync.py`.
- Dependencia nueva autorizada: **solo `tzdata`**.
- **No** hagas `git add -A`. Revisa `git status --short` y agrega por nombre. Nunca `*.db`, `.env`, `google_token.json`.
- **No** hagas `git push` ni abras PR. El revisor lo decide.
- Gate de secretos (los `.md` de `docs/plans/` citan `TELEGRAM_BOT_TOKEN=` vacío: se excluyen):
  `git grep -nE "52(1)?449[0-9]{7}" -- . ':!*.example' ':!docs/plans/*'`
  `git grep -n "TELEGRAM_BOT_TOKEN=" -- . ':!*.example' ':!docs/plans/*'`
- Tests async con `asyncio.run(...)`: no hay `pytest-asyncio`.
- Comandos (Windows, git-bash, desde la raíz): `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py`
- Si un conteo de tests no cuadra con el plan: **para y repórtalo**.

## Al terminar cada bloque, reporta

1. Archivos creados/modificados y commit(s) (`git log --oneline -1`).
2. Salida real de la suite (última línea, `N passed`).
3. Diferencias entre el plan y el código real, y qué hiciste.
4. Puntos marcados **VERIFICAR**.
