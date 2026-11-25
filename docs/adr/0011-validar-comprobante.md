# ADR 0011 — El bot lee el comprobante del anticipo y solo confirma si cuadra

- **Estado:** propuesto (código y tests escritos; falta probarlo con comprobantes reales)
- **Fecha:** 2026-11-22
- **Alcance:** `app/comprobante.py` (nuevo), `app/llm.py` (`leer_comprobante`), `app/wa_cliente.py`
  (`descargar_media`), `app/store.py` (`comprobantes_vistos`, `extender_apartado`), `app/engine.py`
  (`_recibir_media`), `app/avisos.py`, `app/anticipo.py`, `app/config.py`, `app/main.py` y el bloque
  `anticipo` de `data/barberia.json`.
- **Relación:** cambia la decisión 3 del [ADR 0008](0008-anticipo-con-comprobante.md) ("el bot no
  verifica el pago") **solo cuando** `anticipo.validar_comprobante` está en `true`. Apagado, nada cambia.

## Contexto

Con el ADR 0008 cualquier imagen o PDF confirmaba la cita. El anticipo ($200) ya frena a quien no
piensa presentarse, pero una captura cualquiera (o la de otra persona) bastaba para apartar un horario.
Se quiere que el bot, como mínimo, compruebe que el comprobante corresponde a este pago.

Un comprobante es una imagen y se puede editar: esto **sube el costo de la trampa, no la elimina**. La
única prueba de que el dinero llegó sigue siendo el banco (la dueña lo cuadra con el concentrado, ADR 0009).

## Decisión

1. **El modelo lee, el código decide.** Un modelo con visión solo transcribe campos del comprobante
   (`monto`, `fecha`, `clave_rastreo`, `cuenta_destino`, `beneficiario`, `concepto`) a un JSON.
   `comprobante.parsear` lo sanea y `comprobante.evaluar` compara con reglas fijas. Lo que devuelve el
   modelo es un dato no confiable (la imagen puede traer texto que parezca una instrucción): nunca se
   le muestra a la clienta ni se interpreta como orden. Es el mismo principio del ADR 0005.
2. **Modelo:** DeepSeek con visión (`DEEPSEEK_VISION_MODEL`, por defecto `deepseek-flash`), con la misma
   `DEEPSEEK_API_KEY` y la misma URL que el resto del bot, vía `httpx` (sin dependencias nuevas).
   Acepta JPEG, PNG, WebP y GIF; **un PDF no se lee y va a revisión manual**.
3. **Qué se comprueba:**
   - **Monto** exactamente igual al anticipo de esa cita.
   - **Destino:** la cuenta visible coincide con la CLABE del negocio (completa, o por los últimos 4 si
     viene enmascarada) o el beneficiario coincide con el titular (dos palabras en común). Una
     contradicción no pasa; si no se ve ninguno de los dos, tampoco.
   - **Fecha** de hoy o posterior al día del apartado, nunca futura.
   - **Clave de rastreo** no vista antes en **otra** cita (`comprobantes_vistos`). Se registra aunque el
     comprobante falle otra regla, para que un comprobante rechazado tampoco sirva en otra cita.
4. **El folio es blando.** Muchos bancos no muestran el concepto: si cuadra todo lo demás, la cita se
   confirma y el aviso a la dueña dice "faltó el folio". Sin clave de rastreo también pasa, con aviso.
5. **Si no cuadra, la cita sigue apartada y la dueña revisa a mano** (no se rechaza a la clienta de
   frente ni se confirma a ciegas). El apartado se alarga `revision_horas` (12 por defecto) sin pasarse
   del propio horario de la cita. La clienta recibe "la manicurista lo está revisando y te confirma".
   Si el apartado ya había vencido, no se alarga (el horario no queda retenido) y se avisa como tardío.
6. **Falla cerrado:** PDF, archivo que no baja, lector caído o respuesta inservible = revisión manual,
   nunca confirmación automática.
7. **Interruptor en Render:** la variable `VALIDAR_COMPROBANTE` (`1`/`0`) manda sobre el JSON, igual
   que `ANTICIPO_ACTIVO`; vacía, decide `anticipo.validar_comprobante`. Para una demo basta con ponerla
   en `0` y el bot confirma con cualquier imagen, como antes. Nunca enciende nada si el anticipo está apagado.
8. **Arranque:** con `validar_comprobante` activo y sin `DEEPSEEK_API_KEY`, `app.main` no arranca
   (igual que con los datos de pago, ADR 0008 #9).

## Fuera de esta fase

- **Confirmar desde la revisión.** Hoy, cuando la dueña revisa un comprobante y está bien, no hay un
  botón para confirmar la cita: hace falta un endpoint protegido con `ADMIN_TOKEN` o una acción en
  Telegram que llame a `store.registrar_comprobante` y cree el evento en Calendar. Sin eso, la cita
  revisada queda apartada hasta que vence.
- Reintento automático de la lectura si el modelo falla una vez.
- Leer PDFs (necesitaría extraer texto o convertir a imagen: dependencias nuevas).

## Riesgos y cosas por verificar

- **Calidad de lectura:** el reescalado de imágenes grandes puede estropear dígitos pequeños (clave de
  rastreo, cuenta). Probar con comprobantes reales de los bancos de las clientas antes de encender.
- **Privacidad:** el comprobante es un dato financiero y ahora viaja al proveedor del modelo. El aviso de
  privacidad (`static/privacidad.html`, secciones 2 y 5) hoy dice que el modelo solo recibe texto y pide
  no compartir datos financieros: hay que actualizarlo antes de encender esto en producción.
- **Meta:** ruta y campos de la descarga de media (`descargar_media`) marcados VERIFICAR contra la
  documentación vigente.
- El nombre del modelo con visión cambia con frecuencia (`DEEPSEEK_VISION_MODEL`): VERIFICAR en la
  documentación de DeepSeek.

## Consecuencias

- Apagado (por defecto) no cambia el comportamiento ni toca al proveedor del modelo.
- Costo: una llamada con una imagen por comprobante (centavos de peso al mes con el volumen de un estudio).
- Para otro negocio solo cambia el bloque `anticipo` y las variables de pago.
