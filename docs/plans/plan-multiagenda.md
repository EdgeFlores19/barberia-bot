# Plan — varias agendas (un calendario por persona, propiedad del negocio)

- **Estado:** propuesto, sin construir.
- **Fecha:** 2026-11-20
- **Base:** el repo tal como está al 20-nov (anticipo fase 1, concentrado con corte, guardia del LLM).
- **Línea base de pruebas:** recalcúlala tú con `pytest --collect-only -q` antes de empezar (regla de
  `AGENTS.md`). En mi sandbox, sin `TestClient`, eran 496 casos ejecutables y 21 que solo corren en el CI.
- **Rama:** una por fase (`feat/multiagenda-f1` … `f5`). Un commit por tarea (Conventional Commits en español).

## 0. Cómo usar este documento

1. Sigue `AGENTS.md`: TDD (la prueba primero, comprobar que falla, luego el código), sin `datetime.now()`
   (`reloj.ahora()`), nada de red síncrona dentro de `async def` (`asyncio.to_thread`), ningún teléfono real,
   logs con `t4`, **no `git push` ni PR sin que el dueño lo pida**.
2. **Este plan autoriza tocar `app/calendar_sync.py`** (tareas M3, M5 y M7) y **no autoriza tocar `app/slots.py`**:
   la disponibilidad por agenda se resuelve llamándolo una vez por agenda con la lista de ocupados de esa agenda.
3. Una tarea a la vez; al terminar cada una, la suite completa debe dar el mismo resultado que la línea base
   más las pruebas nuevas. Si un conteo no cuadra, **para y reporta**.
4. Todo lo que dependa de un hecho externo (API de Google, límites de WhatsApp) está en la sección 9 como
   **VERIFICAR**: confírmalo en la documentación vigente antes de la tarea que lo usa.

## 1. Objetivo y principios

Que un negocio con varias personas agende por WhatsApp con la agenda de cada una, y que **el dueño** administre
el equipo sin pedirle nada a quien mantiene el bot.

1. **El calendario es del negocio; la persona es una etiqueta.** Los calendarios viven en una cuenta de Google del
   negocio. Si alguien renuncia, el dueño le quita el acceso y el calendario con su historial se queda.
2. **El equipo sale de los nombres de los calendarios.** Un calendario cuyo nombre empieza con el prefijo
   (`Agenda · `) es una agenda. Alta = crear el calendario; baja = ocultarlo o quitar el prefijo; cambio de persona =
   renombrarlo. Sin paneles ni despliegues.
3. **La base manda, Google solo la mantiene al día.** El equipo se guarda en una tabla `agendas`; el motor lee esa
   tabla. Un panel futuro editaría la misma tabla sin tocar el motor.
4. **El ID del calendario es la identidad; el nombre es una etiqueta.** Cada cita guarda el ID y una copia del
   nombre: renombrar no rompe el historial ni los reportes.
5. **Compatibilidad total hacia atrás.** Con `CALENDAR_AGENDAS_PREFIJO` vacío (el valor por defecto) el bot se
   comporta exactamente como hoy. Es el mismo guardarraíl que el anticipo.
6. **Falla hacia lo seguro.** Una lectura fallida o vacía de Google nunca deja al negocio sin equipo, y un
   calendario que falla no afecta a los demás.

## 2. Decisiones

**Tomadas** (con el dueño del proyecto):

| # | Decisión |
|---|---|
| D1 | Un calendario por persona, propiedad del negocio (cuenta de Google propia del negocio, recomendada). |
| D2 | El equipo sale de los nombres de los calendarios (prefijo), con la tabla `agendas` como fuente para el motor. |
| D3 | Una sola autorización de Google (un token) con acceso a todos los calendarios del negocio. |
| D4 | Los días libres y ausencias de cada persona son eventos en su calendario (los de todo el día ya bloquean, ADR 0007). Sin horarios por persona en la v1: todas siguen el horario del negocio. |
| D5 | Todas las personas hacen todos los servicios en la v1. |
| D6 | Los avisos siguen yendo al chat del dueño en la v1 (sin Telegram por persona). |

**Por confirmar con la dueña del negocio** (no bloquean la fase 1 ni la 2): ver sección 10.

**Regla de asignación para "cualquiera disponible"** (propuesta, ajustable):
1. La misma persona de la última cita confirmada de esa clienta (por teléfono), si está libre.
2. Si no, la que tenga **menos citas ese día** (confirmadas más apartados vigentes).
3. Si hay empate, la de menor `orden` (el de alta en `agendas`).

## 3. Diseño

### 3.1 Datos (`app/store.py`)

```sql
-- citas: la agenda a la que pertenece. '' = la agenda única (modo actual, antes de este plan).
ALTER TABLE citas ADD COLUMN agenda_id     TEXT NOT NULL DEFAULT '';
ALTER TABLE citas ADD COLUMN agenda_nombre TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS agendas (
    calendar_id TEXT PRIMARY KEY,            -- identidad (no cambia al renombrar)
    clave       TEXT NOT NULL UNIQUE,        -- 8 hex de sha1(calendar_id): lo que viaja en los botones de WhatsApp
    nombre      TEXT NOT NULL,               -- sin el prefijo
    orden       INTEGER NOT NULL,            -- de alta; desempata la asignación
    activa      INTEGER NOT NULL DEFAULT 1,
    ausencias   INTEGER NOT NULL DEFAULT 0,  -- lecturas exitosas seguidas sin verla (la baja exige 2)
    alta        TEXT NOT NULL
);
```
- La migración es **aditiva** (`ADD COLUMN` con `DEFAULT`, `CREATE TABLE IF NOT EXISTS`): usa el helper
  `_agregar_columna` que ya existe en `store.py`. No toca filas existentes.
- Un choque de horario es "mismo `slot` **y** misma `agenda_id`" (en `crear`, `reagendar` y
  `registrar_comprobante`). El solape entre duraciones distintas sigue validándolo `slots.validar`.
- `confirmadas()` y `ocupados()` ganan el parámetro `agenda_id=None` (None = todas, como hoy) y las filas que
  devuelven incluyen `agenda_id`.
- Los IDs de calendario **no** se muestran al cliente ni en `/health`: en WhatsApp viaja `clave`.

### 3.2 Descubrimiento de agendas (`calendar_sync.listar_agendas` + `store.agendas_sincronizar`)

- Lee `calendarList` con el token del negocio y se queda con los calendarios cuyo nombre empieza con el prefijo
  y cuyo rol permite escribir (dueño o editor). El nombre de la agenda es lo que sigue al prefijo, sin espacios sobrantes.
- **Reglas de sincronización** (puras, en `store`, para probarlas sin red):
  - Calendario nuevo → alta con el siguiente `orden`, `activa=1`. **Aviso al dueño.**
  - Mismo ID, nombre distinto → se actualiza el nombre. **Aviso al dueño.**
  - Ausente en una lectura exitosa → `ausencias += 1`; con `ausencias >= 2` → `activa=0` (baja suave, nunca se borra).
    Reaparece → `activa=1`, `ausencias=0`.
  - **Lectura fallida o lista vacía cuando antes había agendas → se trata como error**: no cambia nada, marca el estado
    como degradado y avisa (una vez, no en cada ciclo). El bot sigue con la última lista buena.
  - Nombre repetido → el segundo recibe el sufijo ` (2)`; el nombre `Cualquiera` está reservado (se le agrega el sufijo).
  - Calendario sin permiso de escritura → se excluye y se avisa (el bot no podría crear el evento espejo).
- Se refresca de forma perezosa con **su propio TTL** (`CALENDAR_AGENDAS_TTL_S`, 300 s; el del bloqueo de eventos sigue
  siendo `CALENDAR_BLOQUEO_TTL_S`, 60 s), dentro del `_sincronizar_calendar` del motor, que ya llama a `calendar_sync`
  con `asyncio.to_thread`.

### 3.3 `calendar_sync` por calendario

Hoy el módulo es de un solo calendario: `config.CALENDAR_ID`, una caché global `_BLOQ` y un estado global.
Pasa a parametrizarse por `calendar_id`:
- `crear(cita, datos, calendar_id=None)`, `mover(event_id, cita, datos, calendar_id=None)` y
  `borrar(event_id, calendar_id=None)`. `calendar_id=None` usa `config.CALENDAR_ID` (modo actual: sin cambios).
- El motor pasa `cita["agenda_id"] or None`: **sin el ID del calendario no se puede borrar un evento** (la URL lo exige),
  por eso toda cita con agenda lo guarda.
- Caché y estado de bloqueo **por calendario** (`_BLOQ[calendar_id]`, `_ESTADO[calendar_id]`) con TTL, errores y
  **periodo de reintento tras un fallo** (`CALENDAR_BLOQUEO_REINTENTO_S`, 120 s) independientes: si un calendario falla,
  solo esa agenda se degrada y solo ella espera para reintentar.
- `bloqueos(calendar_id=None)` devuelve los bloqueos de ese calendario y marca cada uno con su `agenda_id`.
- `salud()` agrega un resumen por agenda (activas, degradadas), sin IDs.
- Se conserva todo lo existente: `MARCA_BOT` (los eventos espejo no se leen como bloqueos), eventos de todo el día,
  paginación y topes.

### 3.4 Disponibilidad y asignación (`app/agendas.py`, módulo nuevo y puro; `slots.py` no se toca)

- `ocupadas(agenda_id)`: citas de esa agenda (`store.confirmadas(agenda_id=...)`) más los bloqueos de su calendario.
- Disponibilidad de una agenda: `generar_slots(datos, servicio_id, ocupadas(agenda_id), ahora)`.
- **"Cualquiera"**: unión ordenada y sin repetidos de los horarios de todas las agendas activas.
- `candidatas(...)` devuelve las agendas donde `slots.validar` aprueba ese horario; `elegir(...)` aplica la regla de la
  sección 2. Funciones puras, con las citas pasadas como argumento, para probarlas con datos de ejemplo.
- **La asignación se hace al apartar o confirmar**, no al mostrar horarios, y **se revalida**: si dos clientas toman el
  mismo horario con "cualquiera", la segunda `store.crear` choca en esa agenda y se prueba la siguiente candidata antes de
  decir "se acaba de ocupar".

### 3.5 Flujo en el motor (`app/engine.py`)

- Paso nuevo `agendar_agenda`, entre el servicio y el día: "¿Con quién prefieres?" con una opción por agenda y
  **"Cualquiera"**. Si solo hay una agenda activa, se salta. El estado de la sesión guarda `agenda_id` (`''` modo actual,
  `'*'` cualquiera, o la `clave` elegida).
- WhatsApp: hasta 3 opciones van como botones y más como lista (el formateador ya hace esa conversión). **v1: hasta 9 agendas
  más "Cualquiera"**; si hay más, se muestran las primeras 9 por `orden` y se registra una advertencia.
  Los títulos largos se recortan solo para mostrarse (VERIFICAR límites).
- La confirmación dice con quién: "…, con Ana."
- **Cancelar** borra el evento en el calendario de la cita. **Mover** mantiene la misma agenda; si esa agenda ya no está
  activa, pasa a la manicurista (handoff) en vez de ofrecer horarios que no son de ella.
- Con anticipo: el apartado y el comprobante ya son por cita, así que heredan la agenda. Con "cualquiera", la agenda se
  asigna en el momento de aceptar (cuando se crea el apartado).

### 3.6 Avisos, resumen y administración

- Las alertas de anticipo, cancelación y handoff agregan la línea "Con: Ana" cuando la cita tiene agenda.
- El concentrado agrega la columna "con quién" y una sección **"Citas por reasignar"** con las citas futuras de agendas dadas
  de baja.
- **Cambios de equipo** (alta, baja, renombre) y **citas huérfanas** avisan al chat del dueño una sola vez por cambio.
- `/health` expone solo conteos (agendas activas, si está degradada). `GET /api/agendas` (con `ADMIN_TOKEN`) lista nombre, si
  está activa, citas futuras y estado del bloqueo; **sin IDs de calendario**.

### 3.7 Configuración

Variables nuevas, vacías en `.env.example`: `CALENDAR_AGENDAS_PREFIJO` (vacío = modo actual) y `CALENDAR_AGENDAS_TTL_S` (300).
`GOOGLE_CALENDAR_ID` sigue existiendo para el modo actual y se ignora (con una advertencia) si el modo multiagenda está activo.
El token sigue siendo uno (`GOOGLE_TOKEN_JSON`). Los IDs de calendario nunca van en el repo ni en variables: se descubren.

## 4. Tareas

Convenciones: **Archivos** (tocar solo estos), **Pruebas primero**, **Aceptación** y **Commit**. Tamaño: S (poco), M, L.

### Fase 1 — Cimientos, sin cambio visible

**M1 · Esquema y almacén de agendas (M).**
- Archivos: `app/store.py`; `tests/test_agendas_store.py` (nuevo).
- Pruebas primero: la migración agrega las columnas y la tabla sin perder filas y es idempotente (dos `init`); una base con la
  forma anterior se migra; las citas viejas quedan con `agenda_id=''`; funciones de `agendas` (alta, renombre, ausencias,
  baja suave, reaparición) según las reglas de 3.2, incluidas lista vacía = error y nombres repetidos.
- Aceptación: suite igual que la base + las nuevas.
- Commit: `feat: tabla de agendas y agenda en las citas (migración aditiva)`.

**M2 · Choques por agenda (S).**
- Archivos: `app/store.py`; `tests/test_agendas_store.py`.
- Pruebas primero: dos citas en el mismo horario y agendas distintas conviven; en la misma agenda chocan (`crear`, `reagendar`,
  `registrar_comprobante`); un apartado bloquea solo su agenda; sin agenda todo se comporta como hoy.
- Aceptación: la suite existente da **exactamente** el mismo resultado que la base.
- Commit: `feat: el choque de horario es por agenda`.

**M3 · `calendar_sync` por calendario (L).** *(autorizado por este plan)*
- Archivos: `app/calendar_sync.py`; `tests/test_calendar_multi.py` (nuevo). Sigue el patrón de simulación de
  `tests/test_calendar_bloqueo.py`.
- Pruebas primero: `crear`/`mover`/`borrar` usan el calendario pedido y, sin parámetro, `CALENDAR_ID`; caché y errores de bloqueo
  independientes por calendario (uno falla, el otro sigue); `salud()` resume por agenda sin IDs.
- Aceptación: `test_calendar_bloqueo.py` y `test_calendar_token.py` sin cambios y en verde.
- Commit: `refactor: calendar_sync trabaja por calendario (el modo actual no cambia)`.

**M4 · El motor lleva la agenda de punta a punta (M).**
- Archivos: `app/engine.py`; `tests/test_engine_multiagenda.py` (nuevo).
- Qué: `_ocupadas(agenda_id)`, `slots_disponibles(..., agenda_id)`, y que crear, mover, cancelar y el espejo usen la agenda de
  la cita. Sin paso nuevo todavía: con una agenda implícita (`''`) todo igual.
- Pruebas primero: con una sola agenda el flujo completo (agendar, anticipo, comprobante, mover, cancelar) da el mismo resultado
  que hoy; con dos agendas simuladas, una cita de la A no ocupa horarios de la B.
- Aceptación: suite completa igual que la base.
- Commit: `refactor: el motor trabaja con la agenda de cada cita`.

### Fase 2 — Descubrimiento de agendas

**M5 · Leer y sincronizar las agendas (L).** *(autorizado: `calendar_sync.py`)*
- Archivos: `app/calendar_sync.py`, `app/store.py`, `app/config.py`, `.env.example`; `tests/test_calendar_multi.py`.
- Pruebas primero: filtra por prefijo y por permiso de escritura; quita el prefijo y los espacios; calendario nuevo, renombrado y
  ausente según 3.2; lectura fallida o vacía no cambia nada y marca el estado degradado; TTL; con el prefijo vacío no se llama
  a Google.
- Aceptación: con el prefijo vacío, cero llamadas nuevas a Google y suite igual que la base.
- Commit: `feat: descubrir las agendas por el nombre de los calendarios`.

**M6 · Avisos de cambios, citas huérfanas y administración (M).**
- Archivos: `app/avisos.py`, `app/main.py`, `app/store.py`; `tests/test_agendas_admin.py` (usa `TestClient`: lo valida el CI).
- Pruebas primero: un aviso por alta, baja y renombre (no uno por ciclo); detecta citas futuras de agendas dadas de baja;
  `/api/agendas` exige `ADMIN_TOKEN` (503 sin configurar, 401 con token malo) y **no** devuelve IDs de calendario ni teléfonos;
  `/health` solo trae conteos.
- Commit: `feat: avisos de cambios de equipo, citas huérfanas y /api/agendas`.

**M7 · Bloqueos por agenda (M).** *(autorizado: `calendar_sync.py`)*
- Archivos: `app/calendar_sync.py`, `app/engine.py`; `tests/test_calendar_multi.py`, `tests/test_engine_multiagenda.py`.
- Pruebas primero: un evento manual (o de todo el día) en el calendario de Ana bloquea solo a Ana; si el calendario de Ana falla, Laura
  sigue disponible y Ana queda degradada (la disponibilidad cae a lo que haya en la base, como hoy con un calendario).
- Commit: `feat: los eventos manuales de cada calendario bloquean solo a su agenda`.

### Fase 3 — "Cualquiera disponible"

**M8 · Disponibilidad por agenda y asignación (M).**
- Archivos: `app/agendas.py` (nuevo), `app/engine.py`; `tests/test_agendas_asignacion.py` (nuevo, puro).
- Pruebas primero: unión de horarios sin repetidos y ordenada; `candidatas` respeta ocupados y bloqueos de cada agenda; `elegir` aplica la
  regla (misma de la última cita, menos citas ese día, menor `orden`); es determinista; con una sola candidata la devuelve.
- Commit: `feat: disponibilidad por agenda y regla de asignación`.

**M9 · Asignar al apartar y al confirmar (L).**
- Archivos: `app/engine.py`; `tests/test_engine_multiagenda.py`.
- Pruebas primero: con "cualquiera" la cita queda en la agenda elegida y el espejo en **su** calendario; carrera: dos clientas por el mismo
  horario terminan en agendas distintas (la segunda prueba la siguiente candidata); sin candidatas dice que se ocupó; también con
  anticipo (apartado, comprobante, comprobante tardío); la confirmación dice con quién.
- Commit: `feat: asignación automática de agenda con revalidación`.

### Fase 4 — Elegir con quién

**M10 · Paso "¿con quién?" (M).**
- Archivos: `app/engine.py`; `tests/test_engine_multiagenda.py`.
- Pruebas primero: una opción por agenda más "Cualquiera"; con una agenda se salta; con más de 3 opciones sale como lista; los títulos largos se
  recortan para mostrar sin cambiar el nombre guardado; las opciones no exponen IDs de calendario (solo `clave`); elegir una agenda
  muestra solo sus horarios; con 10 o más agendas se muestran 9 y se registra la advertencia.
- Commit: `feat: la clienta elige con quién agendar`.

**M11 · Mover y cancelar conservan la agenda (M).**
- Archivos: `app/engine.py`; `tests/test_engine_multiagenda.py`.
- Pruebas primero: mover ofrece solo horarios de la misma agenda; si la agenda está dada de baja, pasa a la manicurista; cancelar borra el
  evento en el calendario correcto y tolera que el calendario ya no exista (404 o 410 cuentan como borrado).
- Commit: `feat: mover y cancelar respetan la agenda de la cita`.

### Fase 5 — Reportes y documentación

**M12 · Con quién, en alertas y resumen (S).**
- Archivos: `app/avisos.py`, `app/concentrado.py`; `tests/test_concentrado.py` y nuevos.
- Pruebas primero: la línea "con quién" aparece solo si hay agenda; sección "Citas por reasignar"; los mensajes largos siguen repartiéndose.
- Commit: `feat: con quién va cada cita en los avisos y el resumen`.

**M13 · Documentación y alta de cliente (S).**
- Archivos: `docs/adr/0010-varias-agendas.md`, `README.md`, `.env.example`, una guía de alta (`docs/alta-cliente-agendas.md`).
- Contenido de la guía: cuenta de Google del negocio, crear los calendarios `Agenda · Nombre`, compartirlos con las chicas
  (permiso revocable), autorizar el token con el alcance suficiente, qué pasa al renombrar o dar de baja.
- Commit: `docs: ADR 0010 y guía de alta de varias agendas`.

## 5. Pruebas manuales (antes de ofrecérselo a un cliente)

Con una cuenta de Google de pruebas y el número de pruebas de Meta, **sin esperar a ningún cliente real**:
1. Crear tres calendarios: `Agenda · Ana`, `Agenda · Laura` y uno sin prefijo. El bot debe ver solo dos.
2. Agendar con "cualquiera" varias veces: reparte entre las dos según la regla; cada evento aparece en el calendario correcto.
3. Agendar eligiendo a Laura; confirmar que solo muestra los horarios de Laura.
4. Crear un evento de todo el día en el calendario de Ana: Ana desaparece ese día para "cualquiera" y para quien la elija.
5. Renombrar `Agenda · Laura` a `Agenda · Sofía`: la próxima cita sale con el nombre nuevo, y las citas viejas siguen con el nombre que tenían.
6. Quitarle el prefijo a un calendario con una cita futura: tras dos lecturas el bot deja de ofrecerlo y el dueño recibe el aviso de la cita
   por reasignar; la cita sigue siendo válida.
7. Quitar el acceso del token a un calendario (o desconectar la red): ese calendario se marca degradado y los demás siguen agendando.
8. Con anticipo activo: apartar con "cualquiera" y mandar el comprobante; la cita queda en la agenda asignada.

## 6. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Un renombre o borrado por error cambia el equipo en vivo | Prefijo obligatorio, baja suave con 2 lecturas, aviso al dueño por cada cambio |
| Google falla y el bot se queda sin equipo | Lista vacía o lectura fallida = error: se conserva la última lista buena y se avisa una vez |
| Dos clientas toman el mismo horario con "cualquiera" | Revalidar al crear, con choque por agenda y reintento con la siguiente candidata |
| Citas futuras de alguien que se va | Siguen siendo válidas, entran en "Citas por reasignar" y avisan al dueño |
| El token no alcanza para listar calendarios | Verificar el alcance antes de M5 y documentar la re-autorización (VERIFICAR 1) |
| Más lecturas a Google (una por agenda) | Caché de 60 s por calendario y TTL de 300 s para el listado; con 5 agendas son pocas llamadas por minuto (VERIFICAR 3) |
| Los IDs de calendario se filtran | No viajan a WhatsApp (solo `clave`), ni a `/health`, ni a `/api/agendas`, ni al repo |
| Complejidad del flujo del cliente | El paso "¿con quién?" se salta con una sola agenda; con el prefijo vacío no existe |

## 7. Rollback

- Apagar sin redesplegar: dejar vacío `CALENDAR_AGENDAS_PREFIJO` en Render. El bot vuelve al modo actual.
- La migración es aditiva: las columnas y la tabla nuevas no estorban al código anterior (todo `agenda_id` queda en `''`).
- Volver al código anterior es seguro mientras no existan citas con `agenda_id` distinto de `''`; si ya existen, resuélvelas antes.

## 8. Fuera de alcance (v2)

Panel web para el equipo; servicios y horarios distintos por persona; Telegram por persona; elegir por texto libre ("con Ana el viernes");
más de 9 agendas con paginación; reasignar citas desde un comando del dueño; preferencias de clientas más allá de "la misma de la última vez".

## 9. VERIFICAR (confirma en la documentación vigente antes de la tarea que lo usa)

1. **Alcance del token actual.** Lee el campo de alcances del JSON del token **sin imprimirlo** y confirma si permite listar calendarios.
   Si solo cubre eventos, hace falta re-autorizar con un alcance que incluya la lista de calendarios (M5). En el repo no hay documentado
   cómo se generó el token: documéntalo en M13.
2. **Campos de la lista de calendarios:** cuál usar para el nombre (el del propio calendario frente al renombrado por usuario), el rol de
   acceso, y cómo se comportan los calendarios ocultos o eliminados.
3. **Cuotas de la API** con N agendas leyendo cada 60 s.
4. **Límite de calendarios secundarios** por cuenta gratuita.
5. **Límites de WhatsApp** para botones y listas (cantidad y largo de los títulos) y que el formateador actual los respete.
6. **Permisos al compartir** un calendario con una empleada (ver o editar) y su efecto en lo que el bot lee.

## 10. Preguntas para la dueña del negocio

1. ¿Cuántas personas son, y cuántas más podría haber pronto?
2. ¿Las clientas suelen pedir a una persona en específico?
3. ¿Hoy cada una tiene su propio calendario o ella lo lleva todo en uno?
4. ¿Todas hacen todos los servicios y tienen el mismo horario? (Si no, se adelanta la v2.)
5. ¿Está de acuerdo con crear una cuenta de Google para el negocio y los calendarios con el prefijo?
6. ¿Quién verifica los depósitos y quién atiende cuando una clienta pide hablar con alguien?

## 11. Correcciones de la revisión previa a la implementación (2026-11-21)

Revisión completa en `docs/reviews/plan-multiagenda.md` (commit `7cb3915`, base `d7097f8`). **Estas tres
correcciones mandan sobre la sección 3.** Las tres las decidió el dueño; no son propuestas.

**C1 · Las citas viejas con `agenda_id=''` bloquean a todas las agendas (decidido).**
La migración deja `''` en todas las citas existentes. Si `ocupadas(agenda_id=…)` solo trae esa agenda, los
horarios ya vendidos vuelven a aparecer libres al encender el prefijo. Por eso:
- `agenda_id=''` significa **la agenda única del modo actual** y ocupa **en todas** las agendas.
- `store.confirmadas(agenda_id=…)` y `ocupados(agenda_id=…)` incluyen las filas con `agenda_id=''` además de las
  de esa agenda; `agenda_id=None` sigue devolviendo todo, como hoy.
- `agenda_id=''` **no** significa "cualquiera": el comodín de la asignación es `'*'` y solo vive en la sesión
  (sección 3.5); nunca se guarda en `citas`.
- Prueba obligatoria en M1: una cita con `agenda_id=''` aparece en `ocupadas()` de **cada** agenda activa.

**C2 · `habilitado()` no puede exigir `CALENDAR_ID` en modo multiagenda (decidido: arreglarlo en M3).**
`calendar_sync.habilitado()` (`app/calendar_sync.py:66-68`) es `bool(config.CALENDAR_ID and
config.CALENDAR_TOKEN_FILE)`; con `GOOGLE_CALENDAR_ID` vacío el espejo y los bloqueos se apagan **en silencio**.
En modo multiagenda el ID del calendario **se descubre**, así que la condición pasa a ser
`bool(config.CALENDAR_TOKEN_FILE and (config.CALENDAR_ID or agendas activas))`, sin excepción ni aviso silencioso:
si no hay token, `/health` lo dice. Además `salud()` deja de publicar `"calendar_id"` (`app/calendar_sync.py:87`)
en cuanto el modo multiagenda está activo: el principio 4 también aplica a `/health`. M3 incluye las dos pruebas.

> **Corrección posterior (descubierta al revisar la implementación).** La condición de C2 vale para el **espejo y los
> bloqueos** (`habilitado()`), pero **no** para el **descubrimiento**: en el primer arranque la tabla `agendas` está
> vacía y es la primera lectura de Google la que la llena. Si el descubrimiento (o el arranque del bucle de C3) también
> exigiera "agendas activas", jamás se leería ninguna. Por eso el descubrimiento depende solo de **prefijo + token**
> (`calendar_sync.multiagenda()`), y el arranque de la app y cada pasada del bucle usan
> `calendar_sync.necesita_bucle()` (= `habilitado() or multiagenda()`). Prueba que lo fija: `tests/test_agendas_arranque.py`.

**C3 · Las lecturas de Google salen del camino del mensaje (decidido: bucle de fondo).**
Hoy `engine._sincronizar_calendar()` (`app/engine.py:243-258`) refresca con un `asyncio.to_thread` por mensaje; con
caché por calendario serían N calendarios × `CALENDAR_TIMEOUT_S=8` **antes de contestarle a la clienta**. Por eso:
- Un bucle de fondo (tarea `asyncio` arrancada en el `startup` de `main.py`) refresca los bloqueos de cada agenda
  cuando vence su TTL, y las agendas cuando vence `CALENDAR_AGENDAS_TTL_S`.
- El motor **solo lee memoria** (`bloqueos()` y la tabla `agendas`); nunca espera a Google para contestar. Si la
  memoria está vacía o vencida, la disponibilidad cae a lo que hay en la base, como hoy (principio 6).
- `crear`/`mover`/`borrar` sí siguen llamando a Google dentro de `asyncio.to_thread`, pero con el timeout por
  calendario y fuera del turno de conversación.
- Esto cambia M3 (la caché deja de refrescarse desde el motor), M4 (el motor pierde el `_sincronizar_calendar`
  perezoso) y M7 (el bucle es quien refresca). El bucle entra en M4; M7 solo lo extiende a los bloqueos.

**Nota de conteo.** La línea base real de esta máquina es **535** pruebas coleccionadas (no 496+21): el
`pytest` del CI y el local coinciden con `TestClient` instalado. Corre el comando con `PYTHONPATH` limpio
(Hermes inyecta un paquete `tests` propio que tapa el del repo y deja 13 errores de colección).
