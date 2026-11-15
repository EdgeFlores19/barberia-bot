# ADR 0009 — El concentrado de depósitos: el corte del día para cuadrar a mano

- **Estado:** aceptado y desplegado el 2026-11-15 (main `3d88cc5`, `/health` `ok: true`)
- **Fecha:** 2026-11-15
- **Alcance:** cómo se entera la dueña de qué depósitos entraron, sin inventar un sistema de
  confirmación que no se quiere.
- **Cierra:** el punto «Resumen diario» que el ADR 0008 dejó fuera de su fase.

## Contexto

El anticipo (ADR 0008) se verifica a mano: llega el comprobante, se revisa y se confirma. Lo que
falta no es un flujo nuevo, es **el corte**: al final del día, la dueña necesita una lista con
teléfono y monto para cruzarla contra su estado de cuenta. Hoy no existe.

Lo que ya hay y no se toca:

- **Aviso instantáneo** por Telegram (`app/avisos.py`), uno por comprobante. Sirve para reaccionar,
  no para cuadrar: a las 20:00 hay que releer cuarenta mensajes.
- **`/api/citas`** ya exige `X-Admin-Token`, pero devuelve las citas **sin** `telefono` ni montos
  (`_SIN_DATOS_PERSONALES`): sirve para ver la agenda, no para cuadrar. El concentrado necesita
  justo esos dos campos, así que no se reutiliza ni se le quita el filtro.

Lo que está muerto: `anticipo.resumen_hora` (`"20:00"`) existe en `data/barberia.json` desde el
principio y **no programa nada**. No hay cron ni tarea de fondo. Y `resumen_diario: true` no es la
salida: apaga el aviso instantáneo y no manda nada, porque nunca hubo quien lo mandara.

## Decisión

Tres piezas, cada una con un solo trabajo:

1. **`GET /api/depositos`** (`app/main.py`) — el concentrado como datos **y** como texto
   (`?formato=texto`), protegido por el mismo `X-Admin-Token` que las demás rutas privadas.
   A diferencia de `/api/citas`, **sí** devuelve `telefono` y `anticipo_monto`. Sin candado no hay
   ruta: 401 sin token, 503 si `ADMIN_TOKEN` no está configurado (falla cerrado, nunca abierto).
2. **`app/concentrado.py`** — arma el reporte sin I/O: recibe `datos` y las filas, devuelve
   `filas`, totales y el texto listo para Telegram. Así se prueba de verdad, sin servidor.
3. **El disparador** — un bucle async dentro del proceso (`app/main.py`, `lifespan`) que cada
   minuto pregunta si ya pasó `resumen_hora` y si hoy no se ha mandado. `RESUMEN_AUTO=0` lo apaga
   sin tocar el JSON; el disparo manual sigue vivo en `POST /api/depositos/enviar`.

## Razones

- **La ventana es por fecha del DEPÓSITO (`comprobante_en`), no por fecha de la cita.** El corte es
  contra el banco: lo que hay que reconocer es el dinero que cayó hoy. La fecha de la cita va en
  cada línea, porque es lo que dice a qué servicio corresponde el dinero.
- **Las citas canceladas sí entran**, marcadas. El depósito existe; la devolución la decide la dueña
  según su política. Esconderlas haría que el corte no cuadre con el banco.
- **Idempotencia en SQLite** (`resumen_envios`, `store.resumen_hecho`/`resumen_marcar`): el bucle
  pregunta cada minuto y el plan de Render reinicia el proceso sin avisar. Sin la marca, el reporte
  saldría sesenta veces al día o se perdería con cada reinicio. La marca se escribe **solo si el
  envío salió** (`_telegram` ahora devuelve `bool`): si Telegram falla, el ciclo siguiente reintenta.
- **El bucle, no un cron externo.** Render Starter no trae cron y un cron fuera del servicio
  necesitaría otra credencial y otro lugar donde fallar. Un bucle de 60 s dentro del proceso no
  cuesta nada y muere con el proceso.
- **No se toca `/api/citas`** ni el esquema de `citas`: el concentrado es una **vista**, no un
  cambio de modelo. Las columnas que usa ya existían por el anticipo.
- **`resumen_diario` sigue en `false`.** El disparador no pasa por ahí; encenderlo apagaría el
  aviso instantáneo.

## Consecuencias

- La dueña recibe un mensaje a las 20:00 con el corte del día; si el proceso estaba caído, sale al
  volver. Un día sin depósitos manda «Sin depósitos registrados», que también es información.
- **A quién le llega hoy es al dueño del despliegue, no a la dueña.** El destino es
  `TELEGRAM_CHAT_ID` en el panel de Render; mientras el anticipo corra con datos ficticios, ahí va
  el chat de Edgar para pruebas. Cuando el bot pase a manos del negocio, el traspaso es cambiar esa
  variable al Telegram de la dueña — sin tocar código.
- **El aviso instantáneo y el concentrado comparten `TELEGRAM_CHAT_ID`**: cambiar el destino mueve
  los dos a la vez. Si el negocio pide que el anticipo llegue a una persona y el corte a otra, eso
  sí es trabajo: una variable aparte (`TELEGRAM_CHAT_ID_RESUMEN`) y una rama en `_telegram`.
- Telegram corta en 4096 caracteres: el texto se recorta a 3800 y avisa dónde seguir
  (`/api/depositos`). Un día de 40 depósitos cabe igual.
- `ADMIN_TOKEN` ya existe en producción (43 caracteres): no hay secreto nuevo que cargar.
- `store.reset()` borra también `resumen_envios`: reiniciar la demo permite volver a ver el
  concentrado del día.
- Si algún día se quiere avisar a la dueña por WhatsApp en vez de Telegram, cambia
  `avisos.notificar_concentrado`; el armado del reporte no se entera.
