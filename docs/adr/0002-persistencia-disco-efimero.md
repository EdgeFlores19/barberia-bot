# ADR 0002 — Persistencia en producción: SQLite efímera aceptada como demo

- **Estado:** aceptado
- **Fecha:** 2026-11-01
- **Alcance:** qué se hace con `barberia.db` mientras el bot siga en el plan free de Render.
- **Decisión del dueño:** mantener la base efímera y tratar el despliegue como demo, sin pagar
  disco ni migrar de motor todavía.
- **Relación con ADR 0001:** no deroga **D10**, lo vuelve operativo. D10 decía "no se migró a
  Postgres en esta fase"; este ADR fija **cuándo** se reabre y con qué camino.

## Contexto

El canal de WhatsApp quedó operativo el 2026-11-01 (`GET /health` → `envios_ok > 0`,
`envios_fallidos: 0`). Producción corre en Render `plan: free`, y `DB_FILE` apunta por defecto a
`BASE_DIR / "barberia.db"` (`app/config.py:13`), es decir al disco de la instancia, que Render
borra en cada deploy y en cada reinicio.

Qué se pierde en cada borrado, leído del código y no supuesto:

| Tabla | Qué guarda | Efecto de perderla |
|---|---|---|
| `citas` | folio, slot, servicio, nombre, teléfono, estado | Se pierden las citas confirmadas: bloqueante para cobrar |
| `wa_eventos` | una fila por `message.id` de Meta | Se pierde la idempotencia; un reintento de Meta **después** de un deploy puede contestarse dos veces |
| `wa_uso` | marcas de tiempo por teléfono | Se reinician los topes por hora/día (`wa_sumar` / `wa_contar_desde`) |
| `handoffs` | KPI de "pidió humano" | El KPI arranca de cero |

Qué **no** se pierde: `data/barberia.json` (servicios, precios y reglas) vive en el repo, y el
estado de conversación (`Sesion`) ya vivía en memoria, así que su pérdida no es nueva.

Un matiz que se suele describir mal: el disco efímero se borra en **deploy o reinicio**, no cuando
el servicio se duerme por inactividad. La dormida sí borra `/health` → `wa` (contadores en
memoria) y `llm: false` vuelve a evaluarse en el arranque frío.

## Decisión

**No se paga disco persistente ni se migra de motor mientras no haya un cliente real.** Se
mantiene SQLite sobre el disco efímero y se acepta el borrado como parte del estado "demo",
porque hoy el bot no atiende a nadie: el número es el de prueba de Meta, ambas funciones de
WhatsApp siguen en "Listo para prueba" sin App Review, y `SEED_DEMO=1` vuelve a sembrar la demo
en el arranque siguiente (`seed.sembrar_si_vacio`), así que el sitio nunca aparece vacío.

Se hace explícito además **qué código no hay que tocar**: `app/store.py` es el único módulo que
importa `sqlite3` y toda la conexión pasa por `_con()`. Cualquier cambio de motor futuro toca un
solo archivo, no la lógica de negocio.

## Alternativas evaluadas

| Opción | Costo | Código | Veredicto |
|---|---|---|---|
| **A.** Render Starter + disco persistente, `DB_FILE=/data/barberia.db` | ~$7 USD/mes + almacenamiento | **0 líneas** (variable de entorno + bloque `disk:` en `render.yaml`) | El camino preferido cuando importe |
| **B.** Postgres gestionado (Neon u otro; el free de Render caduca) | $0 | Reescribir `_con()` y las ~20 consultas de `store.py` (`?`→`%s`, `row_factory`, `IntegrityError`, generación de folio) + tests | Correcto y sin costo, pero cuesta una sesión y no aporta hasta que haya concurrencia real |
| **C.** Turso / libSQL remoto | $0 | Cambia el driver (HTTP) y el manejo de transacciones; el SQL es casi idéntico | Menos documentación y más superficie de fallo que A o B |
| **D.** Mantener la demo efímera | $0 | 0 | **Elegido ahora** |

Descartar A por costo hoy es una decisión de producto, no técnica: 7 dólares al mes no se
justifican mientras no exista la primera cita real que pueda perderse.

## Consecuencias

**A favor**

- Cero código y cero infraestructura nueva: la suite (192 tests) sigue intacta.
- La demo se autorregenera: un deploy a media demostración deja el negocio con horarios libres,
  no con un calendario roto.
- El cambio a persistencia real ya está preparado: `DB_FILE` es configurable desde el inicio, así
  que A es configuración, no desarrollo.

**En contra**

- Cualquier prueba con datos "reales" (el dueño agendando desde su teléfono) se pierde en el
  siguiente deploy, y eso puede confundirse con un bug del bot.
- Un reintento de Meta posterior a un deploy puede producir una respuesta duplicada, porque la
  deduplicación vive en `wa_eventos`.
- Los topes anti-abuso por hora se reinician con cada deploy.
- **Sigue siendo bloqueante para cobrar citas reales**: esto no cambia hasta que se ejecute el
  gatillo de abajo.

## Gatillo para reabrir esta decisión

Se reabre cuando ocurra **cualquiera** de estos, y no antes:

1. Existe la primera cita real que alguien pagaría por conservar.
2. Se pone `SEED_DEMO=0` para operar.
3. La verificación de negocio y el App Review de Meta están aprobados y hay número propio.

Al reabrir, orden de preferencia: **A** (0 código, ~$7 USD/mes) y, solo si se quiere evitar el
plan pagado, **B** con Postgres fuera de Render.

## Verificaciones pendientes (VERIFICAR)

1. Precios vigentes de Render (instancia Starter y almacenamiento por GB) al momento de decidir.
2. Que el plan free **no** permita discos persistentes en la fecha de la decisión.
3. Tamaño real de `barberia.db` con citas de un mes de operación, para dimensionar el disco mínimo.
