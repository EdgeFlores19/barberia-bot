# ADR 0010 — Varias agendas: una persona por calendario, sin tocar el flujo de siempre

- **Estado:** aceptado; implementado en `app/agendas.py`, `app/store.py` y `app/engine.py`
  (commits de M5–M12)
- **Fecha:** 2026-11-21
- **Alcance:** cómo entran varias manicuristas al mismo bot, cómo se reparte una cita cuando la
  clienta no elige a nadie, y qué pasa cuando alguien renombra o borra su calendario.
- **Cierra:** el punto «multi-estilista» que el README dejaba fuera de v1 a propósito.

## Contexto

El bot v1 agendaba contra **un** calendario y **una** agenda: `GOOGLE_CALENDAR_ID` más los horarios
de `data/barberia.json`. Con dos manicuristas eso miente en las dos direcciones: la clienta ve un
horario ocupado porque la otra persona está trabajando, y el negocio pierde capacidad porque no
puede vender dos citas a la misma hora.

La dueña no quiere un panel de administración: quiere que la clienta pregunte y quede. Tampoco
quiere que el bot le pregunte «¿con quién?» a cada rato si trabaja sola.

Lo que ya existe y no se toca:

- **SQLite es la fuente de verdad** (`store.py`): choque de horario, folio, cancelar y reagendar.
- **Calendar es espejo y lectura de bloqueos** (ADR 0007): nunca puede tumbar una reserva.
- **Las lecturas de Google salen del turno de conversación** (`ff7358b`): un bucle de fondo
  mantiene la caché y el motor solo lee memoria.

## Decisión

### 1. El equipo se descubre por el nombre del calendario

`CALENDAR_AGENDAS_PREFIJO` (por ejemplo `Agenda · `) marca qué calendarios del token son agendas.
Un calendario sin el prefijo **no se ve**: así la dueña puede tener su calendario personal en la
misma cuenta sin que el bot lo ofrezca. Se listan con `calendarList.list` cada
`CALENDAR_AGENDAS_TTL_S` segundos (300 por defecto) y cada agenda lleva una `clave` corta
derivada de su `calendar_id`.

**Los `calendar_id` no viajan al cliente.** Al menú y a la sesión va solo la clave y el nombre; el
`calendar_id` es identidad interna (`store.agenda_de`, `store.agenda_por_clave`). Tampoco salen en
`/health` ni en `/api/agendas`.

### 2. La baja es suave, y no borra citas

Si un calendario desaparece del listado (renombrado sin prefijo, sin acceso, borrado) se marca varias
lecturas seguidas (`AUSENCIAS_PARA_BAJA`, 6 por defecto ≈ 30 min con el TTL de 300 s; se ajusta con
`CALENDAR_AGENDAS_AUSENCIAS`) antes de darlo de baja: un fallo de red o un permiso quitado por error no debe
vaciar el equipo ni sacar a nadie al instante. Las citas ya hechas **siguen siendo válidas** y caen en «Citas por reasignar»
(`store.citas_huerfanas`), que se publica en el resumen diario. Al renombrar, la próxima cita sale
con el nombre nuevo; las viejas conservan el nombre que tenían (`agenda_nombre` se guarda en la
cita, no se resuelve al leer).

### 3. «Cualquiera» se resuelve al crear, no al mostrar

Con `agenda_id = "*"` (`engine.CUALQUIERA`) el menú de horarios es la **unión** de las agendas
(`agendas.horarios_libres`) y la elección se hace en el momento de crear: `agendas.elegir` ordena
por última cita con esa persona → menos citas ese día → menor orden → clave. Se revalida contra
SQLite y, si el horario se ocupó entre mostrar y confirmar (`store.SlotOcupado`), se prueba la
siguiente candidata y se reintenta. Nunca se guarda `"*"` en una cita.

Con dos o más agendas activas el flujo pregunta «¿con quién?» antes del día; con **una sola** se
adopta su clave en silencio y con **cero** todo cae al modo de siempre (`agenda_id = ""`),
que es también lo que pasa con `CALENDAR_AGENDAS_PREFIJO` vacío.

### 4. Mover y cancelar respetan la agenda de la cita

Reagendar ofrece **solo** los horarios de esa agenda y borra el evento en **su** calendario. Un
`404`/`410` de Google cuenta como «ya no estaba»: la cita se mueve o se cancela igual. Si su agenda
está dada de baja, la conversación pasa a la manicurista en vez de ofrecer horarios de un calendario
que nadie ve.

## Alternativas consideradas

- **Un calendario por persona con `attendees` en el mismo evento.** Se descartó: el reparto de
  huecos necesita saber *quién* está libre, y con un solo calendario no hay dónde ponerlo sin leer
  todos los eventos y desenredarlos.
- **Guardar `"*"` en la cita y resolverlo al leer.** Se descartó: el horario quedaría vendido dos
  veces si dos clientas lo piden a la vez, y el espejo no sabría a qué calendario ir.
- **Preguntar siempre «¿con quién?».** Se descartó por usabilidad: con una sola agenda es un paso
  de más (y el negocio real empieza así).
- **Panel para que la dueña asigne.** Fuera de alcance v1: el valor está en que el reparto ocurra
  durante la conversación.

## Consecuencias

- **A favor:** no hay migración destructiva (`agenda_id`/`agenda_nombre` vacíos = comportamiento
  anterior); el reparto es una función pura (`app/agendas.py`) con pruebas propias; apagar la
  función es vaciar `CALENDAR_AGENDAS_PREFIJO`.
- **En contra:** una lectura a Google por agenda (mitigada con caché de 60 s por calendario); el
  menú de personas admite hasta 9 (10 filas es el tope de la lista de WhatsApp) y no pagina; elegir
  por texto libre («con Ana el viernes») queda para v2.
- **Riesgo asumido:** un renombre o borrado por error cambia el equipo en vivo. Por eso el prefijo
  es obligatorio, la baja tarda unos 30 minutos (6 lecturas) y cada cambio avisa al dueño.
