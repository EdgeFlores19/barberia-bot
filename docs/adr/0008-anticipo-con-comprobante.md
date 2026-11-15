# ADR 0008 — Anticipo al agendar, con comprobante y verificación del dueño

- **Estado:** aceptado (fase 1; el resumen diario queda para la fase 2)
- **Fecha:** 2026-11-16
- **Alcance:** `app/anticipo.py` (nuevo), `app/store.py`, `app/engine.py`, `app/whatsapp_webhook.py`,
  `app/avisos.py`, `app/main.py`, `app/config.py` y el bloque `anticipo` de `data/barberia.json`.

## Contexto

Una clienta potencial cobra un anticipo fijo al agendar (para frenar las citas que no se
presentan) y no quiere pasar el día verificando pagos uno por uno. El bot confirmaba la cita
al instante, sin dinero de por medio.

## Decisión

1. **Es configurable por negocio** (bloque `anticipo`, apagado por defecto). Los valores
   acordados para la prueba: $200, apartado de 60 min (30 si la cita es hoy), alerta inmediata si
   faltan menos de 24 h, cancelar sin devolución, mover 1 vez con 24 h o más conservando el anticipo.
   Los textos que ve la clienta salen de esa misma configuración: el aviso previo al pago dice
   siempre lo que el sistema de verdad hará.
2. **Flujo (solo WhatsApp):** servicio → horario → nombre → **condiciones + botón "Acepto"** (la
   aceptación queda guardada con fecha y hora) → cuenta de pago con el folio como concepto y el
   horario **apartado** → la clienta manda **foto, captura o PDF** → la cita se confirma.
3. **El bot no verifica el pago.** Cualquier imagen o PDF cuenta; por eso el mensaje dice "la
   manicurista verificará tu pago" y el dueño recibe el aviso. Si algo no cuadra, ella contacta a la
   clienta a mano.
4. **El apartado se libera solo.** Un apartado bloquea el horario mientras `vence > ahora`; no hay
   ningún proceso que lo libere. Una clienta tiene un solo apartado: pedir otro libera el anterior.
5. **Un comprobante tardío no se pierde.** Si el apartado venció pero el horario sigue libre, se
   confirma y se avisa como "tardío". Si ya lo tomaron, o no hay apartado, pasa a la manicurista
   con el detalle (la clienta pudo haber pagado).
6. **El comprobante no depende de la sesión.** Se busca el apartado por el teléfono (últimos 10
   dígitos): la sesión caduca a los 30 min y el apartado dura 60.
7. **El anticipo no pasa del precio del servicio** cuando se descuenta del total (un servicio de
   $180 con anticipo de $200 cobra $180).
8. **Cancelar y mover siguen la política**, no la ventana de 2 h: se puede cancelar siempre, pero la
   clienta confirma sabiendo qué pasa con su dinero; mover se permite según `reagendar`.
9. **Datos de pago en variables de entorno** (`ANTICIPO_BANCO`, `ANTICIPO_CLABE`,
   `ANTICIPO_TITULAR`), nunca en el repo. `app.main` **no arranca** con el anticipo activo si faltan o
   si la CLABE no pasa su dígito de control.
10. **Migración.** SQLite no deja cambiar un `CHECK`: `store._migrar_citas` reconstruye `citas` en
    una sola transacción (si falla, se deshace). Con disco persistente es el punto delicado:
    respaldar el archivo antes del primer arranque con esta versión.

## Fuera de esta fase

- **Resumen diario** para el dueño (`resumen_diario` ya existe en la configuración, en `false`:
  mientras tanto, cada comprobante se avisa en el momento).
- Reenviar la imagen del comprobante al dueño (la media de WhatsApp caduca en minutos; hoy solo se
  guarda su identificador, no el archivo).
- Cancelar por parte del dueño (un teléfono "admin" que se salte el candado de folio).
- Anticipo en el chat web (no puede subir archivos): redirige a WhatsApp.

## Consecuencias

- Con `activo: false` el comportamiento no cambia.
- El monto, las políticas y las horas viven en el JSON de cada negocio; lo único que hay que
  tocar para otro cliente es ese bloque y las variables de pago.
