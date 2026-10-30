# Plan — Adaptador + webhook de WhatsApp (Meta Cloud API)

## 0. Cambios respecto a v1

- Contrato del motor corregido: la firma real es `async def manejar(sid, texto, datos)` en `app/engine.py:167`; v1 citaba `canal="web"` como si ya existiera.
- Renumeración completa: nuevas Tareas 3, 4 y 12–16; las Tareas 3–9 de v1 pasan a 5–11, las 10–12 a 17–19 y las 14–17 a 20–23; la Tarea 13 de v1 queda absorbida por las nuevas 12 y 13.
- El motor (`canal`, `telefono`, TTL) se adelanta al e2e del webhook (Tarea 17): ese test ya llama `manejar(..., canal="wa")` y en v1 no podía pasar hasta la Tarea 13.
- Base real de la suite: 55 tests recogidos con `--collect-only`; con este plan termina en 111, no en 89.
- Conteos corregidos: en v1 la Tarea 4 dejaba `3 passed` en el archivo (decía 4), la 5 dejaba `7` (decía 8) y la 7 acumulaba **68** en la suite; en v2 esa misma tarea es la 9 y acumula **75** por las 7 pruebas de las tareas nuevas 3 y 4.
- Tarea 3 nueva: `app/reloj.py` con `ahora()` en `ZoneInfo(config.CALENDAR_TZ)` y 3 tests; suite en 58.
- Tarea 4 nueva: `GET /api/citas` con `ADMIN_TOKEN` (503 si falta, 401 si no coincide), sin `telefono` ni `calendar_event_id`; 4 tests.
- Tarea 12 nueva: `Sesion.canal`/`telefono`/`ultima_actividad`, TTL `SESION_TTL_MIN` (30 min) y purga de `SESIONES`.
- Tarea 13 nueva: en canal `wa` el folio solo abre la cita cuyo teléfono coincide (últimos 10 dígitos) y la respuesta negativa es indistinguible de "no existe".
- Tarea 14 nueva: handoff real (reset del flag, `avisos.notificar_handoff` con log/Telegram, silencio en `humano` salvo `menu`).
- Tarea 15 nueva: `asyncio.to_thread` en las tres llamadas a `calendar_sync`; el Alcance se ajusta (`calendar_sync` no cambia, `engine.py` sí).
- Tarea 16 nueva: topes propios de WhatsApp (`wa_uso` diario y por hora, `WA_ALLOWLIST`, textos del canal) y `USO_DIA` incrementado después del gate de sesión.
- `firma_valida`/`verificar` comparan bytes (`.encode()`): un payload no ASCII da 403, no un `TypeError` con 500.
- Los tests GET del webhook usan la fixture `bd`, para no sembrar la base real en el primer import de `app.main`.
- Los logs muestran solo los últimos 4 dígitos del teléfono.
- Queda documentado que registrar el evento antes de procesarlo es "como máximo una vez": si el envío falla no hay reintento (decisión aceptada para v1).
- Criterios de aceptación: se corrige "los cuatro" y se agregan los de las tareas nuevas.
- Riesgo de horarios corregido: el motor ya limita a 8 horarios + "otra fecha"; el problema real es que esos 8 caen casi todos en el primer día, y el selector de día queda como pendiente fuera del plan.
- Costos actualizados a modelo por mensaje (VERIFICAR tarifas de México) y Tarea 23 ampliada con método de pago, verificación de negocio y modo Live.
- `WA_API_VERSION` con default vigente y configurable por entorno (VERIFICAR en el changelog de Meta).
- Nota de aviso de privacidad (LFPDPPP) por guardar nombre y teléfono.

---

> Estado: listo para ejecutar. Escrito el 2026-10-28 en modo plan; **nada del código de este plan está todavía en el repo**.
> Verificado en seco: el módulo `app/whatsapp_formato.py` (Tareas 3–5) se probó contra el motor real en una copia desechable (menú de 5 opciones → lista de 5 filas, 9 horarios → lista con títulos de ≤24 caracteres, handoff detectado). El código tal como aparece aquí es el que corrió.

---

## 1. Goal

Conectar el motor existente (`app/engine.py`) a la WhatsApp Cloud API de Meta con un módulo adaptador aislado (`app/whatsapp_webhook.py` + `app/wa_cliente.py` + `app/whatsapp_formato.py`), de modo que el bot de el estudio de uñas funcione por WhatsApp sin tocar la lógica del bot ni el chat web.

## 2. Alcance

**Dentro:**

- `GET /wa/webhook` (verificación de Meta con `hub.challenge`).
- `POST /wa/webhook` (validación de `X-Hub-Signature-256`, respuesta 200 en <5 s, trabajo en segundo plano).
- Idempotencia por `message.id` (Meta reintenta; sin esto se agenda dos veces).
- Traducción de respuestas del motor a botones (≤3) / listas (≤10 filas) / texto.
- Envío saliente a Graph API, con un modo `consola` que imprime el payload en vez de llamar a Meta (para desarrollar **antes** de tener credenciales).
- Registro de handoffs en BD (es el KPI del producto: “resuelto sin humano”).
- Teléfono del cliente guardado en la cita.
- Topes de demo desactivados para el canal WhatsApp.

**Fuera (no lo hagas, aunque parezca fácil):** plantillas de mensaje y ventana de 24 h, media (audio/imagen), pagos, recordatorios salientes, multi-número, panel de conversaciones, migrar de SQLite a Postgres, tocar `llm.py`.

---

## 3. Contexto actual del repo (lo que el implementador no sabe)

Proyecto: `C:\Users\caoz_\OneDrive\Documentos\50-Proyectos-Code\barberia-bot` (rama `main`, en producción en `https://navajazo-bot.onrender.com`).

| Archivo | Qué es | Relevante para este plan |
|---|---|---|
| `app/main.py` | FastAPI con `/`, `/health`, `/api/negocio`, `/api/citas`, `/api/sesion`, `/api/mensaje` | Aquí se monta el router nuevo |
| `app/engine.py` | **El cerebro.** `async def manejar(sid, texto, datos) -> tuple[list[dict], Sesion]` | Se le añade `canal` y `Sesion.telefono` |
| `app/store.py` | SQLite (`citas`), WAL, `_con()`, `_candado`, `_ahora()` | Aquí van las 2 tablas nuevas |
| `app/config.py` | Env vars (`DEMO_CODE`, `MAX_MENSAJES_*`, `DB_FILE`, `DEEPSEEK_*`, `CALENDAR_*`) | Aquí van las `WA_*` |
| `app/slots.py`, `app/seed.py`, `app/calendar_sync.py`, `app/llm.py` | Disponibilidad, datos demo, Google Calendar, FAQ | **No se tocan** (ver Tarea 15: `calendar_sync` tampoco cambia, pero `engine.py` sí envuelve sus llamadas en `asyncio.to_thread`) |
| `static/index.html` | Chat web (estética WhatsApp) | **No se toca** |
| `tests/` | 55 pruebas en pytest (`test_engine.py`, `test_slots.py`, `test_seed.py`) + `smoke_test.py` | Aquí va lo nuevo |

**Contrato del motor (lo único que el adaptador necesita saber):**

```python
respuestas, sesion = await engine.manejar(sid: str, texto: str, datos: dict, canal: str = "web")
# respuestas: [{"tipo": "texto", "texto": "..."}] o
#             [{"tipo": "botones", "texto": "...", "opciones": [{"id": "...", "label": "..."}]}]
# sesion: dataclass con paso, nombre, folio, mensajes, handoff, resuelta, telefono
```

- El motor **ya** acepta texto libre ("cancelar AMB-7K4Q", "mover AMB-7K4Q", "precios", un nombre, el `id` de un horario). No hay que parsear nada en el adaptador: se le pasa el texto y él decide.
- Cuando el motor manda `opciones`, el `id` es lo que hay que devolverle como texto (los botones son solo etiquetas bonitas). Es exactamente lo que ya hace el chat web.
- `engine.SESIONES` es un `dict[str, Sesion]` en memoria; la clave es el `sid`. Para WhatsApp el `sid` será `f"wa:{telefono}"`.

**Supuestos:**

1. Aún no hay credenciales de Meta. Todo el plan se puede completar y probar sin ellas (modo `consola` + `scripts/wa_simula.py`). Las credenciales se pegan al final en Render (Tarea 21).
2. Un solo proceso uvicorn (el `default` de Render). Con varios workers, `engine.SESIONES` y los topes en memoria dejarían de ser fiables.
3. Python 3.12 en Render / 3.14 local. `httpx` ya está en `requirements.txt`: **no agregues dependencias**.
4. Los tests corren con `.venv/Scripts/python.exe` (Windows, git-bash). No hay `pytest-asyncio`: usa `asyncio.run(...)` como ya hacen los tests existentes.

**Preflight (2 min, obligatorio antes de empezar):**

```bash
cd "/c/Users/caoz_/OneDrive/Documentos/50-Proyectos-Code/barberia-bot"
git status --short          # esperado: sin cambios (o solo archivos ignorados)
git log --oneline -1        # esperado: 99c30b3 docs: ...
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
# esperado: "55 passed"
```

Si el número no es 55, para y reporta: el repo no está en el estado que este plan asume.

---

## 4. Arquitectura

Tres módulos nuevos, cada uno con una sola responsabilidad: `whatsapp_formato.py` es una función pura motor→payload (sin red, sin BD, trivial de testear), `wa_cliente.py` es el único lugar que habla con Meta (con un modo `consola` que lo hace testeable sin credenciales), y `whatsapp_webhook.py` es el pegamento: verifica la firma, deduplica por `message.id`, convierte `wa_id` en `sid`, llama a `engine.manejar(..., canal="wa")` y manda las respuestas por `wa_cliente`.

El motor no se entera de que existe WhatsApp: los únicos cambios en él son dos campos (`Sesion.telefono`, parámetro `canal`) y ambos tienen valor por defecto, así que el chat web sigue idéntico. Toda la traducción de límites de Meta (3 botones / 10 filas / 24 caracteres) vive en `whatsapp_formato.py`, que es donde se puede probar sin red.

---

## 5. Convenciones para el implementador

- **Rama:** `feat/whatsapp-meta`, creada desde `main`. Un commit por tarea, mensajes en español con Conventional Commits (`feat:`, `test:`, `fix:`, `docs:`, `chore:`).
- **Ciclo por tarea (TDD):** escribe el test → córrelo y **ve el fallo** → implementa lo mínimo → córrelo y ve el verde → commit.
- Comentarios y mensajes en español; type hints; `from __future__ import annotations` en cada archivo nuevo.
- No edites `.env` (no está en git). Todo lo nuevo va a `.env.example`.
- Al terminar cada tarea, el comando global debe seguir en verde:
  ```bash
  .venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
  ```
- `git push` se cuelga con el credential manager de Windows. Si necesitas empujar:
  ```bash
  export GH_TOKEN=$("/c/Program Files/GitHub CLI/gh.exe" auth token)
  git -c credential.helper= -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f' push origin feat/whatsapp-meta
  ```

---

## 6. Tareas

### Tarea 0 — Arreglar los dos tests flaky (5 min, antes de todo)

`tests/test_engine.py::_primer_slot()` devuelve el primer hueco libre, que a veces cae dentro de las 2 h de antelación y hace fallar el test de cancelación según la hora del día. Ya está diagnosticado y verificado: se filtra por margen.

**`tests/test_engine.py`** — reemplaza el helper completo (líneas 40–43):

```python
def _primer_slot(datos: dict, servicio_id: str = "manicura") -> str:
    """Primer hueco con margen suficiente para que la cancelación sea libre.

    Sin este margen el test falla si el primer hueco cae dentro de las
    `cancelacion_libre_hasta_horas` y la hora actual está cerca del cierre.
    """
    reglas = datos["reglas_agenda"]
    limite = datetime.now() + timedelta(hours=reglas["cancelacion_libre_hasta_horas"] + 1)
    libres = [
        s for s in engine.slots_disponibles(datos, servicio_id)
        if datetime.fromisoformat(s) >= limite
    ]
    assert libres, "debería haber disponibilidad con margen en los próximos 7 días"
    return libres[0]
```

Verificación:

```bash
.venv/Scripts/python.exe -m pytest tests/test_engine.py -q | tail -2
# esperado: "20 passed" (o el total del archivo), 0 failed
```

Commit: `test: margen de cancelación en _primer_slot para evitar flaky`.

---

### Tarea 1 — Rama de trabajo (1 min)

```bash
git switch -c feat/whatsapp-meta
git status --short            # esperado: vacío
```

---

### Tarea 2 — Variables de configuración (3 min)

**`app/config.py`** — añade al final del archivo:

```python
# ---------- Adaptador de WhatsApp Cloud API ----------
# WA_MODO="consola" imprime el payload en el log en vez de llamar a Meta.
# Sirve para desarrollar y probar el adaptador sin credenciales ni número.
WA_MODO = os.getenv("WA_MODO", "graph").strip().lower()
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "").strip()
WA_APP_SECRET = os.getenv("WA_APP_SECRET", "").strip()
WA_TOKEN = os.getenv("WA_TOKEN", "").strip()
WA_PHONE_ID = os.getenv("WA_PHONE_ID", "").strip()
WA_API_VERSION = os.getenv("WA_API_VERSION", "v26.0").strip()   # VERIFICAR en el changelog de Meta; configurable por entorno
# Un cliente real puede escribir 60 mensajes; el tope de 30 es de la demo web.
MAX_MENSAJES_POR_SESION_WA = int(os.getenv("MAX_MENSAJES_POR_SESION_WA", "500"))


def wa_habilitado() -> bool:
    """El canal se enciende solo si están las credenciales que se usan de verdad."""
    if WA_MODO == "consola":
        return bool(WA_VERIFY_TOKEN and WA_APP_SECRET)
    return all((WA_VERIFY_TOKEN, WA_APP_SECRET, WA_TOKEN, WA_PHONE_ID))
```

Si `config.py` no importa `os` en la cabecera, añade `import os` arriba.

Verificación:

```bash
.venv/Scripts/python.exe -c "from app import config; print(config.WA_MODO, config.wa_habilitado(), config.MAX_MENSAJES_POR_SESION_WA)"
# esperado: "graph False 500"
```

Commit: `feat: configuración del canal WhatsApp`.

---

**Variables de entorno que agregan las tareas nuevas** (todas con default seguro, ninguna obligatoria):

```python
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()            # Tarea 4: vacío = 503
SESION_TTL_MIN = int(os.getenv("SESION_TTL_MIN", "30"))       # Tarea 12
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()   # Tarea 14
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()       # Tarea 14
WA_ALLOWLIST = {t for t in os.getenv("WA_ALLOWLIST", "").replace(" ", "").split(",") if t}
WA_MAX_POR_DIA = int(os.getenv("WA_MAX_POR_DIA", "150"))      # Tarea 16
WA_MAX_POR_HORA = int(os.getenv("WA_MAX_POR_HORA", "12"))     # Tarea 16
```

Cada una se documenta en `.env.example` en la Tarea 21. Ninguna se commitea con valor.

---

### Tarea 3 — `app/reloj.py`: la hora del negocio (10 min)

**Por qué:** Render corre en **UTC** y hoy la hora del negocio se toma con `datetime.now()`, que es la del servidor. Eso ya desalinea los horarios, la anticipación mínima y la ventana de cancelación; el canal WhatsApp lo haría visible a clientes reales. `config.CALENDAR_TZ` ya existe (`app/config.py`, default `America/Mexico_City`); falta un único punto que lo use.

Call sites a reemplazar (todos pasan a `reloj.ahora()`): `app/engine.py` (`datetime.now()` en 125, 163, 174-175 y 312), `app/store.py:73` (dentro de `_ahora()`) y `app/seed.py:33`. `app/slots.py` **no** se toca: ya recibe `ahora` como parámetro, que es justamente lo que lo hace testeable.

**Test primero** — `tests/test_reloj.py` (archivo nuevo, 3 tests):

```python
"""La hora del negocio: un solo lugar para "ahora"."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import config, reloj


def test_ahora_usa_la_zona_configurada(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TZ", "UTC")
    utc = reloj.ahora()

    monkeypatch.setattr(config, "CALENDAR_TZ", "Asia/Tokyo")
    tokio = reloj.ahora()

    assert abs((tokio - utc) - timedelta(hours=9)) < timedelta(seconds=2)


def test_ahora_devuelve_naive_en_hora_local(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TZ", "America/Mexico_City")
    a = reloj.ahora()
    referencia = datetime.now(ZoneInfo("America/Mexico_City")).replace(tzinfo=None)

    assert a.tzinfo is None
    assert abs(a - referencia) < timedelta(seconds=2)


def test_zona_invalida_falla_con_nombre_claro(monkeypatch):
    monkeypatch.setattr(config, "CALENDAR_TZ", "Marte/Olympus")

    with pytest.raises(Exception) as exc:
        reloj.ahora()

    assert "Marte/Olympus" in str(exc.value)
```

**Fallo esperado:** `ModuleNotFoundError: No module named 'app.reloj'` en los tres.

**Implementación** — `app/reloj.py` (archivo nuevo):

```python
"""La hora del negocio. Todo lo que necesite "ahora" pasa por aquí.

Render corre en UTC: usar datetime.now() directamente da una hora equivocada
en los slots, la anticipación mínima y la ventana de cancelación.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app import config


def ahora() -> datetime:
    """Hora local del negocio, sin tzinfo (el resto del código compara naive)."""
    zona = ZoneInfo(config.CALENDAR_TZ)
    return datetime.now(zona).replace(tzinfo=None)
```

Y en cada call site: `from app import reloj` + `reloj.ahora()` en lugar de `datetime.now()`. `app/store.py` conserva su `_ahora()` como envoltorio (`return reloj.ahora().isoformat(...)`) para no cambiar su firma.

**Ajuste de tests existentes:** los tests que dependen del reloj deben fijar la zona. En `tests/test_engine.py` y `tests/test_slots.py` no hace falta tocar nada: `slots` recibe `ahora` por parámetro y el motor lo llama con `reloj.ahora()`, así que siguen siendo deterministas con la zona del entorno. Si alguno falla por la diferencia UTC/local, la corrección es `monkeypatch.setattr(config, "CALENDAR_TZ", "UTC")` en el test, **nunca** volver a `datetime.now()` en el código.

**Verificación:**

```bash
.venv/Scripts/python.exe -m pytest tests/test_reloj.py -q        # "3 passed"
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
# esperado: "58 passed"
```

**Commit:** `fix: usa la hora del negocio (CALENDAR_TZ) en lugar de la del servidor`

---

### Tarea 4 — `GET /api/citas` deja de ser público (15 min, antes de exponer WhatsApp)

**Por qué:** hoy `GET /api/citas` (`app/main.py:77-79`) no pide nada y devuelve `store.listar()`, que es un `SELECT *` (`app/store.py:187-194`): incluye `telefono` y `calendar_event_id`. Con el chat web pasa desapercibido; el día que exista el canal WhatsApp esa ruta publica los teléfonos reales de los clientes. Antes de abrir el canal, se cierra.

Comportamiento pedido: sin `ADMIN_TOKEN` configurado → **503**; cabecera ausente o distinta → **401**; correcta → **200** con las citas **sin** `telefono` ni `calendar_event_id`.

Nadie del producto consume esta ruta: el único consumidor es `tests/smoke_test.py` (no corre en pytest), que hay que actualizar en esta misma tarea.

**Test primero** — `tests/test_api_citas.py` (archivo nuevo, 4 tests):

```python
"""GET /api/citas: cerrado por ADMIN_TOKEN y sin datos personales."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, store


@pytest.fixture()
def bd(tmp_path, monkeypatch):
    """Base temporal. Sin esto, importar app.main siembra la BD real."""
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.db")
    store._inicializada = False
    store.init()
    yield
    store.reset()


@pytest.fixture()
def cliente(bd):
    from app.main import app

    return TestClient(app)


def test_sin_admin_token_configurado_devuelve_503(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")

    assert cliente.get("/api/citas").status_code == 503


def test_sin_cabecera_devuelve_401(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secreto-de-prueba")

    assert cliente.get("/api/citas").status_code == 401


def test_cabecera_incorrecta_devuelve_401(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secreto-de-prueba")

    r = cliente.get("/api/citas", headers={"X-Admin-Token": "otro"})

    assert r.status_code == 401


def test_con_token_devuelve_citas_sin_datos_personales(cliente, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "secreto-de-prueba")
    store.crear(slot="2026-11-03T10:00", servicio_id="manicura", nombre="Ana", telefono="5215500000011")

    r = cliente.get("/api/citas", headers={"X-Admin-Token": "secreto-de-prueba"})

    assert r.status_code == 200
    citas = r.json()["citas"]
    assert len(citas) == 1
    assert "telefono" not in citas[0]
    assert "calendar_event_id" not in citas[0]
```

**Fallo esperado:** los tres primeros dan `200` (nadie pide token) y el cuarto falla en `"telefono" not in citas[0]`.

**Implementación:**

1. `app/config.py`, junto a las demás env vars:

```python
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
```

2. `app/main.py`: helper y ruta protegida.

```python
import hmac


_SIN_TELEFONO = ("telefono", "calendar_event_id")


def _exige_admin(x_admin_token: str | None) -> None:
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
            {k: v for k, v in c.items() if k not in _SIN_TELEFONO} for c in store.listar()
        ]
    }
```

(`Header` viene de `fastapi`; `HTTPException` ya está importado en `app/main.py`.)

3. `tests/smoke_test.py`: mandar `headers={"X-Admin-Token": os.getenv("ADMIN_TOKEN", "")}` en las llamadas a `/api/citas` y documentar la variable en el encabezado del script.

4. `.env.example`: añadir `ADMIN_TOKEN=` con un comentario de que vacío = ruta deshabilitada (503).

**Verificación:**

```bash
.venv/Scripts/python.exe -m pytest tests/test_api_citas.py -q   # "4 passed"
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
# esperado: "62 passed"
```

**Commit:** `fix: protege GET /api/citas con ADMIN_TOKEN y oculta datos personales`

---

### Tarea 5 — `app/whatsapp_formato.py`: texto (5 min)

> Nombre exacto del archivo: `app/whatsapp_formato.py`. (No lo llames `formato_wa.py`: así queda junto a `whatsapp_webhook.py` al ordenar por nombre.)

**Test primero — `tests/test_whatsapp_formato.py`:**

```python
"""Tests del traductor motor -> payloads de WhatsApp Cloud API."""
from __future__ import annotations

from app import whatsapp_formato


def test_texto_plano_se_convierte_en_mensaje_de_texto():
    assert whatsapp_formato.a_payloads([{"tipo": "texto", "texto": "Hola"}]) == [
        {"type": "text", "text": {"body": "Hola"}}
    ]
```

Corre y verifica que falla:

```bash
.venv/Scripts/python.exe -m pytest tests/test_whatsapp_formato.py -q | tail -3
# esperado: ModuleNotFoundError: No module named 'app.whatsapp_formato'
```

**Implementación — `app/whatsapp_formato.py`:**

```python
"""Traduce las respuestas del motor a payloads de WhatsApp Cloud API.

El motor devuelve {'tipo': 'texto'|'botones', ...}. WhatsApp limita:
3 reply buttons o 10 filas de lista, títulos de 24 caracteres (filas) y
20 (botones). Todo el recorte de aquí abajo existe por esos límites.
"""
from __future__ import annotations

from typing import Any

MAX_TEXTO = 4096
MAX_CUERPO_INTERACTIVO = 1024
MAX_FILAS = 10
MAX_TITULO_FILA = 24
MAX_TITULO_BOTON = 20
MAX_BOTONES = 3


def _texto(cuerpo: str) -> dict[str, Any]:
    return {"type": "text", "text": {"body": (cuerpo or "")[:MAX_TEXTO]}}


def a_payloads(respuestas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte la lista de respuestas del motor en payloads para la Graph API."""
    payloads: list[dict[str, Any]] = []
    for r in respuestas or []:
        texto = (r.get("texto") or "").strip() or "Elige una opción:"
        payloads.append(_texto(texto))
    return payloads
```

Verificación: el test de la Tarea 5 pasa (`1 passed`).
Commit: `feat: traductor de respuestas del motor a texto de WhatsApp`.

---

### Tarea 6 — Botones (≤3) y listas (4–10) (5 min)

**Test — añade a `tests/test_whatsapp_formato.py`:**

```python
def test_tres_opciones_van_como_botones():
    resp = [{
        "tipo": "botones",
        "texto": "¿Qué necesitas?",
        "opciones": [
            {"id": "agendar", "label": "Agendar cita"},
            {"id": "citas", "label": "Mi cita"},
            {"id": "precios", "label": "Precios"},
        ],
    }]
    p = whatsapp_formato.a_payloads(resp)[0]
    assert p["interactive"]["type"] == "button"
    botones = p["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"] for b in botones] == ["agendar", "citas", "precios"]
    assert all(b["type"] == "reply" for b in botones)


def test_mas_de_tres_opciones_van_como_lista():
    resp = [{
        "tipo": "botones",
        "texto": "Menú",
        "opciones": [{"id": f"op{i}", "label": f"Opción {i}"} for i in range(5)],
    }]
    p = whatsapp_formato.a_payloads(resp)[0]
    assert p["interactive"]["type"] == "list"
    filas = p["interactive"]["action"]["sections"][0]["rows"]
    assert [f["id"] for f in filas] == ["op0", "op1", "op2", "op3", "op4"]
```

Corre: `2 failed` (KeyError `interactive`). Luego añade a `app/whatsapp_formato.py`:

```python
def _corta(texto: str, n: int) -> str:
    """Colapsa espacios y recorta a n caracteres con puntos suspensivos."""
    limpio = " ".join((texto or "").split())
    if len(limpio) <= n:
        return limpio
    return limpio[: n - 1].rstrip() + "…"


def _botones(cuerpo: str, opciones: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": _corta(cuerpo, MAX_CUERPO_INTERACTIVO)},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": o["id"], "title": _corta(o["label"], MAX_TITULO_BOTON)},
                    }
                    for o in opciones[:MAX_BOTONES]
                ]
            },
        },
    }


def _lista(cuerpo: str, opciones: list[dict[str, str]]) -> dict[str, Any]:
    filas = [
        {"id": o["id"], "title": _corta(o["label"], MAX_TITULO_FILA)}
        for o in opciones[:MAX_FILAS]
    ]
    return {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": _corta(cuerpo, MAX_CUERPO_INTERACTIVO)},
            "action": {
                "button": "Ver opciones",
                "sections": [{"title": "Opciones", "rows": filas}],
            },
        },
    }
```

y sustituye el cuerpo de `a_payloads` por:

```python
def a_payloads(respuestas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte la lista de respuestas del motor en payloads para la Graph API."""
    payloads: list[dict[str, Any]] = []
    for r in respuestas or []:
        texto = (r.get("texto") or "").strip() or "Elige una opción:"
        opciones = r.get("opciones") or []
        if r.get("tipo") == "botones" and opciones:
            if len(opciones) <= MAX_BOTONES:
                payloads.append(_botones(texto, opciones))
            else:
                payloads.append(_lista(texto, opciones))
        else:
            payloads.append(_texto(texto))
    return payloads
```

Verificación: `3 passed` (2 nuevos + el de la Tarea 5; en `tests/test_whatsapp_formato.py`).
Commit: `feat: botones y listas de WhatsApp con sus límites`.

---

### Tarea 7 — Recortes y casos borde (5 min)

**Test — añade:**

```python
def test_lista_se_corta_a_diez_filas():
    resp = [{
        "tipo": "botones",
        "texto": "Horarios",
        "opciones": [{"id": f"2026-11-01T{h:02d}:00", "label": f"Slot {h}"} for h in range(30)],
    }]
    filas = whatsapp_formato.a_payloads(resp)[0]["interactive"]["action"]["sections"][0]["rows"]
    assert len(filas) == 10


def test_titulos_se_recortan_al_limite_de_whatsapp():
    largo = "Manicura + pedicura con exfoliación y masaje"
    cinco = [{"id": str(i), "label": largo} for i in range(5)]
    filas = whatsapp_formato.a_payloads(
        [{"tipo": "botones", "texto": "Servicios", "opciones": cinco}]
    )[0]["interactive"]["action"]["sections"][0]["rows"]
    assert all(len(f["title"]) <= 24 for f in filas)

    tres = [{"id": str(i), "label": largo} for i in range(3)]
    botones = whatsapp_formato.a_payloads(
        [{"tipo": "botones", "texto": "Servicios", "opciones": tres}]
    )[0]["interactive"]["action"]["buttons"]
    assert all(len(b["reply"]["title"]) <= 20 for b in botones)


def test_texto_vacio_nunca_produce_cuerpo_vacio():
    p = whatsapp_formato.a_payloads([{"tipo": "texto", "texto": "   "}])[0]
    assert p["text"]["body"].strip()


def test_respuestas_vacias_no_producen_payloads():
    assert whatsapp_formato.a_payloads([]) == []
```

Corre y **verifica que pasan los cuatro a la primera**: `_lista` ya corta con `opciones[:MAX_FILAS]` y `_corta` ya respeta los dos límites, así que esta tarea no lleva código nuevo. Si alguno falla, es que copiaste mal la Tarea 6: vuelve a comparar, no inventes una solución distinta.

Verificación: `7 passed` (4 nuevos + los 3 anteriores del archivo).
Commit: `test: límites de filas y títulos en los payloads de WhatsApp`.

---

### Tarea 8 — `app/wa_cliente.py`: envío saliente (5 min)

**Test — `tests/test_wa_cliente.py`:**

```python
"""Tests del cliente de Graph API: no deben tocar la red real."""
from __future__ import annotations

import asyncio

from app import config, wa_cliente


class _ClienteFalso:
    llamado: tuple | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        _ClienteFalso.llamado = (url, json, headers)

        class _R:
            status_code = 200
            text = "{}"

        return _R()


def test_modo_consola_no_llama_a_la_red(monkeypatch, capsys):
    monkeypatch.setattr(config, "WA_MODO", "consola")

    def _explota(*args, **kwargs):
        raise AssertionError("en modo consola no se debe abrir un cliente HTTP")

    monkeypatch.setattr(wa_cliente.httpx, "AsyncClient", _explota)
    ok = asyncio.run(wa_cliente.enviar("5215500000001", {"type": "text", "text": {"body": "hola"}}))
    assert ok is True
    assert "[wa:consola]" in capsys.readouterr().out


def test_envia_a_graph_con_telefono_y_token(monkeypatch):
    monkeypatch.setattr(config, "WA_MODO", "graph")
    monkeypatch.setattr(config, "WA_TOKEN", "T0KEN")
    monkeypatch.setattr(config, "WA_PHONE_ID", "123456")
    monkeypatch.setattr(wa_cliente.httpx, "AsyncClient", _ClienteFalso)

    ok = asyncio.run(wa_cliente.enviar("5215500000001", {"type": "text", "text": {"body": "hola"}}))

    assert ok is True
    url, cuerpo, cabeceras = _ClienteFalso.llamado
assert url == f"https://graph.facebook.com/{config.WA_API_VERSION}/123456/messages"
    assert cuerpo["messaging_product"] == "whatsapp"
    assert cuerpo["to"] == "5215500000001"
    assert cabeceras["Authorization"] == "Bearer T0KEN"


def test_sin_token_no_intenta_enviar(monkeypatch):
    monkeypatch.setattr(config, "WA_MODO", "graph")
    monkeypatch.setattr(config, "WA_TOKEN", "")
    monkeypatch.setattr(config, "WA_PHONE_ID", "")
    assert asyncio.run(wa_cliente.enviar("5215500000001", {"type": "text", "text": {"body": "x"}})) is False
```

Corre: `ModuleNotFoundError`. **Implementación — `app/wa_cliente.py`:**

```python
"""Envío saliente a la WhatsApp Cloud API. Única puerta a Meta."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config

log = logging.getLogger(__name__)


def _url() -> str:
    return f"https://graph.facebook.com/{config.WA_API_VERSION}/{config.WA_PHONE_ID}/messages"


async def enviar(wa_id: str, payload: dict[str, Any]) -> bool:
    """Manda un payload ya armado. Devuelve True si Meta lo aceptó."""
    cuerpo = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": wa_id,
        **payload,
    }

    if config.WA_MODO == "consola":
        print(f"[wa:consola] -> {wa_id} {cuerpo}")
        return True

    if not (config.WA_TOKEN and config.WA_PHONE_ID):
        log.warning("wa: faltan WA_TOKEN o WA_PHONE_ID; no se envía nada")
        return False

    cabeceras = {"Authorization": f"Bearer {config.WA_TOKEN}"}
    for intento in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=10) as cli:
                r = await cli.post(_url(), json=cuerpo, headers=cabeceras)
        except httpx.HTTPError as exc:
            log.warning("wa: error de red (intento %s): %s", intento, exc)
            continue
        if r.status_code < 300:
            return True
        log.error("wa: Meta respondió %s %s", r.status_code, r.text[:300])
        if r.status_code < 500:
            # 400/401/404 no se arreglan reintentando.
            return False
    return False
```

Verificación: `3 passed`.
Commit: `feat: cliente de envío a WhatsApp Cloud API con modo consola`.

---

### Tarea 9 — Fixture compartida + tabla `wa_eventos` (5 min)

**`tests/conftest.py`** (nuevo; base temporal para los tests nuevos, sin tocar los fixtures que ya tiene `test_engine.py`):

```python
"""Fixtures compartidas para los tests del canal WhatsApp."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import config, engine, store


@pytest.fixture()
def bd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Base SQLite temporal y sesiones limpias por prueba."""
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.db")
    monkeypatch.setattr(store, "_inicializada", False)
    store.init()
    engine.SESIONES.clear()
    yield
    store.reset()
```

**Test — `tests/test_wa_store.py`:**

```python
"""Tests de las tablas nuevas del adaptador."""
from __future__ import annotations

from app import store


def test_registrar_evento_acepta_la_primera_vez(bd):
    assert store.registrar_evento("wamid.A") is True


def test_registrar_evento_rechaza_el_reintento(bd):
    store.registrar_evento("wamid.A")
    assert store.registrar_evento("wamid.A") is False


def test_handoff_queda_registrado(bd):
    store.registrar_handoff("wa:5215500000001", "quiero hablar con una persona")
    filas = store.handoffs()
    assert len(filas) == 1
    assert filas[0]["sid"] == "wa:5215500000001"
    assert "persona" in filas[0]["mensaje"]
```

Corre: `AttributeError: module 'app.store' has no attribute 'registrar_evento'`.

**Implementación — `app/store.py`:**

1. Añade al final de la constante `ESQUEMA` (antes del cierre del string):

```sql
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
```

2. Añade al final del archivo:

```python
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
```

Verificación:

```bash
rm -f barberia.db barberia.db-wal barberia.db-shm
.venv/Scripts/python.exe -m pytest tests/test_wa_store.py -q | tail -2
# esperado: "3 passed"
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
# esperado: 75 passed (55 + 3)
```

Commit: `feat: tabla de eventos y de handoffs para el canal WhatsApp`.

---

### Tarea 10 — `app/whatsapp_webhook.py`: verificación de Meta y firma (5 min)

**Test — `tests/test_wa_webhook.py`:**

```python
"""Tests del webhook: firma, extracción de mensajes y procesamiento."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app import config, engine, store, whatsapp_webhook


def _firmar(cuerpo: bytes, secreto: str = "s3cr3t") -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def _payload(texto: str = "precios", msg_id: str = "wamid.1", telefono: str = "5215500000001") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "0",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "messages": [{
                        "id": msg_id,
                        "from": telefono,
                        "timestamp": "1793000000",
                        "type": "text",
                        "text": {"body": texto},
                    }],
                },
            }],
        }],
    }


def test_firma_valida_acepta_la_correcta(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")
    cuerpo = b'{"a":1}'
    assert whatsapp_webhook.firma_valida(cuerpo, _firmar(cuerpo)) is True


def test_firma_valida_rechaza_firma_ajena(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")
    cuerpo = b'{"a":1}'
    assert whatsapp_webhook.firma_valida(cuerpo, _firmar(cuerpo, "otro")) is False


def test_firma_valida_rechaza_cabecera_vacia(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")
    assert whatsapp_webhook.firma_valida(b'{"a":1}', "") is False


def test_firma_valida_rechaza_sin_secreto(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "")
    cuerpo = b'{"a":1}'
    assert whatsapp_webhook.firma_valida(cuerpo, _firmar(cuerpo)) is False
```

Corre: `ModuleNotFoundError`.

**Implementación — `app/whatsapp_webhook.py`:**

```python
"""Adaptador de WhatsApp Cloud API para el motor del bot.

El motor (app/engine.py) no sabe que existe WhatsApp: aquí se traduce
entrada de Meta a texto del motor y respuestas del motor a payloads de Graph.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from . import config, engine, store, wa_cliente, whatsapp_formato

log = logging.getLogger(__name__)

router = APIRouter()

Enviador = Callable[[str, dict[str, Any]], Awaitable[bool]]

AVISO_SIN_TEXTO = (
    "Por ahora solo entiendo mensajes de texto. Escribe *menu* y te muestro las opciones."
)


def firma_valida(cuerpo: bytes, cabecera: str) -> bool:
    """Valida X-Hub-Signature-256: HMAC-SHA256 del cuerpo crudo con el app secret."""
    if not config.WA_APP_SECRET or not cabecera.startswith("sha256="):
        return False
    esperado = hmac.new(config.WA_APP_SECRET.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecera.split("=", 1)[1].strip())
```

Verificación: `4 passed`.
Commit: `feat: verificación de firma del webhook de Meta`.

---

### Tarea 11 — Lectura del payload (5 min)

**Test — añade:**

```python
def test_extrae_mensaje_de_texto():
    msgs = whatsapp_webhook.extraer_mensajes(_payload("hola"))
    assert msgs == [{"id": "wamid.1", "wa_id": "5215500000001", "texto": "hola", "tipo": "text"}]


def test_extrae_la_opcion_elegida_en_una_lista():
    p = _payload()
    p["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "id": "wamid.2",
        "from": "5215500000001",
        "type": "interactive",
        "interactive": {"type": "list_reply", "list_reply": {"id": "agendar", "title": "Agendar cita"}},
    }
    assert whatsapp_webhook.extraer_mensajes(p)[0]["texto"] == "agendar"


def test_ignora_confirmaciones_de_entrega():
    p = _payload()
    del p["entry"][0]["changes"][0]["value"]["messages"]
    p["entry"][0]["changes"][0]["value"]["statuses"] = [{"id": "wamid.1", "status": "delivered"}]
    assert whatsapp_webhook.extraer_mensajes(p) == []
    # reaction, unsupported y system se ignoran en silencio: no generan respuesta.
    p["entry"][0]["changes"][0]["value"]["messages"] = [
        {"id": "wamid.4", "from": "5215500000001", "type": "reaction",
         "reaction": {"message_id": "wamid.1", "emoji": "\U0001f44d"}},
        {"id": "wamid.5", "from": "5215500000001", "type": "unsupported"},
        {"id": "wamid.6", "from": "5215500000001", "type": "system"},
    ]
    assert whatsapp_webhook.extraer_mensajes(p) == []


def test_mensaje_de_imagen_llega_con_texto_vacio():
    p = _payload()
    p["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "id": "wamid.3", "from": "5215500000001", "type": "image", "image": {"id": "X"},
    }
    msgs = whatsapp_webhook.extraer_mensajes(p)
    assert msgs[0]["tipo"] == "image"
    assert msgs[0]["texto"] == ""
```

Corre: `AttributeError: ... extraer_mensajes`. **Añade al módulo:**

```python
# Tipos que Meta manda y que no son un mensaje del cliente: se descartan sin avisar.
# El aviso de "solo entiendo texto" queda solo para image, audio, video, document,
# sticker y location (Tarea 18).
TIPOS_IGNORADOS = frozenset({"reaction", "unsupported", "system", "errors", "ephemeral"})


def extraer_mensajes(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Mensajes del payload. `texto` queda vacío si el tipo no está soportado."""
    salida: list[dict[str, str]] = []
    for entrada in payload.get("entry") or []:
        for cambio in entrada.get("changes") or []:
            valor = cambio.get("value") or {}
            for msg in valor.get("messages") or []:
                msg_id = str(msg.get("id") or "")
                wa_id = str(msg.get("from") or "")
                tipo = str(msg.get("type") or "")
                if tipo in TIPOS_IGNORADOS:
                    continue          # reaction, unsupported, system, errors, ...
                texto = ""
                if tipo == "text":
                    texto = ((msg.get("text") or {}).get("body") or "").strip()
                elif tipo == "interactive":
                    inter = msg.get("interactive") or {}
                    elegido = inter.get("button_reply") or inter.get("list_reply") or {}
                    texto = str(elegido.get("id") or "").strip()
                if msg_id and wa_id:
                    salida.append({"id": msg_id, "wa_id": wa_id, "texto": texto, "tipo": tipo})
    return salida
```

Verificación: `8 passed`.
Commit: `feat: extracción de mensajes del payload de Meta`.

---

### Tarea 12 — Motor I: `canal`, `telefono` y expiración de sesión (25 min)

**Contrato real de hoy:** `async def manejar(sid: str, texto: str, datos: dict[str, Any]) -> tuple[list[dict[str, Any]], Sesion]` (`app/engine.py:167`) y `Sesion` (`app/engine.py:35-46`) con `sid, paso, servicio_id, slot, nombre, folio, mensajes, handoff, resuelta, historial`. Los helpers `_cancelar_folio` (380), `_empezar_reagendar` (405) y `_paso_citas_folio` (345) **no reciben canal**: por eso el canal tiene que vivir en la sesión, no en el argumento.

**Por qué ahora y no al final (como en v1):** el e2e del webhook (Tarea 17) llama `manejar(..., canal="wa")` y en v1 eso no existía todavía cuando ese test se escribía.

**Test primero** — `tests/test_engine.py` (5 tests nuevos, el archivo queda en 16):

```python
def test_canal_y_telefono_se_guardan_en_la_sesion():
    resp, s = asyncio.run(engine.manejar("s1", "hola", DATOS, canal="wa", telefono="5215500000011"))

    assert s.canal == "wa"
    assert s.telefono == "5215500000011"
    assert resp


def test_ttl_reinicia_el_paso_a_inicio(monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    engine.SESIONES["s2"] = engine.Sesion("s2", paso="servicio", servicio_id="manicura")
    engine.SESIONES["s2"].ultima_actividad = reloj.ahora() - timedelta(minutes=31)

    resp, s = asyncio.run(engine.manejar("s2", "hola", DATOS))

    assert s.paso == "inicio"
    assert s.servicio_id is None
    assert resp


def test_ttl_no_reinicia_dentro_del_limite(monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    engine.SESIONES["s3"] = engine.Sesion("s3", paso="servicio", servicio_id="manicura")
    engine.SESIONES["s3"].ultima_actividad = reloj.ahora() - timedelta(minutes=5)

    asyncio.run(engine.manejar("s3", "manicura", DATOS))

    assert engine.SESIONES["s3"].paso != "inicio"


def test_ultima_actividad_se_actualiza_en_cada_mensaje():
    s = engine.SESIONES["s4"] = engine.Sesion("s4", paso="servicio", servicio_id="manicura")
    s.ultima_actividad = reloj.ahora() - timedelta(minutes=10)

    asyncio.run(engine.manejar("s4", "manicura", DATOS))

    assert reloj.ahora() - s.ultima_actividad < timedelta(seconds=5)


def test_purga_elimina_sesiones_viejas(monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    engine.SESIONES.clear()
    engine.SESIONES["vieja"] = engine.Sesion("vieja")
    engine.SESIONES["vieja"].ultima_actividad = reloj.ahora() - timedelta(hours=5)

    asyncio.run(engine.manejar("nueva", "hola", DATOS))

    assert "vieja" not in engine.SESIONES
```

**Fallo esperado:** `TypeError: manejar() got an unexpected keyword argument 'canal'` en los cinco.

**Implementación:**

```python
# app/config.py
SESION_TTL_MIN = int(os.getenv("SESION_TTL_MIN", "30"))

# app/engine.py
from . import calendar_sync, config, llm, reloj, store

@dataclass
class Sesion:
    sid: str
    paso: str = "inicio"
    servicio_id: str | None = None
    slot: str | None = None
    nombre: str = ""
    folio: str | None = None
    mensajes: int = 0
    handoff: bool = False
    resuelta: bool = False
    historial: list[dict[str, str]] = field(default_factory=list)
    canal: str = "web"                      # "web" | "wa"
    telefono: str = ""                      # tal como lo manda el canal
    ultima_actividad: datetime = field(default_factory=reloj.ahora)


def _purgar_sesiones() -> None:
    tope = reloj.ahora() - timedelta(minutes=config.SESION_TTL_MIN * 8)
    for sid in [k for k, v in SESIONES.items() if v.ultima_actividad < tope]:
        SESIONES.pop(sid, None)


async def manejar(sid, texto, datos, canal="web", telefono=""):
    s = SESIONES.get(sid) or Sesion(sid)
    SESIONES[sid] = s
    if canal != "web":
        s.canal = canal
    if telefono:
        s.telefono = telefono

    # TTL: el cliente volvió después de mucho; se retoma desde el menú.
    if s.paso != "inicio" and reloj.ahora() - s.ultima_actividad > timedelta(minutes=config.SESION_TTL_MIN):
        s.paso, s.servicio_id, s.slot, s.folio, s.nombre = "inicio", None, None, None, ""

    s.ultima_actividad = reloj.ahora()
    s.mensajes += 1
    if len(SESIONES) > 500:
        _purgar_sesiones()
    ...
```

`manejar` conserva el resto del cuerpo tal cual; los parámetros nuevos son keyword con default, así que los llamadores actuales (`app/main.py`) siguen funcionando sin cambios.

**Verificación:**

```bash
.venv/Scripts/python.exe -m pytest tests/test_engine.py -q        # "16 passed"
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
# esperado: "88 passed"
```

**Commit:** `feat: canal, telefono y expiración de sesión en el motor`

---

### Tarea 13 — Motor II: el teléfono como candado del folio (20 min)

**Por qué:** en WhatsApp el folio se puede adivinar (4 caracteres de un alfabeto de 32: ~1M de combinaciones, pero se dicta en voz alta y se comparte). Si el folio es la única llave, cualquiera cancela la cita de cualquiera. En canal `wa` se exige además que el teléfono de la cita coincida con el de la sesión.

**Regla:** comparar los **últimos 10 dígitos** (`_t10()` en `app/whatsapp_formato.py`). Si no coincide, la respuesta es **idéntica** a la de folio inexistente: no se revela que la cita existe. En canal `web` (teléfono de la cita vacío) se mantiene el comportamiento actual.

**Test primero** — `tests/test_engine.py` (3 tests nuevos, archivo en 19):

```python
def _cita_ajena(folio="AMB-7K4Q", telefono="5215500000000"):
    return store.crear(slot="2026-11-10T10:00", servicio_id="manicura", nombre="Otro",
                       telefono=telefono)


def test_wa_no_cancela_cita_de_otro_telefono():
    cita = _cita_ajena()

    resp, _ = asyncio.run(engine.manejar("w1", f"cancelar {cita['folio']}", DATOS,
                                         canal="wa", telefono="5215500000011"))

    assert store.obtener(cita["folio"])["estado"] == "confirmada"
    assert "no encuentro" in " ".join(r["texto"] for r in resp).lower()


def test_wa_cancela_su_propia_cita_aunque_llegue_con_521():
    cita = _cita_ajena(telefono="5215500000011")

    asyncio.run(engine.manejar("w2", f"cancelar {cita['folio']}", DATOS,
                               canal="wa", telefono="525500000011"))

    assert store.obtener(cita["folio"])["estado"] == "cancelada"


def test_web_sigue_cancelando_por_folio():
    cita = _cita_ajena(telefono="")

    asyncio.run(engine.manejar("web1", f"cancelar {cita['folio']}", DATOS))

    assert store.obtener(cita["folio"])["estado"] == "cancelada"
```

**Fallo esperado:** el primero cancela la cita ajena (falla en el `assert ... == "confirmada"`); los otros dos pasan.

**Implementación:** un solo helper, aplicado en los tres puntos donde hoy se resuelve un folio.

```python
def _folio_autorizado(s: Sesion, cita: dict[str, Any] | None) -> bool:
    """En WhatsApp el folio solo abre la cita del mismo teléfono."""
    if s.canal != "wa" or not cita:
        return True
    return _t10(cita.get("telefono", "")) == _t10(s.telefono)


def _folio_no_encontrado(s: Sesion, datos: dict[str, Any]) -> tuple[list[dict[str, Any]], Sesion]:
    return [_t("No encuentro ninguna cita con ese folio. ¿Me lo repites o te ayudo con otra cosa?")], s
```

En `_cancelar_folio` (380), `_empezar_reagendar` (405) y `_paso_citas_folio` (345): tras `store.obtener(...)`, `if not _folio_autorizado(s, cita): return _folio_no_encontrado(s, datos)`. Las tres funciones ya reciben `s`; no hay que cambiarles la firma.

**Verificación:**

```bash
.venv/Scripts/python.exe -m pytest tests/test_engine.py -q        # "19 passed"
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
# esperado: "91 passed"
```

**Commit:** `fix: el folio solo abre la cita del mismo teléfono en WhatsApp`

---

### Tarea 14 — Motor III: handoff que de verdad entrega la conversación (30 min)

**Por qué:** hoy `_es_handoff` pone `s.handoff = True` y `s.paso = "humano"` (`app/engine.py:190-195`), y ahí termina: nadie se entera y el bot sigue contestando como si nada. Además, como el flag nunca se resetea, el segundo handoff de la misma sesión no registra nada.

Tres piezas: (a) resetear el flag tras avisar; (b) `notificar_handoff(sid, telefono, mensaje)` con `log` por defecto y Telegram opcional; (c) en canal `wa`, mientras `paso == "humano"` el bot calla salvo `"menu"`.

**Decisión del dueño del repo:** el medio de aviso. Propuesta: **Telegram** (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`), porque la manicurista ya lo tiene en el teléfono y no cuesta nada. **No** se usa WhatsApp para avisar a la manicurista: fuera de la ventana de 24 h exige plantilla aprobada y complica el arranque.

**Test primero** — `tests/test_engine.py` (3 tests nuevos, archivo en 22):

```python
def test_handoff_avisa_y_resetea_el_flag(monkeypatch):
    avisos = []
    monkeypatch.setattr(engine.avisos, "notificar_handoff",
                        lambda sid, tel, msg: avisos.append((sid, tel, msg)))

    asyncio.run(engine.manejar("h1", "quiero hablar con una persona", DATOS))

    assert len(avisos) == 1
    assert engine.SESIONES["h1"].handoff is False
    assert engine.SESIONES["h1"].paso == "humano"


def test_handoff_se_registra_dos_veces_en_la_misma_sesion(monkeypatch):
    avisos = []
    monkeypatch.setattr(engine.avisos, "notificar_handoff",
                        lambda sid, tel, msg: avisos.append(sid))

    asyncio.run(engine.manejar("h2", "quiero hablar con una persona", DATOS))
    asyncio.run(engine.manejar("h2", "menu", DATOS))
    asyncio.run(engine.manejar("h2", "quiero hablar con una persona", DATOS))

    assert len(avisos) == 2


def test_en_humano_el_bot_calla_salvo_menu():
    asyncio.run(engine.manejar("h3", "quiero hablar con una persona", DATOS,
                               canal="wa", telefono="5215500000011"))

    resp, _ = asyncio.run(engine.manejar("h3", "¿hola?", DATOS,
                                         canal="wa", telefono="5215500000011"))
    assert resp == []

    resp, _ = asyncio.run(engine.manejar("h3", "menu", DATOS,
                                         canal="wa", telefono="5215500000011"))
    assert resp
```

**Fallo esperado:** `AttributeError: module 'app.engine' has no attribute 'avisos'` en los dos primeros; el tercero responde con el menú.

**Implementación** — `app/avisos.py` (archivo nuevo):

```python
"""Avisos a la manicurista. Log por defecto; Telegram si está configurado."""
from __future__ import annotations

import logging

import httpx

from . import config

log = logging.getLogger(__name__)


def _t4(telefono: str) -> str:
    return (telefono or "")[-4:] or "----"


def notificar_handoff(sid: str, telefono: str, mensaje: str) -> None:
    texto = f"Handoff {sid} · cliente …{_t4(telefono)}: {mensaje[:200]}"
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        log.warning(texto)
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": texto},
            timeout=5,
        )
    except httpx.HTTPError:            # nunca tumbar la respuesta al cliente
        log.exception("no se pudo avisar el handoff")
```

`app/config.py`:

```python
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
```

`app/engine.py`:

```python
from . import avisos, calendar_sync, config, llm, reloj, store

    if _es_handoff(txt):
        s.handoff = True
        s.paso = "humano"
        await asyncio.to_thread(avisos.notificar_handoff, sid, s.telefono, texto)
        s.handoff = False
        return [...], s

    # silencio del bot cuando la conversación está en manos de la manicurista
    if s.canal == "wa" and s.paso == "humano" and txt != "menu":
        return [], s
```

(el bloque de silencio va **después** de la salida global de `"menu"`, que ya existe en 186.)

**Verificación:**

```bash
.venv/Scripts/python.exe -m pytest tests/test_engine.py -q        # "22 passed"
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
# esperado: "94 passed"
```

**Commit:** `feat: handoff avisa a la manicurista, resetea el flag y silencia el canal wa`

---

### Tarea 15 — Motor IV: `calendar_sync` fuera del event loop (15 min)

**Por qué:** `app/calendar_sync.py` usa `httpx` **síncrono** (`import httpx` en la línea 17, `crear` en 106, `mover` en 122, `borrar` en 144) y se llama desde dentro de `async def manejar`: cada llamada bloquea el event loop hasta `CALENDAR_TIMEOUT_S` (8 s por defecto). Un cliente lento puede congelar el bot para todos. `calendar_sync.py` **no se modifica**; el cambio es en `engine.py`.

Llamadas a envolver: `calendar_sync.crear` (engine:327), `calendar_sync.borrar` (engine:395) y `calendar_sync.mover` (engine:464).

**Test primero** — `tests/test_engine.py` (2 tests nuevos, archivo en 24):

```python
def test_calendar_corre_en_otro_hilo(monkeypatch):
    hilos = []

    def crear_lento(cita, datos):
        hilos.append(threading.current_thread().name)
        return None

    monkeypatch.setattr(engine.calendar_sync, "crear", crear_lento)
    asyncio.run(_agendar_hasta_confirmar("t1"))

    assert hilos and hilos[0] != threading.main_thread().name


def test_un_calendar_lento_no_bloquea_otra_conversacion(monkeypatch):
    def crear_lento(cita, datos):
        time.sleep(0.3)
        return None

    monkeypatch.setattr(engine.calendar_sync, "crear", crear_lento)

    async def dos_a_la_vez():
        inicio = time.perf_counter()
        await asyncio.gather(_agendar_hasta_confirmar("a"), _agendar_hasta_confirmar("b"))
        return time.perf_counter() - inicio

    assert asyncio.run(dos_a_la_vez()) < 0.5      # secuencial serían 0.6 s
```

`_agendar_hasta_confirmar` es un helper del propio test que recorre servicio → horario → nombre → confirmar; ya existe en el archivo para los tests de agendado, se reutiliza.

**Fallo esperado:** el segundo tarda ~0.6 s y falla el `assert`; el primero ve `MainThread`.

**Implementación:** cambiar las tres llamadas por `await asyncio.to_thread(...)`:

```python
event_id = await asyncio.to_thread(calendar_sync.crear, cita, datos)
await asyncio.to_thread(calendar_sync.borrar, cita.get("calendar_event_id"))
event_id = await asyncio.to_thread(calendar_sync.mover, cita.get("calendar_event_id"), nueva, datos)
```

`import asyncio` ya está en `app/engine.py`.

**Verificación:**

```bash
.venv/Scripts/python.exe -m pytest tests/test_engine.py -q        # "24 passed"
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
# esperado: "96 passed"
```

**Commit:** `perf: llama a calendar_sync en un hilo aparte`

---

### Tarea 16 — Topes propios del canal WhatsApp (35 min)

**Por qué:** hoy el presupuesto es global (`USO_DIA`, `app/engine.py:50`) y sus textos hablan de demo y de recargar la página (`app/engine.py:179-183`). Con WhatsApp, esos mensajes llegarían a clientes reales, y los mensajes bloqueados seguirían gastando el presupuesto del negocio porque el contador se incrementa **antes** de los gates (174-176).

Cuatro piezas: contador diario propio (`wa_uso`), límite por teléfono por hora, allowlist `WA_ALLOWLIST` (vacía = abierta) y textos propios del canal. `USO_DIA` pasa a incrementarse **después** del gate de sesión.

**Test primero** — `tests/test_wa_limites.py` (archivo nuevo, 5 tests):

```python
def _wa(texto, tel="5215500000011", sid=None):
    return asyncio.run(engine.manejar(sid or f"wa-{tel}", texto, DATOS,
                                      canal="wa", telefono=tel))


def test_textos_propios_del_canal_wa(monkeypatch):
    monkeypatch.setattr(config, "MAX_MENSAJES_POR_SESION", 1)
    resp, _ = _wa("hola")

    texto = " ".join(r["texto"] for r in resp).lower()
    assert "demo" not in texto
    assert "recarga" not in texto


def test_mensaje_bloqueado_no_gasta_presupuesto(monkeypatch):
    monkeypatch.setattr(config, "MAX_MENSAJES_POR_SESION", 1)
    sesenta = engine.SESIONES["wa-x"] = engine.Sesion("wa-x", mensajes=5, canal="wa")
    antes = engine.USO_DIA["n"]

    _wa("hola", sid="wa-x")

    assert engine.USO_DIA["n"] == antes


def test_allowlist_bloquea_telefonos_fuera_de_la_lista(monkeypatch):
    monkeypatch.setattr(config, "WA_ALLOWLIST", {"5215500000011"})

    resp, _ = _wa("hola", tel="5215500000000")

    assert "no puedo" in " ".join(r["texto"] for r in resp).lower()


def test_limite_por_telefono_por_hora(monkeypatch):
    monkeypatch.setattr(config, "WA_MAX_POR_HORA", 2)
    for i in range(2):
        _wa("hola", tel="5215500000011", sid=f"wa-h{i}")

    resp, _ = _wa("hola", tel="5215500000011", sid="wa-h-x")

    assert "espera" in " ".join(r["texto"] for r in resp).lower()


def test_tope_diario_de_whatsapp_es_independiente_del_global(monkeypatch):
    monkeypatch.setattr(config, "WA_MAX_POR_DIA", 1)
    _wa("hola", tel="5215500000011", sid="wa-d1")

    resp, _ = _wa("hola", tel="5215500000022", sid="wa-d2")

    assert engine.USO_DIA["n"] <= config.MAX_MENSAJES_POR_DIA
    assert "mañana" in " ".join(r["texto"] for r in resp).lower()
```

**Fallo esperado:** el primero encuentra "demo"; el segundo ve `USO_DIA["n"]` incrementado; los tres restantes no tienen allowlist ni contadores y responden normal.

**Implementación:**

1. `app/config.py`:

```python
WA_ALLOWLIST = {t for t in os.getenv("WA_ALLOWLIST", "").replace(" ", "").split(",") if t}
WA_MAX_POR_DIA = int(os.getenv("WA_MAX_POR_DIA", "150"))
WA_MAX_POR_HORA = int(os.getenv("WA_MAX_POR_HORA", "12"))
```

2. `app/store.py` (la tabla `wa_uso` se creó en la Tarea 9):

```python
def wa_sumar(telefono: str, momento: str) -> None:
    with _candado, sqlite3.connect(config.DB_FILE) as cx:
        cx.execute("INSERT INTO wa_uso (telefono, momento) VALUES (?, ?)", (telefono, momento))


def wa_contar_desde(desde: str, telefono: str | None = None) -> int:
    q = "SELECT COUNT(*) FROM wa_uso WHERE momento >= ?"
    args: list[Any] = [desde]
    if telefono:
        q += " AND telefono = ?"
        args.append(telefono)
    with _candado, sqlite3.connect(config.DB_FILE) as cx:
        return cx.execute(q, args).fetchone()[0]
```

3. `app/engine.py`, dentro de `manejar`, con este orden exacto:

```python
    # 1. gate de sesión (bloqueado = no gasta nada)
    if s.mensajes > config.MAX_MENSAJES_POR_SESION:
        if s.canal == "wa":
            return [_t("Por hoy ya van muchos mensajes en esta conversación. "
                       "Escríbeme mañana y seguimos.")], s
        return [_t("Esta sesión de demo llegó a su tope de mensajes. Recarga la página...")], s

    if s.canal == "wa":
        tel = _t10(s.telefono)
        if config.WA_ALLOWLIST and tel not in config.WA_ALLOWLIST:
            return [_t("Por ahora no puedo atender este número. Marca al "
                       f"{datos['telefono']} y con gusto te ayudamos.")], s
        ahora_iso = s.ultima_actividad.isoformat()
        if store.wa_contar_desde(_hace_una_hora(), tel) >= config.WA_MAX_POR_HORA:
            return [_t("Me llegaron muchos mensajes seguidos. Espera unos minutos y seguimos.")], s
        if store.wa_contar_desde(_hoy_iso(), tel) >= config.WA_MAX_POR_DIA or \
           store.wa_contar_desde(_hoy_iso()) >= config.WA_MAX_POR_DIA * 5:
            return [_t("Hoy ya no alcanzo a contestar más por este medio. "
                       f"Escríbeme mañana o marca al {datos['telefono']}.")], s
        store.wa_sumar(tel, ahora_iso)
        return await _flujo(s, texto, txt, datos)

    # 2. recién ahora el presupuesto global (los bloqueados no lo consumen)
    if USO_DIA["fecha"] != reloj.ahora().date():
        USO_DIA.update(fecha=reloj.ahora().date(), n=0)
    USO_DIA["n"] += 1
    if USO_DIA["n"] > config.MAX_MENSAJES_POR_DIA:
        return [_t("La demo llegó a su límite de mensajes por hoy...")], s
    return await _flujo(s, texto, txt, datos)
```

`_flujo(...)` es un renombre del cuerpo actual de `manejar` desde la línea 185 (salidas globales) hasta el final: misma lógica, sin cambios, para que los gates queden arriba y legibles. `_hace_una_hora()` y `_hoy_iso()` son dos helpers de tres líneas en `engine.py` con `reloj.ahora()`.

**Verificación:**

```bash
.venv/Scripts/python.exe -m pytest tests/test_wa_limites.py -q     # "5 passed"
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -2
# esperado: "101 passed"
```

**Commit:** `feat: topes, allowlist y textos propios del canal WhatsApp`

---

### Tarea 17 — Procesar un evento de punta a punta (5 min)

**Test — añade:**

```python
class _Enviador:
    def __init__(self):
        self.salida: list[tuple[str, dict]] = []

    async def __call__(self, wa_id: str, payload: dict) -> bool:
        self.salida.append((wa_id, payload))
        return True


def _cuerpo(wa_id: str, payload: dict) -> str:
    if payload["type"] == "text":
        return payload["text"]["body"]
    return json.dumps(payload["interactive"], ensure_ascii=False)


def test_procesar_responde_faq_y_manda_al_telefono(bd):
    enviador = _Enviador()
    n = asyncio.run(whatsapp_webhook.procesar_evento(_payload("precios"), enviador))
    assert n == 1
    wa_id, payload = enviador.salida[0]
    assert wa_id == "5215500000001"
    assert "450" in _cuerpo(wa_id, payload)


def test_procesar_usa_una_sesion_por_telefono(bd):
    enviador = _Enviador()
    asyncio.run(whatsapp_webhook.procesar_evento(_payload("hola", "wamid.a"), enviador))
    asyncio.run(whatsapp_webhook.procesar_evento(_payload("precios", "wamid.b"), enviador))
    assert list(engine.SESIONES) == ["wa:5215500000001"]


def test_procesar_ignora_el_mensaje_repetido(bd):
    enviador = _Enviador()
    primero = asyncio.run(whatsapp_webhook.procesar_evento(_payload("precios", "wamid.x"), enviador))
    segundo = asyncio.run(whatsapp_webhook.procesar_evento(_payload("precios", "wamid.x"), enviador))
    assert (primero, segundo) == (1, 0)
    assert len(enviador.salida) == 1


def test_procesar_avisa_si_no_es_texto(bd):
    p = _payload()
    p["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "id": "wamid.img", "from": "5215500000001", "type": "image", "image": {"id": "X"},
    }
    enviador = _Enviador()
    asyncio.run(whatsapp_webhook.procesar_evento(p, enviador))
    assert "solo entiendo mensajes de texto" in _cuerpo(*enviador.salida[0])


def test_procesar_guarda_el_telefono_en_la_sesion(bd):
    enviador = _Enviador()
    asyncio.run(whatsapp_webhook.procesar_evento(_payload("hola"), enviador))
    assert engine.SESIONES["wa:5215500000001"].telefono == "5215500000001"
```

(Añade `import asyncio` a la cabecera de `tests/test_wa_webhook.py`.)

Corre: `AttributeError: procesar_evento`.

**Implementación — añade a `app/whatsapp_webhook.py`:**

```python
async def procesar_evento(payload: dict[str, Any], enviar: Enviador | None = None) -> int:
    """Atiende cada mensaje nuevo del payload. Devuelve cuántos mensajes respondió."""
    enviar = enviar or wa_cliente.enviar
    datos = engine.cargar_datos()
    atendidos = 0

    for msg in extraer_mensajes(payload):
        if not store.registrar_evento(msg["id"]):
            log.info("wa: evento repetido %s (reintento de Meta)", msg["id"])
            continue

        sid = f"wa:{msg['wa_id']}"
        sesion = engine.SESIONES.get(sid)
        if sesion is None:
            sesion = engine.Sesion(sid)
            engine.SESIONES[sid] = sesion
        sesion.telefono = msg["wa_id"]
        ya_pidio_humano = sesion.handoff

        if not msg["texto"]:
            await _enviar(msg["wa_id"], [{"tipo": "texto", "texto": AVISO_SIN_TEXTO}], enviar)
            atendidos += 1
            continue

        respuestas, sesion = await engine.manejar(sid, msg["texto"], datos, canal="wa")

        if sesion.handoff and not ya_pidio_humano:
            store.registrar_handoff(sid, msg["texto"])
            log.warning("wa: handoff registrado para %s", sid)

        await _enviar(msg["wa_id"], respuestas, enviar)
        atendidos += 1

    return atendidos


async def _enviar(wa_id: str, respuestas: list[dict[str, Any]], enviar: Enviador) -> None:
    for payload in whatsapp_formato.a_payloads(respuestas):
        await enviar(wa_id, payload)
```

Verificación: `13 passed` (los 8 de antes + 5 nuevos).
Commit: `feat: procesamiento de eventos de WhatsApp contra el motor`.

---

**Prueba temprana con un celular mexicano real (antes de seguir con las rutas):**

1. En Meta → *WhatsApp → API Setup*, agrega **tu propio celular mexicano** a la lista de destinatarios de prueba. El número debe estar dado de alta en WhatsApp; el país y el formato del formulario de Meta son la fuente de verdad, no este plan.
2. Manda `hola` desde ese celular y copia de los logs el `wa_id` tal como llegó. **VERIFICAR:** Meta ha entregado números de México como `521...` (13 dígitos, con el `1` de móvil) y también como `52...` (12 dígitos). La diferencia cambia el string que se guarda y al que se responde.
3. Con ese `wa_id` real, corre `WA_MODO=consola` y confirma dos cosas: el bot responde **al mismo `wa_id`** que llegó (no a uno reconstruido) y la comparación de folios funciona (Tarea 13 normaliza a los últimos 10 dígitos, así que `521...` y `52...` coinciden).
4. Anota el valor observado en el README o en el ticket de despliegue. El código no debe fijar ninguno de los dos formatos: guarda y responde el `wa_id` exacto.

Esto no agrega tests: es una comprobación manual con el único número que de verdad existe.

---

### Tarea 18 — Las dos rutas HTTP (5 min)

**Test — añade:**

```python
def _cliente():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_webhook_get_devuelve_el_challenge(monkeypatch):
    monkeypatch.setattr(config, "WA_VERIFY_TOKEN", "prueba")
    r = _cliente().get("/wa/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "prueba", "hub.challenge": "12345",
    })
    assert r.status_code == 200
    assert r.text == "12345"


def test_webhook_get_rechaza_token_incorrecto(monkeypatch):
    monkeypatch.setattr(config, "WA_VERIFY_TOKEN", "prueba")
    r = _cliente().get("/wa/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "12345",
    })
    assert r.status_code == 403


def test_webhook_post_sin_firma_devuelve_403(bd, monkeypatch):
    monkeypatch.setattr(config, "WA_MODO", "consola")
    monkeypatch.setattr(config, "WA_VERIFY_TOKEN", "prueba")
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")
    r = _cliente().post("/wa/webhook", content=json.dumps(_payload("hola")))
    assert r.status_code == 403


def test_webhook_post_con_firma_responde_200(bd, monkeypatch):
    monkeypatch.setattr(config, "WA_MODO", "consola")
    monkeypatch.setattr(config, "WA_VERIFY_TOKEN", "prueba")
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")
    cuerpo = json.dumps(_payload("precios")).encode()
    r = _cliente().post(
        "/wa/webhook", content=cuerpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _firmar(cuerpo)},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert store.registrar_evento("wamid.1") is False  # ya estaba registrado
```

> Importante: importa `app.main` **dentro** de `_cliente()` (como arriba), no en la cabecera del archivo. Al importarse corre `store.init()` y el seed, y así lo hace contra la base temporal que puso la fixture `bd`.

Corre: `404` en las cuatro. **Implementación — añade a `app/whatsapp_webhook.py`:**

```python
@router.get("/wa/webhook")
def verificar(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
) -> PlainTextResponse:
    """Meta llama aquí una vez, al guardar la URL del webhook en la app."""
    if (
        hub_mode == "subscribe"
        and config.WA_VERIFY_TOKEN
        and hmac.compare_digest(hub_token, config.WA_VERIFY_TOKEN)
    ):
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verificación fallida.")


@router.post("/wa/webhook")
async def recibir(request: Request, tareas: BackgroundTasks) -> dict[str, bool]:
    """Meta exige 200 en menos de 5 s: se contesta y el trabajo va en segundo plano."""
    if not config.wa_habilitado():
        raise HTTPException(status_code=503, detail="WhatsApp no configurado.")
    cuerpo = await request.body()
    if not firma_valida(cuerpo, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(status_code=403, detail="Firma inválida.")
    try:
        payload = json.loads(cuerpo or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="JSON inválido.") from exc
    tareas.add_task(_procesar_seguro, payload)
    return {"ok": True}


async def _procesar_seguro(payload: dict[str, Any]) -> None:
    try:
        await procesar_evento(payload)
    except Exception:  # noqa: BLE001 - un fallo aquí no debe tumbar el webhook
        log.exception("wa: fallo procesando el evento")
```

**Montaje — `app/main.py`:** cambia el import (línea 12) y añade el router después de `app = FastAPI(...)`:

```python
from . import config, engine, seed, store, whatsapp_webhook
...
app.include_router(whatsapp_webhook.router)
```

Verificación:

```bash
.venv/Scripts/python.exe -m pytest tests/test_wa_webhook.py -q | tail -2
# esperado: "17 passed"
```

Commit: `feat: rutas GET y POST del webhook de WhatsApp`.

---

### Tarea 19 — Orden garantizado por teléfono (5 min)

Dos mensajes del mismo cliente pueden llegar en dos webhooks distintos y procesarse en paralelo; como `BackgroundTasks` no garantiza orden, el bot podría contestar “nombre” antes que “servicio”. Un candado por teléfono lo resuelve con 8 líneas.

**Test — añade:**

```python
def test_hay_un_candado_por_telefono():
    a = whatsapp_webhook.candado("5215500000001")
    b = whatsapp_webhook.candado("5215500000001")
    c = whatsapp_webhook.candado("5215500000002")
    assert a is b
    assert a is not c
```

**Implementación — `app/whatsapp_webhook.py`:** añade `import asyncio` con los demás imports de la cabecera (orden alfabético: antes de `hashlib`) y luego esto al final del archivo:

```python
_CANDADOS: dict[str, asyncio.Lock] = {}


def candado(wa_id: str) -> asyncio.Lock:
    """Un lock por teléfono: serializa los mensajes de un mismo cliente."""
    if wa_id not in _CANDADOS:
        _CANDADOS[wa_id] = asyncio.Lock()
    return _CANDADOS[wa_id]
```

y dentro del bucle de `procesar_evento`, envuelve el cuerpo por mensaje:

```python
    for msg in extraer_mensajes(payload):
        async with candado(msg["wa_id"]):
            ...  # todo el cuerpo del mensaje, tal cual está
```

(Ojo con la indentación: el `continue` de los repetidos y el `continue` de los no-texto quedan dentro del `async with`.)

Verificación: `18 passed` en `tests/test_wa_webhook.py`.
Commit: `feat: serializa los mensajes de un mismo teléfono`.

---

### Tarea 20 — Script de simulación local (5 min)

Sin cuenta de Meta no hay webhook real. Este script firma un payload como lo haría Meta y lo manda al servidor local.

**`scripts/wa_simula.py`:**

```python
"""Simula un webhook de Meta contra el servidor local (sin cuenta de Meta).

Uso:
    WA_MODO=consola WA_VERIFY_TOKEN=prueba WA_APP_SECRET=prueba \
        .venv/Scripts/python.exe -m uvicorn app.main:app --port 8077
    .venv/Scripts/python.exe scripts/wa_simula.py "precios"
    .venv/Scripts/python.exe scripts/wa_simula.py "agendar"
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import httpx

BASE = os.getenv("WA_BASE", "http://127.0.0.1:8077")
SECRETO = os.getenv("WA_APP_SECRET", "prueba")
TELEFONO = os.getenv("WA_TELEFONO", "5215500000001")


def main() -> int:
    texto = " ".join(sys.argv[1:]).strip() or "menu"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "0",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "messages": [{
                        "id": f"wamid.{uuid.uuid4().hex}",
                        "from": TELEFONO,
                        "timestamp": str(int(time.time())),
                        "type": "text",
                        "text": {"body": texto},
                    }],
                },
            }],
        }],
    }
    cuerpo = json.dumps(payload).encode()
    firma = "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()
    r = httpx.post(
        f"{BASE}/wa/webhook",
        content=cuerpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": firma},
        timeout=20,
    )
    print(r.status_code, r.text)
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

**Prueba manual (la salida esperada es literal):**

```bash
# terminal 1
WA_MODO=consola WA_VERIFY_TOKEN=prueba WA_APP_SECRET=prueba \
  .venv/Scripts/python.exe -m uvicorn app.main:app --port 8077
# terminal 2
.venv/Scripts/python.exe scripts/wa_simula.py "precios"
# esperado: 200 {"ok":true}
```

En la terminal 1 debe aparecer una línea con `[wa:consola] -> 5215500000001 {'messaging_product': 'whatsapp', ..., 'text': {'body': 'Manicura + pedicura ... $450 ...'}}`.

Repite con `"agendar"` y verás un payload con `'type': 'interactive'` y `'type': 'list'` (5 opciones del menú → lista).

Commit: `chore: script para simular webhooks de Meta en local`.

---

### Tarea 21 — Documentación y blueprint (5 min)

**`.env.example`** — añade al final:

```bash
# ---------- WhatsApp Cloud API (opcional; sin esto el canal queda apagado) ----------
# WA_MODO=consola imprime los envíos en el log en vez de llamar a Meta.
WA_MODO=graph
WA_VERIFY_TOKEN=
WA_APP_SECRET=
WA_TOKEN=
WA_PHONE_ID=
WA_API_VERSION=v26.0            # VERIFICAR: versión vigente en el changelog de Meta
MAX_MENSAJES_POR_SESION_WA=500
# ---------- Seguridad y operación (tareas nuevas) ----------
# Token de administración para GET /api/citas. Vacío = ruta deshabilitada (503).
ADMIN_TOKEN=
# Expiración de sesión en minutos (vuelve a "inicio" y purga SESIONES viejas).
SESION_TTL_MIN=30
# Aviso de handoff a la manicurista: sin estas dos, solo se escribe en el log.
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
# Allowlist de WhatsApp por últimos 10 dígitos, separados por coma. Vacía = abierto.
WA_ALLOWLIST=
WA_MAX_POR_DIA=150
WA_MAX_POR_HORA=12
```

**`render.yaml`** — dentro de `envVars`, añade (mismo estilo que las existentes, `sync: false` para los secretos):

```yaml
      - key: WA_MODO
        value: graph
      - key: WA_VERIFY_TOKEN
        sync: false
      - key: WA_APP_SECRET
        sync: false
      - key: WA_TOKEN
        sync: false
      - key: WA_PHONE_ID
        sync: false
      - key: ADMIN_TOKEN
        sync: false
      - key: TELEGRAM_BOT_TOKEN
        sync: false
      - key: TELEGRAM_CHAT_ID
        sync: false
      - key: WA_API_VERSION
        value: v26.0
      - key: MAX_MENSAJES_POR_SESION_WA
        value: "500"
```

**`README.md`** — añade una sección antes de la de variables de entorno:

```markdown
## Canal WhatsApp (opcional)

El motor es el mismo del chat web; el adaptador vive en `app/whatsapp_webhook.py`
y se puede probar sin cuenta de Meta:

```bash
WA_MODO=consola WA_VERIFY_TOKEN=prueba WA_APP_SECRET=prueba \
  uvicorn app.main:app --port 8077
python scripts/wa_simula.py "precios"
```

Con credenciales reales: webhook `https://<host>/wa/webhook`, campos `messages`,
y las variables `WA_VERIFY_TOKEN`, `WA_APP_SECRET`, `WA_TOKEN`, `WA_PHONE_ID`.
```

Verificación:

```bash
.venv/Scripts/python.exe -c "import yaml,sys; yaml.safe_load(open('render.yaml'))" && echo YAML_OK
# esperado: YAML_OK
```

Commit: `docs: variables y documentación del canal WhatsApp`.

---

### Tarea 22 — Suite completa y push (5 min)

```bash
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py | tail -3
# esperado: "111 passed" (o más), 0 failed
git status --short
git add -A
git commit -m "feat: adaptador y webhook de WhatsApp Cloud API"
export GH_TOKEN=$("/c/Program Files/GitHub CLI/gh.exe" auth token)
git -c credential.helper= -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f' push -u origin feat/whatsapp-meta
```

Abre el PR:

```bash
"/c/Program Files/GitHub CLI/gh.exe" pr create --fill --base main --head feat/whatsapp-meta
```

---

### Tarea 23 — Cuando lleguen las credenciales (bloque separable, sin código)

Esto lo hace el dueño del repo, no el implementador. No hay nada que programar.

1. Meta for Developers → tu app → *WhatsApp* → *API Setup*: apunta **Phone number ID** y el **token** (el temporal dura 24 h; genera uno permanente con un usuario de sistema).
2. *App settings → Basic*: apunta **App secret**.
3. Inventa un `WA_VERIFY_TOKEN` (cualquier cadena secreta, p. ej. 32 caracteres aleatorios).
4. En Render → tu servicio → *Environment*: pega `WA_VERIFY_TOKEN`, `WA_APP_SECRET`, `WA_TOKEN`, `WA_PHONE_ID`; deja `WA_MODO=graph`. Guarda y espera el redeploy.
5. Comprueba que el proceso nuevo está vivo: `curl -s https://<host>/health` → el `commit` debe ser el del merge (no el anterior).
6. Meta → *WhatsApp → Configuration → Webhook*: URL `https://<host>/wa/webhook`, verify token = el del paso 3, *Verify and save*. Debe decir **verified** al instante; si falla, mira los logs de Render (403 = token distinto, 503 = faltan variables).
7. Suscríbete al campo **messages** en esa misma pantalla.
8. *WhatsApp Manager → Configuración de la cuenta → Método de pago*: agrega tarjeta. Sin método de pago la cuenta queda limitada y los envíos se cortan al llegar al cupo de prueba.
9. *Meta Business Suite → Centro de seguridad → Verificación del negocio*: inicia la verificación (documentos fiscales del negocio). Puede tardar días; empieza antes de la fecha de corte, no el mismo día.
10. *App settings → Basic → App Mode*: pasa la app de **Development** a **Live**. Con la app en Development solo reciben mensajes los números de la lista de prueba.
11. **VERIFICAR** tarifas de México en la página oficial de Meta antes de activar: desde julio de 2025 el cobro es **por mensaje**, y desde el 1 de octubre de 2026 los mensajes de servicio y las plantillas de utilidad dentro de la ventana de 24 h también se cobran, con 1,000 mensajes de servicio gratis al mes por número. No fijes cifras en el README sin volver a comprobarlo.
12. Manda un “hola” desde tu teléfono al número de prueba y mira los logs de Render: `[wa]` sólo si `WA_MODO=consola`; en `graph` no hay log de éxito, así que confirma que llegó la respuesta al WhatsApp.

**Aviso que hay que darle al cliente/lector del README:** con el plan free de Render el servicio duerme a los 15 min y el disco es efímero; un canal real necesita plan pago con disco persistente (o Postgres). El webhook perderá mensajes mientras duerme.

---

## 7. Validación

### 7.1 Comandos

```bash
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/smoke_test.py
.venv/Scripts/python.exe -m pytest tests/ -q --collect-only --ignore=tests/smoke_test.py | tail -1
.venv/Scripts/python.exe -m ruff check app tests            # si el repo usa ruff
```

### 7.2 Conteos acumulados reales

Base medida con `--collect-only` sobre la rama `main`: **55 tests**. Cada fila suma solo los `def test_` que esa tarea define en el texto. Ojo con las dos convenciones: en las Tareas 5–11 la línea «Verificación» da el total **del archivo** de pruebas (así venía en v1); la columna «Acumulado» es siempre la **suite completa**, que es la que exige el gate de la Tarea 22.

| Tarea | Qué agrega | Tests nuevos | Acumulado |
|---|---|---|---|
| 0 | estabiliza tests flaky | 0 | 55 |
| 1 | rama de trabajo | 0 | 55 |
| 2 | configuración | 0 | 55 |
| 3 | `app/reloj.py` | 3 | 58 |
| 4 | `ADMIN_TOKEN` en `/api/citas` | 4 | 62 |
| 5 | `app/whatsapp_formato.py` | 1 | 63 |
| 6 | botones y plantillas de texto | 2 | 65 |
| 7 | recortes de payload | 4 | 69 |
| 8 | `app/wa_cliente.py` | 3 | 72 |
| 9 | fixture `bd` + `wa_eventos` | 3 | 75 |
| 10 | verificación de firma | 4 | 79 |
| 11 | lectura del payload | 4 | 83 |
| 12 | canal, teléfono, TTL | 5 | 88 |
| 13 | candado del folio | 3 | 91 |
| 14 | handoff real | 3 | 94 |
| 15 | `calendar_sync` en hilo | 2 | 96 |
| 16 | topes de WhatsApp | 5 | 101 |
| 17 | e2e del webhook | 5 | 106 |
| 18 | rutas HTTP | 4 | 110 |
| 19 | orden por teléfono | 1 | 111 |
| 20–23 | script, docs, suite y push, producción | 0 | 111 |

Si algún test nuevo resulta parametrizado, el número real lo manda `--collect-only`; el plan no estima, cuenta.

### 7.3 Gate de salida

- Suite en verde: **111 passed, 0 failed**, sin `-k` ni exclusiones fuera de `tests/smoke_test.py`.
- `git diff --stat` contra `main` limitado a: `app/engine.py`, `app/store.py`, `app/main.py`, `app/config.py`, `app/reloj.py`, `app/avisos.py`, `app/whatsapp_formato.py`, `app/wa_cliente.py`, `app/wa_eventos.py`, `tests/`, `docs/`, `.env.example`.
- `app/calendar_sync.py` y `app/slots.py` sin cambios.
- `git grep -n "5215500000011\|TELEGRAM_BOT_TOKEN=" -- ':!*.example'` vacío: no hay teléfonos ni tokens en el código.
- `GET /api/citas` sin `ADMIN_TOKEN` configurado devuelve 503; con cabecera distinta, 401; con cabecera correcta, citas sin `telefono` ni `calendar_event_id`.
- Un payload de webhook con acentos y firma inválida devuelve 403 (antes: 500).

---
## 8. Riesgos, tradeoffs y preguntas abiertas

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Firma del webhook comparada como `str` | `hmac.compare_digest` lanza `TypeError` con no ASCII: 500 en vez de 403, y el 500 se reintenta | Comparar bytes (`.encode()`) en `firma_valida` y en `verificar`; test con acentos incluido |
| `calendar_sync` con `httpx` síncrono dentro de `async def manejar` | Una llamada lenta (hasta `CALENDAR_TIMEOUT_S`, 8 s) congela el bot para todos los clientes | `asyncio.to_thread` en crear, mover y borrar; test de dos conversaciones concurrentes |
| `GET /api/citas` público con `SELECT *` | Expone teléfonos y `calendar_event_id` de clientes reales | `ADMIN_TOKEN` por cabecera (503/401) y campos sensibles fuera de la respuesta |
| Handoff que solo escribe en una tabla | El manicurista nunca se entera y el bot sigue contestando | `notificar_handoff` (log/Telegram), reset del flag y silencio en `humano` |
| Folio como única llave en WhatsApp | Cualquiera cancela la cita de otro adivinando o copiando un folio | Exigir coincidencia de teléfono (últimos 10 dígitos) y respuesta indistinguible |
| Tope diario global compartido con la demo | Los clientes de WhatsApp reciben textos de "demo" y "recarga la página" | Contadores `wa_uso` propios, allowlist y textos del canal |
| Mensajes bloqueados consumiendo presupuesto | El tope se agota sin haber atendido a nadie | `USO_DIA` se incrementa después del gate de sesión y de los gates de WhatsApp |
| Registrar el evento antes de procesarlo | Entrega "como máximo una vez": si el envío falla no hay reintento | Aceptado para v1; documentado en la Tarea 17. La alternativa (marcar al confirmar el envío) reabre el doble procesamiento |
| Horarios: los 8 del motor caen casi todos en el primer día | El cliente ve una lista sesgada y "otra fecha" | El motor ya limita a 8 + "otra fecha"; el **selector de día** queda como pendiente explícito, fuera de este plan |
| Zona horaria del servidor | Render corre en UTC: slots, anticipación mínima y ventana de cancelación usan la hora equivocada | `app/reloj.py` con `ZoneInfo(config.CALENDAR_TZ)` y reemplazo de los seis `datetime.now()` |
| Teléfono en logs | Fuga de datos personales en los logs del hosting | Solo últimos 4 dígitos en cualquier log o aviso |
| Formato de números de México (521 vs 52) | Comparaciones que fallan y mensajes a un `wa_id` equivocado | Guardar y responder al `wa_id` exacto; comparar por últimos 10 dígitos; **VERIFICAR** el comportamiento real con un celular mexicano antes de la Tarea 17 |
| Costos: desde julio de 2025 se cobra por mensaje y desde el 1 de octubre de 2026 también los de servicio y plantillas de utilidad dentro de la ventana, con 1,000 mensajes de servicio gratis al mes por número | Factura inesperada en el primer mes real | **VERIFICAR** tarifas de México en la página oficial de Meta antes de la Tarea 23; activar método de pago y alertas |
| `WA_API_VERSION` desactualizada (v1 usaba `v21.0`) | La API rechaza llamadas o se rompe en silencio | Default vigente y configurable por entorno; **VERIFICAR** en el changelog oficial |
| Datos personales sin aviso de privacidad | El negocio guarda nombre y teléfono sin base legal documentada | Aviso de privacidad (LFPDPPP) visible antes de pedir el nombre; **validar con un especialista** |
| Sesión que nunca expira | Clientes que vuelven días después retoman un paso a medias; `SESIONES` crece sin límite | `SESION_TTL_MIN` (30) y purga de sesiones viejas |

### 8.1 Decisiones abiertas (dueño del repo)

1. Medio de aviso del handoff: propuesta Telegram (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`); alternativas: correo o WhatsApp con plantilla aprobada.
2. `ADMIN_TOKEN` en `/api/citas`: si el panel `static/index.html` debe seguir leyendo la lista, hay que darle el token (o moverlo a una ruta con sesión).
3. Allowlist vacía = canal abierto: decidir si en producción arranca cerrado o abierto.
4. Umbrales: `WA_MAX_POR_DIA` (150) y `WA_MAX_POR_HORA` (12) son puntos de partida, no medidas.
5. Aviso de privacidad: redacción y responsable, antes de la primera cita real.

### 8.2 Pendientes fuera de este plan

- Selector de día en el flujo de horarios (hoy: 8 horarios casi todos del primer día).
- Reintento de envíos fallidos ("al menos una vez") con cola e idempotencia.
- Panel web con autenticación propia.

### 8.3 Criterios de aceptación

La v1 decía "los cuatro" y listaba cinco. Son estos, más los nuevos:

1. La suite completa pasa: **111 passed, 0 failed**.
2. `POST /api/whatsapp/webhook` responde 200 con payload válido y 403 con firma inválida (incluido payload no ASCII).
3. Un mensaje de texto de WhatsApp recorre el flujo completo: servicio → horario → nombre → confirmación con folio, y la cita queda en la base.
4. `GET /api/whatsapp/webhook` responde el `hub.challenge` cuando el `verify_token` coincide y 403 cuando no.
5. `GET /api/citas` sin `ADMIN_TOKEN` configurado responde 503; con token distinto, 401; con token correcto, lista sin `telefono` ni `calendar_event_id`.
6. En canal `wa`, `cancelar`, `mover` y consulta de folio exigen que el teléfono coincida; si no, la respuesta es la misma que para un folio inexistente.
7. Los clientes de WhatsApp reciben textos del canal (sin "demo" ni "recarga la página") y los topes diario y por hora se aplican por separado.
8. Un handoff avisa a la manicurista, resetea el flag y deja al bot en silencio salvo `"menu"`.
9. Las tres llamadas a `calendar_sync` no bloquean el event loop.
10. Ningún log ni aviso contiene el teléfono completo.