# Revisión — plan de varias agendas (un calendario por persona)

- **Estado:** revisión hecha sobre el plan adjunto; **nada implementado**.
- **Fecha:** 2026-11-20
- **Base del repo:** `main` en `d7097f8` (producción sirve `452fc02`).
- **Qué se revisó:** `plan-multiagenda.md` (5771 tokens) + el parche
  `0001-docs-plan-de-varias-agendas-un-calendario-por-person.patch`.
  El parche **aplica limpio** (`git apply --check` → rc 0; toca `AGENTS.md` y crea
  `docs/plans/plan-multiagenda.md`) y **no se aplicó**. Por eso en el repo todavía no existen
  ni el plan ni las referencias a este documento.
- **Cómo se revisó:** leyendo el código real (`app/engine.py`, `app/store.py`,
  `app/calendar_sync.py`, `app/slots.py`, `app/config.py`, `app/whatsapp_formato.py`,
  `app/main.py`, `tests/test_calendar_bloqueo.py`) y contando las pruebas en esta máquina.
  No se ejecutó el plan ni se creó ninguna rama.

## Veredicto

El plan está bien armado: fases sin cambio visible para la clienta, migración aditiva,
guardarraíl por prefijo (`CALENDAR_AGENDAS_PREFIJO` vacío = comportamiento actual), TDD por
tarea y una sección de VERIFICAR honesta. Se puede ejecutar.

Pero tiene **tres huecos que rompen la promesa de "no cambia nada"** y **cuatro afirmaciones
que el código desmiente**. Los tres bloqueantes son baratos de cerrar y conviene resolverlos
antes de empezar la fase 1, porque dos de ellos cambian el alcance de M1 y M3.

---

## 1. Bloqueantes

### B1. Al encender el prefijo, las citas ya agendadas dejan de ocupar

`engine.py:240` arma los ocupados con `store.confirmadas() + calendar_sync.bloqueos()`. El plan
filtra esos ocupados por agenda (`ocupadas(agenda_id)`), pero las citas anteriores a la
migración quedan con `agenda_id = ''` (es el `DEFAULT` de la columna), así que
`ocupadas(agenda_id='…')` **no las ve**: el día que se activa el prefijo, todos los horarios ya
vendidos vuelven a aparecer libres.

M1 lo prueba como "las citas viejas quedan con `''`" sin notar la consecuencia.

**Arreglo:** `''` significa *bloquea a todas las agendas*. Una condición explícita en
`ocupadas`/`confirmadas` (`agenda_id = ? OR agenda_id = ''`), y su prueba en M1. Si se prefiere
lo contrario (backfill de las futuras a la agenda del calendario actual), entonces es una tarea
propia en M5, no un supuesto.

### B2. `habilitado()` exige `CALENDAR_ID`, y el plan dice que en multiagenda se ignora

`calendar_sync.py:68` → `bool(config.CALENDAR_ID and config.CALENDAR_TOKEN_FILE)`. Si el dueño
levanta el modo multiagenda con `GOOGLE_CALENDAR_ID` vacío —lo que 3.7 permite— `bloqueo_activo()`
da `False` y **el espejo y los bloqueos se apagan en silencio**. Ninguna de las 13 tareas toca
`habilitado()`.

Además `salud()` publica hoy `"calendar_id": config.CALENDAR_ID or "(vacío)"`
(`calendar_sync.py:87`), o sea que `/health` **ya** filtra el ID del calendario: contradice el
principio 4 ("el ID no se muestra") y no está en la lista de tareas.

**Arreglo:** en modo multiagenda, `habilitado()` = hay token y hay al menos un calendario
declarado; y `salud()` deja de devolver `calendar_id` (devuelve `"configurado": true/false`).
Añadirlo explícitamente a M3.

### B3. N lecturas de Google dentro del camino del mensaje

`engine.py:255` refresca los bloqueos con un solo `asyncio.to_thread`. Con caché por calendario
ese único hilo haría N lecturas de eventos, cada una con `CALENDAR_TIMEOUT_S = 8`
(`config.py:67`), más la lectura de `calendarList`: con cinco agendas lentas, hasta ~40 s de
espera antes de contestarle a la clienta. Al confirmar es peor, porque va con `forzar=True`.

El plan lo trata como riesgo de cuota (riesgo 6), no de latencia.

**Arreglo:** un presupuesto por mensaje. Cualquiera de estas sirve y es barata:
(a) refrescar como máximo un calendario vencido por mensaje y repartir el resto entre mensajes;
(b) lanzarlos en paralelo con fecha límite global (`CALENDAR_TIMEOUT_S` total, no por agenda);
(c) mover el refresco a un bucle de fondo, como el del concentrado, y que el motor solo lea la
memoria. La (c) es la única que también arregla el costo al confirmar.

---

## 2. Afirmaciones del plan que el código no sostiene

| El plan dice | Lo que hay en el repo |
|---|---|
| M3: `test_calendar_bloqueo.py` y `test_calendar_token.py` quedan **sin cambios y en verde** | El fixture de `tests/test_calendar_bloqueo.py:53-55` sustituye `crear` por `lambda cita, d: None`, `mover` por `lambda ev, cita, d: ev` y `borrar` por `lambda ev: True`; el motor los llama posicionalmente vía `asyncio.to_thread` (`engine.py:1079`, `1244`, `1366`, `1486`). En cuanto la firma gane el tercer parámetro, los tres stubs revientan con `TypeError`. |
| Línea base: 496 ejecutables + 21 que solo corren en el CI | Aquí `pytest --collect-only -q` da **535 coleccionadas en 0.79 s** y la suite completa corre en local (535 passed): `TestClient` funciona y los 6 archivos que lo usan sí se ejecutan. `smoke_test.py` aporta 0. La cifra del autor no sirve como base. |
| Rollback: vaciar el prefijo y el bot vuelve al modo actual | Cierto para el código, falso para los datos: las citas creadas con `agenda_id` propio conservan su evento en el calendario de la persona y el código anterior borra contra `CALENDAR_ID` (`calendar_sync.py:185`). Los eventos espejo quedan huérfanos y el resumen anterior no tiene sección de "por reasignar". |
| Migración aditiva: no toca filas existentes | Cierto, pero `_migrar_citas` (`store.py:107-130`) reconstruye `citas` copiando la lista fija `_COLUMNAS_VIEJAS` (`store.py:65`). Hoy no rompe (el rebuild solo corre si la tabla no tiene `'pendiente'`), pero M1 debería fijar ese contrato con una prueba: si alguien añade `agenda_*` al rebuild, se pierden datos. |

**Sobre "sin cambios" en M3, hay salida limpia:** que `calendar_sync.crear/mover/borrar` tomen
`calendar_id=None` y, cuando sea `None`, lo lean de la propia cita
(`cita.get("agenda_id") or config.CALENDAR_ID`). Así el motor sigue llamando con dos argumentos,
los stubs actuales siguen sirviendo y el cambio queda contenido en `calendar_sync.py`, que es el
único módulo que el plan autoriza. Si aun así hace falta el tercer argumento, M3 debe **decir**
que se toca el fixture, no prometer que no.

---

## 3. Diseño: detalles que sí importan

- **`clave` de 8 hex de sha1 con `UNIQUE`.** Colisión no manejada y el fallo caería dentro del
  bucle de sincronización. Regenerar con sal en el alta: cinco líneas y una prueba.
- **Los límites de WhatsApp no son un VERIFICAR: ya están en el repo.** `whatsapp_formato.py:11-16`
  → 3 botones, 10 filas, título de fila 24 caracteres, de botón 20. `_lista` (50-53) **trunca en
  silencio** las opciones extra, sin "ver más". Las 9 agendas + "Cualquiera" caben exactamente en
  10 filas: sin margen, y con nombres recortados que pueden quedar idénticos para la clienta.
  Sugerencia: 4-5 agendas + "Otra persona", o paginar.
- **Baja con `ausencias >= 2` y TTL de 300 s** = 10 minutos para dar de baja a alguien por
  quitarle el acceso al calendario, que es indistinguible de borrarlo (el calendario simplemente
  desaparece de `calendarList`). Un enredo de sharing se lleva media agenda. Mejor umbral por
  tiempo (30-60 min) o 6+ lecturas, y tratar "sin permiso de escritura" distinto de "no está".
- **`generar_slots` tiene `limite=24` por defecto** (`slots.py:147`); el motor usa `limite=1000`
  (`engine.py:262,284`). La unión de "Cualquiera" debe generarse por agenda con 1000 y recortar al
  final, o el menú se corta a media tarde.
- **Regla de asignación #1 va por teléfono**, y en el chat web el teléfono puede venir vacío.
  Normalizar con `app/telefono.py` y decidir qué pasa cuando no hay teléfono (cae a "menos citas").
- **`/api/agendas` ya tiene patrón de autenticación:** reusar `_exige_admin` (`main.py:196-203`)
  da el 503/401 exacto que pide M6.
- **Choque por `slot` exacto** (`store.py:209-213`) y solape por duración en `slots.validar`
  conviven bien; falta solo la prueba explícita de "misma agenda, duraciones distintas".
- **"Citas por reasignar" en el resumen de las 20:00** se vuelve ruido diario: mostrarla solo
  cuando haya algo.

---

## 4. Estado de los VERIFICAR

- **1 (alcance del token): no comprobado.** No hay token local; el de producción está en
  `GOOGLE_TOKEN_JSON` y no hay llave para leerlo desde aquí. `salud()` tampoco expone los
  alcances. Antes de M5 conviene que `salud()` devuelva un booleano tipo `"puede_listar"`;
  así se verifica con un `curl /health` sin imprimir nada y queda documentado en M13.
- **5 (límites de WhatsApp): ya no es VERIFICAR.** Está fijado en `whatsapp_formato.py` y
  cubierto por pruebas. El plan debería citar esos números.
- **2, 3, 4 y 6: abiertos de verdad.** Bloquean M5 y M7, no M1/M2/M4.

---

## 5. Orden recomendado

1. Fase 1 tal cual, pero con **B1 resuelto dentro de M1** (semántica de `''`) y **M3 sin
   prometer tests intactos**.
2. Antes de M5: cerrar **B2** (`habilitado()` + quitar `calendar_id` de `/health`). Es pequeño y
   hoy `salud()` ya es una fuga.
3. Antes de M7: cerrar **B3** con el presupuesto de lecturas; la opción (c) —bucle de fondo— es
   la que además arregla el costo al confirmar.
4. **Fase 3 ("Cualquiera") es la que más código mueve y la que menos valor tiene si la dueña
   responde que las clientas sí piden a alguien en específico.** Las preguntas de la sección 10
   valen más que M8-M10: conviene preguntarlas antes de empezar la fase 3.

## 6. Decisiones que le tocan al dueño (no al asistente)

- ¿`''` bloquea a todas las agendas, o se hace backfill de las citas futuras a una agenda?
- ¿Se acepta el refresco en bucle de fondo (opción c), o se prefiere el presupuesto por mensaje?
- ¿Techo de agendas visibles en el menú de WhatsApp (4-5 con "Otra persona", o 9 sin margen)?
- ¿Umbral de baja de una agenda por desaparición del calendario?
- Las seis preguntas de la sección 10 del plan, en especial "¿piden a alguien en específico?".
