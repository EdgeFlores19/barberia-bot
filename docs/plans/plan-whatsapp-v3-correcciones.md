# Plan v3 — Correcciones al plan v2 (adaptador de WhatsApp)

> Fecha: 2026-10-30. Base: `adaptador-whatsapp-meta-v2.md`.
> **Este documento manda sobre v2.** Donde v2 y v3 se contradicen, se aplica v3. Lo que v3 no menciona queda como en v2.
> Léelo junto con `AGENTS.md` (reglas de seguridad y de trabajo).

## 0. Cómo usar este documento

1. Lee `AGENTS.md`, luego v2 completo, luego esta lista de correcciones (C1–C15).
2. Cada corrección dice **qué tarea de v2 afecta**. Cuando llegues a esa tarea, aplica la versión de v3.
3. **No edites el archivo v2.** Si el dueño pide un v3 fusionado, genéralo al final a partir de esta lista.
4. Las cifras de "N passed" de v2 ya no son fiables. Calcula el esperado contando con `--collect-only` y repórtalo (ver C16).
5. Lo que dependa de documentación de Meta que no puedas confirmar se marca **VERIFICAR**.

## 1. Orden de ejecución v3

| Paso | Qué | Dónde |
|---|---|---|
| H1 | **Hotfix `/api/citas`** (C1): rama aparte desde `main`, PR, merge y deploy | `fix/api-citas-admin` |
| 0 | Preflight (baseline **59 passed** si H1 ya está en `main`) y Tarea 0 de v2 | `main` |
| 1 | Tarea 1 de v2: crear `feat/whatsapp-meta` desde `main` ya actualizado | — |
| 2 | Tarea 2 de v2 + C13 (config) | `feat/…` |
| 3 | Tarea 3 de v2 (`reloj`) + C2 (`tzdata`) | `feat/…` |
| 4 | **Tarea 4 de v2 se omite** (ya está en `main` por H1) | — |
| 5 | Tareas 5–23 de v2, con las correcciones C3–C15 donde correspondan | `feat/…` |

Razón del orden: `/api/citas` hoy expone nombres, teléfonos y folios de la demo pública. No debe esperar a que termine un PR de 23 tareas.

---

## 2. Correcciones

### C1 — Hotfix de `/api/citas` en `main` (sustituye a la Tarea 4 de v2)

**Rama:** `fix/api-citas-admin` desde `main`.

Implementa exactamente la Tarea 4 de v2 (`ADMIN_TOKEN`, 503 si falta, 401 si no coincide, respuesta sin `telefono` ni `calendar_event_id`, actualizar `tests/smoke_test.py` y `.env.example`), con estos cambios:

- La fixture `bd` del archivo `tests/test_api_citas.py` usa la versión de **C5** (desactiva el seed).
- Los teléfonos de los tests son ficticios (**C4**).
- Verificación: `4 passed` en el archivo y **59 passed** en la suite (55 + 4).
- Commit: `fix: protege GET /api/citas con ADMIN_TOKEN y oculta datos personales`.

**Después de mergear (lo hace el dueño):**

1. En Render define `ADMIN_TOKEN` con una cadena aleatoria de 32+ caracteres.
2. Comprueba: `curl -i https://navajazo-bot.onrender.com/api/citas` → `401` (con token definido) o `503` (sin él). Nunca `200`.
3. Vuelve a `main` actualizado y crea ahí `feat/whatsapp-meta`.

### C2 — `tzdata` (Tarea 3 de v2)

`zoneinfo` no trae la base de zonas horarias en Windows. Sin el paquete `tzdata`, `ZoneInfo("America/Mexico_City")` falla en la PC de desarrollo.

- Agrega `tzdata` a `requirements.txt`. **Es la única dependencia nueva autorizada** (v2 decía "no agregues dependencias").
- En `tests/test_engine.py`, el helper `_primer_slot` de la Tarea 0 pasa a usar `reloj.ahora()` en lugar de `datetime.now()` (agrega `from app import reloj`). Hazlo en la Tarea 3, cuando `reloj` ya existe.

### C3 — `app/telefono.py` (Tareas 5–7, 13 y 16 de v2)

v2 define `_t10` dentro de `whatsapp_formato.py` y el motor lo importa de ahí. Eso invierte la dependencia (el motor no debe conocer el adaptador) y puede crear imports circulares. Crea un módulo puro:

```python
"""Normalización de teléfonos. Módulo puro: lo importan el motor y el adaptador."""
from __future__ import annotations


def _digitos(texto: str | None) -> str:
    return "".join(ch for ch in (texto or "") if ch.isdigit())


def t10(texto: str | None) -> str:
    """Últimos 10 dígitos: '5215500000011' y '525500000011' dan '5500000011'."""
    return _digitos(texto)[-10:]


def t4(texto: str | None) -> str:
    """Últimos 4 dígitos, para logs."""
    return _digitos(texto)[-4:] or "----"
```

- Elimina `_t10` de `whatsapp_formato.py` y usa `from .telefono import t10`. El motor hace lo mismo.
- `avisos.py` usa `t4` de este módulo (se elimina `_t4`).

**Tests** — `tests/test_telefono.py` (3 tests):

```python
from __future__ import annotations

from app.telefono import t4, t10


def test_t10_iguala_formatos_con_y_sin_el_1():
    assert t10("5215500000011") == t10("525500000011") == "5500000011"


def test_t10_ignora_signos_y_vacios():
    assert t10("+52 55 0000 0011") == "5500000011"
    assert t10("") == "" and t10(None) == ""


def test_t4_devuelve_los_ultimos_cuatro():
    assert t4("5215500000011") == "0011"
    assert t4("") == "----"
```

### C4 — Teléfonos ficticios y gate de privacidad (Tareas 4, 12, 13, 14, 16 y sección 7.3)

Las pruebas de v2 contienen un número de 13 dígitos con lada real (449…) que parece de una persona. **No debe llegar a ningún archivo del repo.**

- Crea `tests/telefonos.py`:

```python
"""Teléfonos ficticios para los tests. Nunca uses números reales."""
TEL_A = "5215500000011"
TEL_B = "5215500000022"
TEL_C = "5215500000033"
TEL_A_SIN_1 = "525500000011"    # mismo teléfono que TEL_A, formato de 12 dígitos
```

- En **todas** las pruebas de las tareas 4, 12, 13, 14 y 16, sustituye cualquier teléfono de 13 dígitos con lada real (y `5215500000000`, `5215500000001` si estorban) por `TEL_A`/`TEL_B`/`TEL_C`. En las pruebas del webhook de v2 (`5215500000001`) puedes dejar ese valor: ya es ficticio.
- **Gate de 7.3** (sustituye al `git grep` de v2, que fallaba contra las propias pruebas):

```bash
git grep -nE "52(1)?449[0-9]{7}" -- . ':!*.example'     # esperado: vacío
git grep -n "TELEGRAM_BOT_TOKEN=" -- . ':!*.example'    # esperado: vacío
```

- **No copies el archivo v2 al repo** si aún trae el número real. Este v3 sí puede ir a `docs/plans/`.

### C5 — Fixture `bd`: desactivar el seed (Tarea 4 / hotfix y Tarea 9)

Al importar `app.main` por primera vez se ejecuta el seed y mete 2 citas en la base temporal; un test que cuente citas falla según el orden de ejecución. La fixture queda así (en el hotfix, local a `test_api_citas.py`; en la Tarea 9, en `tests/conftest.py`, y entonces se **borra** la copia local):

```python
@pytest.fixture()
def bd(tmp_path, monkeypatch):
    """Base temporal sin seed. Sin esto, importar app.main siembra la BD real."""
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "test.db")
    monkeypatch.setattr(config, "SEED_DEMO", False)
    monkeypatch.setattr(store, "_inicializada", False)
    store.init()
    engine.SESIONES.clear()      # en el hotfix, omite esta línea si no importas engine
    yield
    store.reset()
```

La fixture `cliente` debe depender de `bd` e importar `app.main` **dentro** (después del `monkeypatch`).

### C6 — Comparación en bytes (Tareas 10 y 18 de v2)

El changelog de v2 dice que `firma_valida` y `verificar` comparan bytes, pero el código aún compara `str`: un valor no ASCII lanza `TypeError` y da 500 (que Meta reintenta). Corrige:

```python
def firma_valida(cuerpo: bytes, cabecera: str) -> bool:
    """Valida X-Hub-Signature-256: HMAC-SHA256 del cuerpo crudo con el app secret."""
    if not config.WA_APP_SECRET or not cabecera.startswith("sha256="):
        return False
    esperado = hmac.new(config.WA_APP_SECRET.encode(), cuerpo, hashlib.sha256).hexdigest()
    recibido = cabecera.split("=", 1)[1].strip()
    return hmac.compare_digest(esperado.encode(), recibido.encode())
```

En `verificar`:

```python
        and hmac.compare_digest(hub_token.encode(), config.WA_VERIFY_TOKEN.encode())
```

**Tests nuevos (2):**

Tarea 10, en `tests/test_wa_webhook.py`:

```python
def test_firma_con_caracteres_no_ascii_es_invalida(monkeypatch):
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")

    assert whatsapp_webhook.firma_valida(b"{}", "sha256=ñandú") is False
```

Tarea 18:

```python
def test_webhook_get_con_token_no_ascii_devuelve_403(bd, monkeypatch):
    monkeypatch.setattr(config, "WA_MODO", "consola")
    monkeypatch.setattr(config, "WA_VERIFY_TOKEN", "prueba")
    monkeypatch.setattr(config, "WA_APP_SECRET", "s3cr3t")

    r = _cliente().get(
        "/wa/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "ñandú", "hub.challenge": "x"},
    )

    assert r.status_code == 403
```

### C7 — Sesiones: TTL, contador y conversación con el barbero (Tarea 12 de v2)

Problemas de v2: (a) el TTL de 30 min también reinicia `humano`, y el bot vuelve a hablar encima del barbero; (b) `mensajes` nunca se reinicia, así que un cliente recurrente acumula mensajes hasta quedar bloqueado; (c) la purga puede borrar sesiones `humano` vigentes.

**Config:** `SESION_HUMANO_TTL_MIN = int(os.getenv("SESION_HUMANO_TTL_MIN", "720"))` (12 h).

**Motor:** al inicio de `manejar` (reemplaza el bloque de TTL de v2):

```python
async def manejar(sid, texto, datos, canal="web", telefono=""):
    s = SESIONES.get(sid) or Sesion(sid)
    SESIONES[sid] = s
    if canal != "web":
        s.canal = canal
    if telefono:
        s.telefono = telefono

    ahora = reloj.ahora()
    inactivo = ahora - s.ultima_actividad
    if inactivo > timedelta(minutes=config.SESION_TTL_MIN):
        s.mensajes = 0                               # conversación nueva, contador nuevo
        if s.paso not in ("inicio", "humano"):
            s.paso, s.servicio_id, s.slot, s.folio, s.nombre = "inicio", None, None, None, ""
    if s.paso == "humano" and inactivo > timedelta(minutes=config.SESION_HUMANO_TTL_MIN):
        s.paso, s.handoff = "inicio", False          # el barbero ya no contesta: el bot retoma

    s.ultima_actividad = ahora
    s.mensajes += 1
    if len(SESIONES) > 500:
        _purgar_sesiones()
    ...
```

`_purgar_sesiones` usa `tope = reloj.ahora() - timedelta(minutes=max(config.SESION_TTL_MIN * 8, config.SESION_HUMANO_TTL_MIN))`.

**Tests nuevos (3)** en `tests/test_engine.py` (agrega `from tests.telefonos import TEL_A` o el import que use el archivo):

```python
def test_ttl_no_reinicia_una_conversacion_en_humano(monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    monkeypatch.setattr(config, "SESION_HUMANO_TTL_MIN", 720)
    s = engine.SESIONES["s5"] = engine.Sesion("s5", paso="humano", canal="wa", telefono=TEL_A)
    s.ultima_actividad = reloj.ahora() - timedelta(minutes=90)

    asyncio.run(engine.manejar("s5", "¿hola?", DATOS, canal="wa", telefono=TEL_A))

    assert engine.SESIONES["s5"].paso == "humano"


def test_humano_expira_con_su_ttl_largo(monkeypatch):
    monkeypatch.setattr(config, "SESION_HUMANO_TTL_MIN", 720)
    s = engine.SESIONES["s6"] = engine.Sesion("s6", paso="humano", handoff=True)
    s.ultima_actividad = reloj.ahora() - timedelta(hours=13)

    resp, s = asyncio.run(engine.manejar("s6", "hola", DATOS))

    assert s.paso != "humano" and s.handoff is False
    assert resp


def test_ttl_reinicia_el_contador_de_mensajes(monkeypatch):
    monkeypatch.setattr(config, "SESION_TTL_MIN", 30)
    s = engine.SESIONES["s7"] = engine.Sesion("s7", mensajes=39)
    s.ultima_actividad = reloj.ahora() - timedelta(minutes=31)

    asyncio.run(engine.manejar("s7", "hola", DATOS))

    assert engine.SESIONES["s7"].mensajes == 1
```

### C8 — El chat web no puede usar `sid` de WhatsApp (Tarea 12 de v2, `app/main.py`)

`/api/mensaje` acepta cualquier `sid`. Con `sid="wa:<teléfono>"` un usuario del chat web puede entrar a la sesión de un cliente de WhatsApp (que ya tiene `canal="wa"` y su teléfono) y saltarse el candado de la Tarea 13. Además, `texto` y `sid` no tienen tope de longitud (se mandan al LLM).

```python
from pydantic import BaseModel, Field


class Entrada(BaseModel):
    sid: str = Field(max_length=64)
    texto: str = Field(max_length=500)
    codigo: str = Field(default="", max_length=100)
```

Y en `mensaje()`, tras validar el código:

```python
    if body.sid.startswith("wa:"):
        raise HTTPException(status_code=400, detail="sid inválido.")
```

**Tests nuevos (2)** — `tests/test_api_mensaje.py`:

```python
"""POST /api/mensaje: el chat web no puede suplantar sesiones de WhatsApp."""
from __future__ import annotations

from app import config, engine

from tests.telefonos import TEL_A


def _cliente():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_web_no_puede_usar_un_sid_de_whatsapp(bd, monkeypatch):
    monkeypatch.setattr(config, "DEMO_CODE", "")

    r = _cliente().post("/api/mensaje", json={"sid": f"wa:{TEL_A}", "texto": "hola"})

    assert r.status_code == 400
    assert f"wa:{TEL_A}" not in engine.SESIONES


def test_texto_demasiado_largo_se_rechaza(bd, monkeypatch):
    monkeypatch.setattr(config, "DEMO_CODE", "")

    r = _cliente().post("/api/mensaje", json={"sid": "web1", "texto": "x" * 501})

    assert r.status_code == 422
```

### C9 — Candado del folio (Tarea 13 de v2)

1. **La respuesta debe ser idéntica a la de "folio inexistente".** v2 usa un texto distinto al que hoy dice el motor (`engine.py` ~línea 349: "No encuentro ninguna cita con ese folio. Revísalo en tu confirmación (tipo NVZ-7K4Q)."), así que se podía distinguir. No crees `_folio_no_encontrado`. En cada uno de los tres puntos (`_paso_citas_folio`, `_cancelar_folio`, `_empezar_reagendar`), tras `store.obtener(...)`, trata la cita ajena como inexistente **antes** de la rama de "no encontrada":

```python
cita = store.obtener(folio)
if not _folio_autorizado(s, cita):
    cita = None            # a partir de aquí se comporta exactamente como "no existe"
if not cita:
    ...                    # la rama existente, sin tocarla
```

(Adapta los nombres a las variables reales. Si la rama de no encontrada también cambia `s.paso`, eso queda idéntico.) `_folio_autorizado` usa `t10` de `app.telefono`.

2. **Tests sin fecha fija.** `2026-11-10` en v2 hace fallar las pruebas de cancelar desde ese día (cae dentro de la ventana de cancelación). Usa un horario real con margen:

```python
def _cita_ajena(telefono: str = TEL_B):
    slot = _primer_slot(DATOS)        # con margen de cancelación (Tarea 0)
    return store.crear(slot=slot, servicio_id="corte", nombre="Otro", telefono=telefono)
```

Los tests usan `TEL_A`, `TEL_B` y `TEL_A_SIN_1` (**C4**); `test_wa_cancela_su_propia_cita_aunque_llegue_con_521` usa cita con `TEL_A` y sesión con `TEL_A_SIN_1`.

3. **Test nuevo (1):** la respuesta ante un folio ajeno es igual a la de un folio inexistente.

```python
def test_wa_folio_ajeno_responde_igual_que_folio_inexistente():
    cita = _cita_ajena()
    ajeno, _ = asyncio.run(engine.manejar("w3", f"cancelar {cita['folio']}", DATOS,
                                          canal="wa", telefono=TEL_A))
    inexistente, _ = asyncio.run(engine.manejar("w4", "cancelar NVZ-ZZZZ", DATOS,
                                                canal="wa", telefono=TEL_A))

    assert ajeno == inexistente
```

### C10 — Handoff: el motor lo registra y avisa; el flag no se "autoborra" (Tareas 14 y 17 de v2)

Problema de v2: el motor pone `s.handoff = False` antes de devolver, pero el webhook (Tarea 17) decide registrar el handoff mirando `sesion.handoff` **después** de `manejar`. Nunca vale `True`, así que el KPI jamás se registra, y el chat web recibe siempre `handoff: false`.

**Regla v3:**

- El **motor** registra y avisa en el mismo punto. `s.handoff` queda en `True` mientras la conversación esté en `humano`, y baja a `False` cuando el cliente escribe `menu` o cuando expira (C7).
- El **adaptador** ya no mira `handoff`.

```python
    if _es_handoff(txt):
        s.handoff = True
        s.paso = "humano"
        store.registrar_handoff(sid, texto)                                  # KPI
        await asyncio.to_thread(avisos.notificar_handoff, sid, s.telefono, texto)
        return [...mismas respuestas que hoy...], s
```

En la salida global de `"menu"` (la que ya existe): `s.handoff = False`.
El bloque de silencio en canal `wa` (v2) queda igual y va **después** de la salida de `"menu"`.

**Tests de la Tarea 14** (reemplazan a los de v2; son 4, usan la fixture `bd`; ajusta el nombre de la tabla de handoffs al que definió la Tarea 9):

```python
def _contar_handoffs() -> int:
    with store._con() as con:
        return con.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]


def test_handoff_registra_avisa_y_deja_el_flag_activo(bd, monkeypatch):
    avisos_enviados = []
    monkeypatch.setattr(engine.avisos, "notificar_handoff",
                        lambda sid, tel, msg: avisos_enviados.append((sid, tel, msg)))

    asyncio.run(engine.manejar("h1", "quiero hablar con una persona", DATOS))

    assert len(avisos_enviados) == 1
    assert _contar_handoffs() == 1
    assert engine.SESIONES["h1"].handoff is True
    assert engine.SESIONES["h1"].paso == "humano"


def test_menu_devuelve_al_bot_y_baja_el_flag(bd, monkeypatch):
    monkeypatch.setattr(engine.avisos, "notificar_handoff", lambda *a: None)

    asyncio.run(engine.manejar("h2", "quiero hablar con una persona", DATOS))
    asyncio.run(engine.manejar("h2", "menu", DATOS))

    assert engine.SESIONES["h2"].handoff is False
    assert engine.SESIONES["h2"].paso != "humano"


def test_dos_handoffs_en_la_misma_sesion_avisan_dos_veces(bd, monkeypatch):
    avisos_enviados = []
    monkeypatch.setattr(engine.avisos, "notificar_handoff",
                        lambda sid, tel, msg: avisos_enviados.append(sid))

    asyncio.run(engine.manejar("h3", "quiero hablar con una persona", DATOS))
    asyncio.run(engine.manejar("h3", "menu", DATOS))
    asyncio.run(engine.manejar("h3", "quiero hablar con una persona", DATOS))

    assert len(avisos_enviados) == 2
    assert _contar_handoffs() == 2


def test_en_humano_el_bot_calla_salvo_menu(bd, monkeypatch):
    monkeypatch.setattr(engine.avisos, "notificar_handoff", lambda *a: None)
    asyncio.run(engine.manejar("h4", "quiero hablar con una persona", DATOS,
                               canal="wa", telefono=TEL_A))

    resp, _ = asyncio.run(engine.manejar("h4", "¿hola?", DATOS, canal="wa", telefono=TEL_A))
    assert resp == []

    resp, _ = asyncio.run(engine.manejar("h4", "menu", DATOS, canal="wa", telefono=TEL_A))
    assert resp
```

**Tarea 17 (webhook), versión v3 de `procesar_evento`** — sin `ya_pidio_humano`, sin `registrar_handoff`, sin `cargar_datos()` por evento, y pasando el teléfono a `manejar`:

```python
_DATOS: dict[str, Any] | None = None


def _datos() -> dict[str, Any]:
    global _DATOS
    if _DATOS is None:
        _DATOS = engine.cargar_datos()
    return _DATOS


async def procesar_evento(payload: dict[str, Any], enviar: Enviador | None = None) -> int:
    """Atiende cada mensaje nuevo del payload. Devuelve cuántos mensajes respondió."""
    enviar = enviar or wa_cliente.enviar
    atendidos = 0

    for msg in extraer_mensajes(payload):
        if not store.registrar_evento(msg["id"]):
            log.info("wa: evento repetido (reintento de Meta)")
            continue

        if not msg["texto"]:
            await _enviar(msg["wa_id"], [{"tipo": "texto", "texto": AVISO_SIN_TEXTO}], enviar)
            atendidos += 1
            continue

        respuestas, _ = await engine.manejar(
            f"wa:{msg['wa_id']}", msg["texto"], _datos(), canal="wa", telefono=msg["wa_id"]
        )
        await _enviar(msg["wa_id"], respuestas, enviar)
        atendidos += 1

    return atendidos
```

La Tarea 19 (candado por teléfono) sigue igual: envuelve el cuerpo del bucle en `async with candado(msg["wa_id"])`.

### C11 — Avisos al dueño: `app/avisos.py` (Tarea 14 de v2)

Problemas de v2: el aviso solo mostraba los últimos 4 dígitos (el barbero no podía contactar al cliente) y, a la vez, `sid` ya llevaba el número completo en el mismo texto; no se revisaba la respuesta de Telegram (un token inválido pasaba en silencio); y los logs de errores de `httpx` pueden incluir la URL con el token.

**Regla:** el **log** solo lleva los últimos 4 dígitos; el **aviso privado de Telegram** lleva el número completo y un enlace `wa.me`.

```python
"""Avisos al dueño. Log por defecto; Telegram si está configurado."""
from __future__ import annotations

import logging

import httpx

from . import config
from .telefono import t4

log = logging.getLogger(__name__)


def _telegram(texto: str) -> None:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": texto},
            timeout=5,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        log.error("aviso: Telegram respondió %s", exc.response.status_code)   # nunca loguear exc: lleva la URL con el token
    except httpx.HTTPError as exc:
        log.error("aviso: error de red al avisar por Telegram (%s)", type(exc).__name__)


def notificar_handoff(sid: str, telefono: str, mensaje: str) -> None:
    log.warning("handoff: cliente …%s pide hablar con una persona", t4(telefono))
    if telefono:
        contacto = f"Tel: +{telefono}\nWhatsApp: https://wa.me/{telefono}"
    else:
        contacto = "Canal: chat web (sin teléfono)"
    _telegram(f"Un cliente pide hablar con una persona.\n{contacto}\nDijo: {mensaje[:200]}")


def notificar_alerta(texto: str) -> None:
    log.error("alerta: %s", texto)
    _telegram(f"⚠️ {texto}")
```

`config.py`: `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` como en v2. **VERIFICAR** que `https://wa.me/<wa_id>` abre el chat correcto con un celular mexicano real (ver el punto de formato 521/52 en la prueba temprana de la Tarea 17).

**Tests — `tests/test_avisos.py` (3):**

```python
from __future__ import annotations

import logging

import httpx

from app import avisos, config

from tests.telefonos import TEL_A


class _Resp:
    status_code = 200

    def raise_for_status(self) -> None:
        pass


def test_el_log_enmascara_el_telefono(caplog):
    with caplog.at_level(logging.WARNING):
        avisos.notificar_handoff(f"wa:{TEL_A}", TEL_A, "hola")

    assert TEL_A not in caplog.text
    assert "0011" in caplog.text


def test_telegram_lleva_el_telefono_completo_y_el_enlace(monkeypatch):
    enviados = []
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "T0KEN")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(avisos.httpx, "post",
                        lambda url, json, timeout: enviados.append(json) or _Resp())

    avisos.notificar_handoff(f"wa:{TEL_A}", TEL_A, "hola")

    assert TEL_A in enviados[0]["text"]
    assert f"https://wa.me/{TEL_A}" in enviados[0]["text"]


def test_telegram_rechazado_no_tumba_ni_filtra_el_token(monkeypatch, caplog):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "T0KEN")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "1")

    def rechaza(url, json, timeout):
        req = httpx.Request("POST", url)
        raise httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))

    monkeypatch.setattr(avisos.httpx, "post", rechaza)

    with caplog.at_level(logging.ERROR):
        avisos.notificar_handoff(f"wa:{TEL_A}", TEL_A, "hola")      # no debe lanzar

    assert "T0KEN" not in caplog.text
```

### C12 — Tarea 15: que el test de concurrencia pruebe algo

Si `a` y `b` agendan el **mismo** horario, la segunda reserva choca (`SlotOcupado`) antes de llamar a Calendar y el test pasa aunque `to_thread` no exista.

- Lee `_agendar_hasta_confirmar` (si no existe en el archivo, créalo). Parametrízalo con un índice de horario (`libres[indice]`) y haz que `"a"` use el primero y `"b"` el segundo.
- Tras ver el verde, **revierte temporalmente** un `to_thread` y confirma que `test_un_calendar_lento_no_bloquea_otra_conversacion` se pone rojo. Vuelve a ponerlo. Si no se pone rojo, el test no sirve: arréglalo.

### C13 — Topes del canal WhatsApp (Tareas 2 y 16 de v2)

Problemas de v2: (a) la allowlist compara últimos 10 dígitos contra números crudos, así que bloquea a los propios permitidos; (b) el gate de sesión de WhatsApp usa el tope de la demo web (40) y `MAX_MENSAJES_POR_SESION_WA` queda sin uso; (c) el tope global (`WA_MAX_POR_DIA × 5`) es un número mágico y el test de "tope diario" no podía pasar; (d) el test "bloqueado no gasta presupuesto" probaba WhatsApp, que nunca toca `USO_DIA`; (e) `wa_sumar`/`wa_contar_desde` abren `sqlite3.connect` sin pasar por `_con()`.

**`app/config.py`** (reemplaza lo de v2):

```python
from .telefono import t10


def _lista_telefonos(valor: str) -> set[str]:
    """'521 55 0000 0011, 5500000022' -> {'5500000011', '5500000022'}"""
    return {t10(p) for p in valor.split(",") if t10(p)}


MAX_MENSAJES_POR_SESION_WA = int(os.getenv("MAX_MENSAJES_POR_SESION_WA", "500"))
WA_ALLOWLIST = _lista_telefonos(os.getenv("WA_ALLOWLIST", ""))
WA_MAX_POR_HORA = int(os.getenv("WA_MAX_POR_HORA", "12"))          # por teléfono
WA_MAX_POR_DIA = int(os.getenv("WA_MAX_POR_DIA", "150"))           # por teléfono
WA_MAX_GLOBAL_POR_DIA = int(os.getenv("WA_MAX_GLOBAL_POR_DIA", "750"))   # todos los teléfonos
```

**`app/store.py`**:

```python
def wa_sumar(telefono: str, momento: str) -> None:
    init()
    with _candado, _con() as con:
        con.execute("INSERT INTO wa_uso (telefono, momento) VALUES (?, ?)", (telefono, momento))


def wa_contar_desde(desde: str, telefono: str | None = None) -> int:
    init()
    sql = "SELECT COUNT(*) FROM wa_uso WHERE momento >= ?"
    args: list[Any] = [desde]
    if telefono:
        sql += " AND telefono = ?"
        args.append(telefono)
    with _con() as con:
        return con.execute(sql, args).fetchone()[0]
```

**`app/engine.py`** — dentro de `manejar`, después de actualizar `ultima_actividad` y `mensajes`:

```python
def _limite_wa(s: Sesion, datos: dict[str, Any]) -> list[dict[str, Any]] | None:
    """None si el mensaje puede pasar; si no, la respuesta de bloqueo."""
    tel = t10(s.telefono)
    if config.WA_ALLOWLIST and tel not in config.WA_ALLOWLIST:
        return [_t(f"Por ahora no puedo atender este número. Marca al {datos['telefono']} y con gusto te ayudamos.")]
    if s.mensajes > config.MAX_MENSAJES_POR_SESION_WA:
        return [_t("En esta conversación ya van muchos mensajes. Escríbeme mañana y seguimos.")]
    ahora = reloj.ahora()
    hace_una_hora = (ahora - timedelta(hours=1)).isoformat(timespec="seconds")
    inicio_del_dia = ahora.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if store.wa_contar_desde(hace_una_hora, tel) >= config.WA_MAX_POR_HORA:
        return [_t("Me llegaron muchos mensajes seguidos. Espera unos minutos y seguimos.")]
    if (store.wa_contar_desde(inicio_del_dia, tel) >= config.WA_MAX_POR_DIA
            or store.wa_contar_desde(inicio_del_dia) >= config.WA_MAX_GLOBAL_POR_DIA):
        return [_t(f"Hoy ya no alcanzo a contestar más por este medio. Escríbeme mañana o marca al {datos['telefono']}.")]
    store.wa_sumar(tel, ahora.isoformat(timespec="seconds"))
    return None


# en manejar():
    if s.canal == "wa":
        bloqueo = _limite_wa(s, datos)
        if bloqueo is not None:
            return bloqueo, s
    else:
        if s.mensajes > config.MAX_MENSAJES_POR_SESION:
            return [_t("Esta sesión de demo llegó a su tope de mensajes. Recarga la página...")], s
        # recién ahora el presupuesto global (los bloqueados no lo consumen)
        if USO_DIA["fecha"] != reloj.ahora().date():
            USO_DIA.update(fecha=reloj.ahora().date(), n=0)
        USO_DIA["n"] += 1
        if USO_DIA["n"] > config.MAX_MENSAJES_POR_DIA:
            return [_t("La demo llegó a su límite de mensajes por hoy...")], s
    return await _flujo(s, texto, txt, datos)
```

(Conserva los textos exactos de demo que ya existen en el motor; los de arriba son los del canal WhatsApp.)

**Tests — `tests/test_wa_limites.py` (6, todos con la fixture `bd`):**

```python
from __future__ import annotations

import asyncio

from app import config, engine

from tests.telefonos import TEL_A, TEL_B, TEL_C

DATOS = engine.cargar_datos()


def _wa(texto, tel=TEL_A):
    return asyncio.run(engine.manejar(f"wa:{tel}", texto, DATOS, canal="wa", telefono=tel))


def _texto(resp) -> str:
    return " ".join(r.get("texto", "") for r in resp).lower()


def test_wa_usa_su_propio_tope_de_sesion(bd, monkeypatch):
    monkeypatch.setattr(config, "MAX_MENSAJES_POR_SESION", 1)        # el de la demo web no aplica
    monkeypatch.setattr(config, "MAX_MENSAJES_POR_SESION_WA", 2)

    _wa("hola"); _wa("hola")
    resp, _ = _wa("hola")

    assert "mañana" in _texto(resp)
    assert "demo" not in _texto(resp) and "recarga" not in _texto(resp)


def test_web_bloqueado_no_gasta_presupuesto(bd, monkeypatch):
    monkeypatch.setattr(config, "MAX_MENSAJES_POR_SESION", 1)
    engine.SESIONES["web-x"] = engine.Sesion("web-x", mensajes=5)
    antes = engine.USO_DIA["n"]

    asyncio.run(engine.manejar("web-x", "hola", DATOS))

    assert engine.USO_DIA["n"] == antes


def test_allowlist_deja_pasar_a_los_permitidos_y_bloquea_al_resto(bd, monkeypatch):
    monkeypatch.setattr(config, "WA_ALLOWLIST", config._lista_telefonos(TEL_A))   # número de 13 dígitos

    permitido, _ = _wa("hola", tel=TEL_A)
    bloqueado, _ = _wa("hola", tel=TEL_B)

    assert "no puedo atender" not in _texto(permitido)
    assert "no puedo atender" in _texto(bloqueado)


def test_limite_por_telefono_por_hora(bd, monkeypatch):
    monkeypatch.setattr(config, "WA_MAX_POR_HORA", 2)

    _wa("hola"); _wa("hola")
    resp, _ = _wa("hola")

    assert "espera" in _texto(resp)


def test_tope_diario_por_telefono(bd, monkeypatch):
    monkeypatch.setattr(config, "WA_MAX_POR_HORA", 100)
    monkeypatch.setattr(config, "WA_MAX_POR_DIA", 1)

    _wa("hola")
    resp, _ = _wa("hola")

    assert "mañana" in _texto(resp)


def test_tope_global_diario_de_whatsapp(bd, monkeypatch):
    monkeypatch.setattr(config, "WA_MAX_POR_DIA", 100)
    monkeypatch.setattr(config, "WA_MAX_GLOBAL_POR_DIA", 2)

    _wa("hola", tel=TEL_A); _wa("hola", tel=TEL_B)
    resp, _ = _wa("hola", tel=TEL_C)

    assert "mañana" in _texto(resp)
```

**`.env.example`:** agrega `WA_ALLOWLIST=`, `WA_MAX_POR_HORA=12`, `WA_MAX_POR_DIA=150`, `WA_MAX_GLOBAL_POR_DIA=750`, `SESION_HUMANO_TTL_MIN=720`, `MAX_MENSAJES_POR_SESION_WA=500`.

### C14 — Alerta cuando los envíos a Meta fallan (Tarea 17 de v2)

Si el token de WhatsApp vence (los temporales duran 24 h), `wa_cliente.enviar` devuelve `False`, `_enviar` lo ignora y los clientes se quedan sin respuesta sin que nadie se entere. En `whatsapp_webhook.py` (importa `time` y `avisos`):

```python
_ULTIMA_ALERTA: float | None = None


async def _alerta_envio_fallido() -> None:
    """Como máximo una alerta por hora."""
    global _ULTIMA_ALERTA
    ahora = time.monotonic()
    if _ULTIMA_ALERTA is not None and ahora - _ULTIMA_ALERTA < 3600:
        return
    _ULTIMA_ALERTA = ahora
    await asyncio.to_thread(
        avisos.notificar_alerta, "No pude enviar un mensaje de WhatsApp. Revisa WA_TOKEN (¿venció?)."
    )


async def _enviar(wa_id: str, respuestas: list[dict[str, Any]], enviar: Enviador) -> None:
    for payload in whatsapp_formato.a_payloads(respuestas):
        if not await enviar(wa_id, payload):
            await _alerta_envio_fallido()
```

**Test nuevo (1)**, en `tests/test_wa_webhook.py`:

```python
def test_envio_fallido_avisa_una_sola_vez(bd, monkeypatch):
    alertas = []
    monkeypatch.setattr(whatsapp_webhook.avisos, "notificar_alerta", lambda texto: alertas.append(texto))
    monkeypatch.setattr(whatsapp_webhook, "_ULTIMA_ALERTA", None)

    async def falla(wa_id, payload):
        return False

    asyncio.run(whatsapp_webhook.procesar_evento(_payload("hola", "wamid.f1"), falla))
    asyncio.run(whatsapp_webhook.procesar_evento(_payload("hola", "wamid.f2"), falla))

    assert len(alertas) == 1
```

### C15 — Erratas de v2

- **Tarea 8, test `test_envia_a_graph_con_telefono_y_token`:** la línea `assert url == f"https://graph.facebook.com/…"` quedó sin indentar y rompe el archivo (`SyntaxError`). Debe ir dentro de la función, con 4 espacios.
- **Referencias cruzadas:** "Tarea 21" (pegar credenciales en Render) es la **Tarea 23**; "Tareas 3–5" (verificado en seco `whatsapp_formato`) son las **Tareas 5–7**.
- **Criterios de aceptación 2 y 4:** la ruta es `/wa/webhook`, no `/api/whatsapp/webhook`.
- **Decisión abierta 2 (8.1):** nadie del producto consume `/api/citas` (v2 lo afirma en la Tarea 4), así que `static/index.html` no necesita el token. Quita esa decisión.
- **Sección 2 (Alcance):** "Topes de demo desactivados para el canal WhatsApp" pasa a "Topes propios del canal WhatsApp" (C13); agrega a "Dentro": `app/telefono.py`, `app/avisos.py`, `ADMIN_TOKEN`, TTL de sesión, alerta de envío fallido.

---

## 3. Validación v3

### 3.1 Conteos (C16)

v2 prometía **111**. Con H1 en `main` el baseline sube a 59 (la Tarea 4 ya está hecha) y el total final no cambia por eso. Las correcciones agregan estas pruebas:

| Corrección | Tests nuevos respecto a v2 |
|---|---|
| C3 `telefono` | +3 |
| C6 bytes | +2 |
| C7 sesiones | +3 |
| C8 `sid` y longitud | +2 |
| C9 folio ajeno = inexistente | +1 |
| C10 handoff (4 en lugar de 3) | +1 |
| C11 `avisos` | +3 |
| C13 topes (6 en lugar de 5) | +1 |
| C14 alerta de envío | +1 |
| **Total** | **+17 → ≈ 128** |

El número exacto lo da `--collect-only`. **No ajustes pruebas para que cuadre con 128**: reporta el real y actualiza la tabla de la sección 7.2 de v2.

### 3.2 Gate de salida (reemplaza y amplía el de v2)

- Suite completa en verde, sin `-k` ni exclusiones (salvo `tests/smoke_test.py`).
- `git grep -nE "52(1)?449[0-9]{7}" -- . ':!*.example'` y `git grep -n "TELEGRAM_BOT_TOKEN=" -- . ':!*.example'` vacíos.
- `app/calendar_sync.py` y `app/slots.py` sin cambios. `requirements.txt` solo gana `tzdata`.
- `GET /api/citas` sin token → 503/401, con token → sin `telefono` ni `calendar_event_id`.
- Webhook con firma o token no ASCII → 403 (nunca 500).
- Chat web con `sid` que empieza con `wa:` → 400.
- Teléfono permitido en `WA_ALLOWLIST` (en formato de 13 dígitos) **sí** pasa.
- Un handoff queda registrado en la BD y llega por Telegram con el número completo; el log solo muestra los últimos 4 dígitos.

### 3.3 Criterios de aceptación adicionales

11. Un cliente de WhatsApp recurrente no queda bloqueado por acumular mensajes de visitas anteriores (el contador se reinicia con el TTL).
12. Una conversación en `humano` no la retoma el bot antes de `SESION_HUMANO_TTL_MIN`.
13. Un folio ajeno recibe exactamente la misma respuesta que un folio inexistente.
14. Si Meta rechaza los envíos, el dueño recibe una alerta (máximo una por hora).

## 4. Decisiones abiertas para el dueño del repo

1. **Medio de aviso del handoff:** propuesta Telegram. Con la API de WhatsApp el barbero **no** puede contestar desde la app del negocio: lo haría desde su WhatsApp personal con el enlace `wa.me`. **VERIFICAR** si el número de producción admite uso simultáneo con la app de WhatsApp Business (modo coexistencia) antes de decidir.
2. **Responder o no a números bloqueados:** con el cobro por mensaje, contestarle a un spammer cuesta. Opción: tras N bloqueos seguidos, silencio.
3. **Allowlist:** ¿en producción arranca cerrada (solo números de prueba) o abierta?
4. **Umbrales:** `WA_MAX_POR_HORA` (12), `WA_MAX_POR_DIA` (150), `WA_MAX_GLOBAL_POR_DIA` (750), `SESION_TTL_MIN` (30) y `SESION_HUMANO_TTL_MIN` (720) son puntos de partida, no medidas.
5. **Aviso de privacidad (LFPDPPP):** redacción y responsable, antes de la primera cita real. Validar con un especialista.
6. **Selector de día y multi-profesional:** siguen fuera de este plan (ver "Pendientes" de v2).

## 5. Reporte final que debes entregar

1. Ruta de los archivos creados o modificados y rama usada.
2. Conteo real de tests (`--collect-only`) y resultado de la suite.
3. Lista de puntos de este documento que **no** pudiste cumplir y por qué.
4. Lo que marcaste **VERIFICAR**.
5. Decisiones abiertas que requieren al dueño.
6. Confirmación de que no hiciste `push` ni abriste PR (salvo que el dueño lo haya pedido).
