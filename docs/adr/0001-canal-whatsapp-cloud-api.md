# ADR 0001 — Canal WhatsApp Cloud API sobre el motor existente

- **Estado:** aceptado (canal implementado, **apagado en producción**)
- **Fecha:** 2026-10-30
- **Rama:** `feat/whatsapp-meta` — 30 commits propios del canal (29 archivos, +1877/−68).
  El PR [#1](https://github.com/EdgeFlores19/barberia-bot/pull/1) muestra 34 commits y
  35 archivos porque `main` local tenía 4 commits sin publicar en `origin/main`
  (hotfix H1 de `GET /api/citas` + planes normalizados).
- **Alcance:** conectar el bot de barbería a la WhatsApp Cloud API sin reescribir el motor.
- **Planes de referencia:** `docs/plans/plan-whatsapp-v3-correcciones.md` (manda),
  `docs/plans/adaptador-whatsapp-meta-v2.md`.

## Contexto

El bot solo existía como chat web (`static/index.html` + `POST /api/mensaje`). El objetivo de
negocio es atender por WhatsApp, que es donde el cliente de una barbería realmente escribe. El
proyecto es una muestra de portafolio con datos ficticios y **sin credenciales de Meta**, así que
todo el canal tenía que poder desarrollarse y probarse sin cuenta, sin número y sin token.

Restricciones duras del dueño: no tocar `app/slots.py` ni `app/calendar_sync.py`, no reescribir el
motor, no subir secretos ni teléfonos reales al repo, y no hacer push/PR sin autorización.

## Decisiones

### D1 — Adaptador aislado; el motor no sabe que existe WhatsApp

`app/engine.py` no importa nada de WhatsApp. El canal entra como dos campos opcionales:
`manejar(..., canal="wa", telefono=...)` y `Sesion.canal` / `Sesion.telefono` (últimos campos).
El chat web sigue llamando `manejar(sid, texto, datos)` sin cambios y sin regresión.

- **Alternativa descartada:** ramificar por canal dentro del motor. Habría duplicado los flujos de
  agendar/cancelar y hecho imposible testearlos por separado.
- **Consecuencia:** cada canal nuevo (Instagram, Telegram) es un traductor, no una copia del bot.

### D2 — El KPI del handoff lo registra el motor, no el adaptador

Cuando el cliente pide humano, `engine.manejar` deja la sesión en `paso="humano"` y llama
`store.registrar_handoff`. `procesar_evento` (`app/whatsapp_webhook.py`) **no** vuelve a
registrarlo ni consulta un `ya_pidio_humano`.

- **Alternativa descartada:** registrar desde el adaptador con dedupe propio. Producía doble
  conteo cuando el mismo cliente pedía humano dos veces y rompía el KPI.
- **Consecuencia:** el handoff funciona igual por web y por WhatsApp; el adaptador solo envía.

### D3 — Firma HMAC sobre bytes crudos, y el rechazo nunca es 500

`firma_valida` calcula HMAC-SHA256 del cuerpo **crudo** (`await request.body()`), exige el prefijo
`sha256=` y compara con `hmac.compare_digest`. El `hub.verify_token` se compara en **bytes**.

- **Alternativa descartada:** comparar el token como `str`. Un token con bytes no ASCII hacía que
  `compare_digest` lanzara `TypeError` → 500 en vez de 403, delatando la ruta.
- **Consecuencia:** `GET /wa/webhook` con token no ASCII devuelve 403
  (`test_webhook_get_con_token_no_ascii_devuelve_403`).

### D4 — Entrega "como máximo una vez", sin cola de reintentos

El webhook contesta 200 en milisegundos y procesa en `BackgroundTasks`. La idempotencia es por
`wa_eventos` (una fila por `message.id` de Meta) y hay un `asyncio.Lock` por teléfono para
serializar los mensajes de un mismo cliente. Si el envío a Graph falla, se registra, se alerta y
**no se reintenta**.

- **Alternativa descartada:** cola persistente con reintentos. Es la solución correcta para un
  producto de pago; aquí añadía una dependencia (Redis/Celery) sin cliente real todavía.
- **Consecuencia aceptada:** un fallo transitorio de Graph pierde esa respuesta. El cliente puede
  reescribir y el bot retoma la conversación donde iba, porque la sesión vive en memoria.
- **Deuda:** pasar a "al menos una vez" con idempotencia de envío cuando exista volumen.

### D5 — Estado en memoria ⇒ un solo worker, deliberadamente

`engine.SESIONES`, `_CANDADOS` y `_ULTIMA_ALERTA` viven en el proceso. `render.yaml` no define
`--workers` y así debe quedarse: con dos instancias se rompen el candado por teléfono y la sesión.

- **Consecuencia:** el canal escala en vertical, no en horizontal. Migrar el estado a la BD es
  requisito previo a escalar.

### D6 — Aviso de envío fallido, como máximo uno por hora

`_enviar` alerta cuando `enviar()` devuelve `False`. La alerta va por `asyncio.to_thread` (no
bloquea el event loop) y se limita con `_ULTIMA_ALERTA` y `time.monotonic()` a una por hora.

- **Alternativa descartada:** alertar por cada fallo. Un token vencido con 50 mensajes pendientes
  generaría 50 avisos y el dueño silenciaría el canal justo cuando importa.
- **Consecuencia:** `test_envio_fallido_avisa_una_sola_vez`.

### D7 — Reloj del negocio, no del servidor

Se añadió `app/reloj.py` (`ahora()` con `CALENDAR_TZ`, `America/Mexico_City`). Todo cálculo de
horarios que antes usaba `datetime.now()` pasa por ahí, y los tests congelan la hora.

- **Motivo:** en Render el proceso corre en UTC. Sin esto, "hoy a las 16:30" se evaluaba en UTC y
  los tests fallaban según la hora real de ejecución (4 tests rojos el 2026-10-29).

### D8 — Teléfonos normalizados a últimos 10 dígitos

`app/telefono.py` expone `t10()`: normaliza `521 55 0000 0011`, `+52 55 0000 0011` y
`55 0000 0011` al mismo valor. `WA_ALLOWLIST` y los contadores por teléfono comparan sobre ese valor.

- **Motivo:** Meta entrega `521...` y el humano escribe `55...`; sin normalizar, la allowlist y los
  topes por teléfono no funcionaban.

### D9 — `WA_API_VERSION` centralizado y marcado VERIFICAR

Default `v26.0` en `app/config.py`, declarado en `render.yaml` y en `.env.example`. En
`.env.example` el comentario va en **línea propia**: si el `#` comparte línea con el valor, dotenv
carga `v26.0  # VERIFICAR...` y la URL de Graph queda corrupta.

### D10 — SQLite sigue siendo la base, sobre disco efímero

No se migró a Postgres en esta fase: habría multiplicado el alcance y no había cliente real.

- **Riesgo aceptado y activo:** en Render free la BD se borra en cada deploy y cada dormida
  (`store.DB_FILE` → `barberia.db` local). **Bloqueante para cobrar citas reales**, no para este
  PR; requiere plan con disco persistente (`DB_FILE=/data/barberia.db`) o Postgres.

## Verificaciones pendientes (no se pudieron confirmar sin credenciales)

1. **`WA_API_VERSION=v26.0`** — contrastar con el changelog de Meta.
2. **`https://wa.me/<wa_id>`** — confirmar que abre el chat con un celular mexicano real
   (formato `521` vs `52`).
3. **Tarifas de conversación para México** — consultar la tabla vigente antes de fijar precios.
4. **Allowlist abierta o cerrada en producción** — hoy vacía = abierta.
5. **Umbrales** `WA_MAX_POR_HORA` (12), `WA_MAX_POR_DIA` (150), `WA_MAX_GLOBAL_POR_DIA` (750),
   `SESION_TTL_MIN` (30), `SESION_HUMANO_TTL_MIN` (720).
6. **Medio de aviso del handoff** — hoy Telegram; sin `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
   solo queda en el log.

## Decisiones del dueño (no del implementador)

- Destino del aviso de handoff (Telegram, correo, panel) y quién lo recibe.
- `WA_ALLOWLIST` abierta o cerrada al primer cliente real.
- Umbrales de los topes y de los TTL.
- Subir de plan en Render + disco persistente, o migrar a Postgres.
- `SEED_DEMO=0` antes de operar: con `1`, `seed.sembrar_si_vacio` mete citas de demostración en la
  base real.
- Aviso de privacidad (LFPDPPP) antes de capturar nombre y teléfono de clientes.

## Consecuencias

**A favor**

- 127 tests verdes, deterministas (reloj fijo, teléfonos ficticios, margen anti-flaky).
- `app/slots.py` y `app/calendar_sync.py` con diff vacío contra `main`.
- El canal se puede probar sin Meta: `WA_MODO=consola` + `scripts/wa_simula.py`, que firma el
  payload igual que Meta.
- El chat web no cambió de comportamiento (verificado con `tests/test_api_mensaje.py`).

**En contra**

- Un solo worker, por diseño y por límite del estado en memoria.
- Entrega sin reintentos.
- BD efímera en el plan free.

**Siguiente paso:** fusionar el código con el canal apagado (sin `WA_*` en Render el `POST` del
webhook responde 503 y el `GET` 403) y encenderlo solo cuando existan disco persistente, `SEED_DEMO=0` y aviso de
privacidad.
