# ADR 0007 — Los eventos manuales de Google Calendar bloquean huecos

- **Estado:** aceptado
- **Fecha:** 2026-11-10
- **Alcance:** `app/calendar_sync.py`, `app/engine.py` y `app/slots.py` (soporte de `fin`).
- **Relación con el diseño anterior:** hasta hoy Calendar era un espejo de una sola vía
  (bot → Calendar) y el README decía que "nunca se usa para decidir disponibilidad". Eso se
  matiza: se lee para **bloquear**, no para **autorizar**.

## Contexto

La manicurista ve su agenda en Google Calendar y puede anotar ahí, a mano, una cita que le
pidieron por otro medio. El bot no lo sabía y podía ofrecer ese mismo horario: doble reserva.

## Decisión

1. **Qué se lee.** `events.list` del calendario configurado, de ahora a `dias_adelante + 1`,
   con `singleEvents=true` y solo los campos necesarios (sin títulos). No se usa `freeBusy`
   porque mezcla en un solo bloque las citas que el propio bot espejó, y esas ya están en SQLite.
2. **Qué bloquea.** Todo evento con hora o de todo el día, salvo los cancelados, los marcados
   como "libre" (`transparent`), los que la dueña rechazó y los que lleva la marca
   `Reservado por el bot` en la descripción (el espejo).
3. **Cómo entra al motor.** Cada evento es una "cita ocupada" con su `fin` propio
   (`slots._intervalos_ocupados` lo respeta). Así `generar_slots` y `validar` no cambian de lógica.
4. **Cuándo se lee.** Caché de 60 s, que se revisa al empezar cada mensaje que pasó los topes.
   Además se **revalida sin caché al confirmar** una cita o un cambio de horario, que es donde
   importa la carrera.
5. **Si Calendar falla, el bot sigue.** SQLite es la fuente de verdad (ADR 0001). El fallo se
   anota en `/health` (sin el texto de la excepción, que lleva el id del calendario), se avisa
   **una vez** por Telegram y se avisa de nuevo cuando se recupera. Tras un fallo no se reintenta
   durante 120 s, para no esperar el timeout en cada mensaje.
6. **Apagable.** `CALENDAR_BLOQUEO=0` desactiva la lectura sin tocar el espejo.

## Consecuencias

- Con Calendar caído, el bot puede ofrecer un horario ocupado a mano. Es la compensación elegida:
  una reserva que falla por Calendar es peor que una doble reserva que la dueña ve en su aviso.
- **El token necesita permiso de lectura de eventos.** Si solo puede crear, la lectura falla con
  `HTTP 403` y se ve en `/health`; hay que regenerar el token.
- El espejo sigue siendo de una vía: si la dueña **mueve o borra a mano** un evento que creó el
  bot, SQLite no se entera. Queda fuera de este ADR.
- Un evento de todo el día bloquea todo ese día, aunque sea un recordatorio. Quien use el mismo
  calendario para recordatorios debe marcarlos como "libre" o usar otro calendario.
