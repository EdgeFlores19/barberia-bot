# ADR 0006 — Plan Starter con disco persistente (reemplaza al ADR 0002)

- **Estado:** aceptado y aplicado
- **Fecha:** 2026-11-09
- **Alcance:** persistencia de `ambar.db` y plan de cómputo del servicio `navajazo-bot`.
- **Decisión del dueño:** pagar Render Starter para la demo, de modo que las pruebas y
  demostraciones no se pierdan al reiniciar, y quedarse con SQLite sobre disco.
- **Relación con ADR 0002:** lo **reemplaza**. El 0002 aceptaba el disco efímero *mientras no hubiera
  cliente real*; ese supuesto dejó de valer cuando el dueño empezó a vender pruebas de 350.
- **Relación con ADR 0001:** sigue sin migrarse a Postgres. D10 queda intacto.

## Contexto

Hasta hoy el servicio corría en `plan: free`. Dos consecuencias, ambas verificadas en el panel y no
supuestas:

1. **Disco efímero.** `DB_FILE` caía al valor por defecto `BASE_DIR / "barberia.db"`
   (`app/config.py:13`), dentro del árbol del repo. Render borra ese sistema de archivos en cada
   deploy y en cada reinicio, así que las cuatro tablas del ADR 0002 (`citas`, `wa_eventos`,
   `wa_uso`, `handoffs`) volvían a cero. Para una demostración eso significa agendar una cita,
   redeployar y que el cliente vea la agenda vacía.
2. **Suspensión por inactividad.** A los ~15 min sin tráfico el proceso se apagaba. El webhook de
   Meta tiene ventana de reintento corta: si el primer mensaje de una prueba llega a un proceso
   dormido, la conversación arranca con un error visible para quien está probando.

El dueño pagó Starter (`0.5c-512mb`) el 2026-11-09. Verificado por API: el servicio ya reporta
`plan: 0.5c-512mb` y **0 discos adjuntos** — es decir, el plan estaba pagado pero el disco no
existía, y `DB_FILE` seguía apuntando al disco efímero. Ese hueco es el que cierra este ADR.

## Decisión

**Un disco de 1 GB montado en `/var/data`, con `DB_FILE=/var/data/ambar.db`, declarado en el
blueprint junto al plan.**

```yaml
    plan: starter          # en render.yaml; si dijera `free` el sync bajaría el servicio
    disk:
      name: ambar-data
      mountPath: /var/data
      sizeGB: 1
```

Y la variable correspondiente:

```yaml
      - key: DB_FILE
        value: /var/data/ambar.db
```

Por qué 1 GB: una cita ocupa unas decenas de bytes y el precio es $0.25/GB/mes, así que 1 GB cubre
años de operación de un solo negocio. El mínimo de Render alcanza; pagar más no compra nada.

`store._con` crea el directorio si falta (`app/store.py:85`), así que el montaje no necesita que el
archivo exista de antemano. SQLite abre en modo WAL (`app/store.py:73`); los archivos `-wal` y `-shm`
viven junto a la base, dentro del mismo montaje, sin configuración extra.

## Consecuencias

**Buenas**

- Las citas sobreviven a deploys y reinicios: la demo deja de ser un teatro que se resetea.
- Se conserva la idempotencia de `wa_eventos`. Antes, un reintento de Meta que cayera después de un
  deploy podía contestarse dos veces; con disco, no.
- El proceso no se suspende, así que el primer webhook de una prueba encuentra el servicio caliente.
- Los topes `wa_uso` y el KPI de `handoffs` dejan de reiniciarse, que es lo que se quiere mostrar en
  una demostración con números reales.
- `SEED_DEMO=1` se vuelve casi inofensivo: `seed.sembrar_si_vacio` solo siembra si la agenda está
  vacía, y con disco ya no lo está.

**Malas / a vigilar**

- **El deploy pierde el cero-downtime.** Con disco adjunto Render no puede levantar la instancia
  nueva antes de apagar la vieja: hay unos segundos fuera de línea. Aceptable para un bot; conviene
  no deployar en hora pico.
- **Bajar de plan borra el disco.** Si `render.yaml` vuelve a `plan: free`, o alguien cambia el plan
  en el panel, Render descarta el disco y con él las citas. El plan queda declarado en el blueprint
  justo para que el sync no lo deshaga.
- **Render no respalda discos.** No hay snapshots automáticos. Mientras haya clientes de prueba
  pagando, la copia es manual (`sqlite3 /var/data/ambar.db ".backup /var/data/ambar-$(date +%F).db"`
  desde la shell del servicio, o descargar el archivo). Para un cliente permanente, el siguiente
  paso es Postgres, que sí trae respaldos y réplicas.
- **El disco no es un volumen de red.** No se comparte entre servicios: cada cliente necesitará su
  propio disco, o su propio Postgres.

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| Plan de cómputo Pro o superior | Resuelve lo mismo que Starter (no dormir, admitir disco) a un múltiplo del precio. Un bot de texto con SQLite no necesita 2 CPU ni 4 GB. |
| Plan Pro de *workspace* ($25/mes) | Es plataforma (audit logs, SOC 2, dominios), no cómputo. **No** quita el sueño de las instancias free, según la propia doc de Render. |
| Seguir en free con un ping para no dormir | No arregla el borrado en deploy, que es el problema caro: perder citas de una prueba pagada. Además el free carece de disco. |
| Migrar a Postgres ahora | Correcto a mediano plazo, pero implica tocar `store.py` completo y credenciales nuevas. Con tres clientes de prueba, el disco de $0.25 resuelve sin refactor. Se reevalúa al pasar a clientes permanentes. |
| Guardar la base en el repo | Reescribir el repo en cada cita; además el contenedor es de solo lectura salvo el disco y `/tmp`. |

## Verificación

- Local: `DB_FILE` en un directorio nuevo → `store` crea la carpeta, crea la base, siembra 2 citas y
  un segundo proceso lee las mismas 2 citas (`AMB-328D`, `AMB-ZTGF`). Persistencia confirmada.
- Remoto, después del deploy: `GET /health` debe seguir en `ok: true`, y `render.yaml` debe mostrar
  el disco `ambar-data` adjunto al servicio en el panel.
- Prueba de aceptación para el cliente: agendar una cita, forzar un redeploy, y confirmar que el
  folio sigue apareciendo en `GET /api/citas`.
