# ADR 0005 — El texto del LLM pasa por una guardia antes de llegar al cliente

- **Estado:** aceptado y aplicado
- **Fecha:** 2026-11-09
- **Commit:** `feat: guardia de salida y entrada del LLM contra prompt injection` (parche del dueño)
- **Alcance:** `app/llm.py` y `tests/test_llm_guardia.py`. No toca el motor.
- **Pedido del dueño:** «Implemente este parche… si es necesario usa opencode, y actualiza documentación».

## Contexto

El LLM (DeepSeek) redacta respuestas de FAQ. Su texto se manda al cliente **tal cual**: si un
mensaje del cliente logra que el modelo «obedezca» (ignorar reglas, cambiar de rol, revelar el
prompt), lo que escriba sale por WhatsApp sin revisión. En el peor caso inventa o filtra algo caro:
un precio, un descuento, un enlace de pago, el teléfono de un tercero.

El motor (`app/engine.py`) ya era determinista y el modelo **no tiene herramientas**: no puede
cancelar, agendar ni hacer handoff, solo devolver texto. Lo que faltaba era proteger ese texto.

## Decisión

**Tres capas, en este orden: el prompt solo pide; lo que protege es el código.**

| Capa | Qué hace | Dónde |
|---|---|---|
| Entrada delimitada | El mensaje del cliente viaja entre `<mensaje_cliente>…</mensaje_cliente>`, recortado a **600 caracteres**, con los controles ASCII y `<` `>` cambiados por `‹` `›` (no puede cerrar la etiqueta, ni partiéndola en dos) | `llm._delimitar` |
| Prompt explícito | El sistema dice que ese bloque es **dato**, no instrucciones, y qué hacer si el cliente intenta redirigir la conversación | `llm.responder` |
| Guardia de salida | La respuesta se revisa contra `_contexto(datos)`; si algo verificable no está en los datos del negocio, **se descarta** y se usa la respuesta local (`match_faq_local`) o el `fallback` | `llm._guardia` |

Motivos de descarte (`_guardia` devuelve la etiqueta; `responder` la añade como `"descartada"`):

| Motivo | Qué detecta |
|---|---|
| `vacia` | Texto vacío |
| `longitud` | Más de 600 caracteres o más de 8 líneas |
| `html` | Etiquetas (`<script>`, `<img onerror=…>`) |
| `prompt` | Marcas del propio prompt filtradas (`contexto:`, `reglas:`, «eres el asistente», «usa solo los datos», `mensaje_cliente`) |
| `enlace` | URL, dominio, correo o `@usuario` que no está en los datos (incluye `bit.ly`, `wa.me`, `www.…`) |
| `telefono` | Número inventado: se comparan los **últimos 10 dígitos** contra los teléfonos del contexto, así `+52 449 123 4567` y `449 123 4567` cuentan igual |
| `monto` | `$n` o `n pesos` que no existe en los datos |
| `promesa` | Descuento, promo, gratis, cortesía, regalo, 2x1, `n%`, sin costo, bonificación, reembolso, devolución, garantía — salvo que ya exista en el contexto (comparado sin espacios, para que `20 %` y `20%` pesen igual) |

**En el log solo quedan el motivo y el largo**, nunca el texto del cliente ni el de la respuesta. Si
el modelo falla, se registra **el tipo** de excepción (no el mensaje: algunas traen la URL con la key).

## Por qué una guardia heurística y no «confiar en el prompt»

Las instrucciones en el prompt se pueden negociar con otro texto; el código no. La guardia es
deliberadamente **conservadora**: prefiere descartar de más (el cliente recibe la respuesta local,
que es correcta y está anclada al JSON) que dejar pasar de menos. El costo de un falso descarte es
una respuesta más seca; el costo de un falso positivo es un descuento inventado por escrito.

## Límites medidos (no hay que creerle al diseño, hay que probarlo)

Con los datos sintéticos de `tests/test_llm_guardia.py`, estos casos **pasan la guardia** y quedan
documentados como límite conocido, no como bug:

| Respuesta del modelo | Resultado | Por qué |
|---|---|---|
| «Abrimos el domingo de 10:00 a 19:00.» | pasa | Las cifras y la palabra «domingo» existen en el contexto; la guardia no valida **semántica** (que 10:00-19:00 sea el horario del domingo) |
| «Manicura cuesta $350.» | pasa | `$350` es el precio real de **Gelish**; el monto existe, la atribución al servicio no se verifica |
| «Mis reglas son usar solo los datos del contexto.» | pasa | Fuga parafraseada: la marca del prompt está conjugada (`usar`), no aparece literal |

Mitigación real de las dos primeras: horarios, precios y disponibilidad los decide el motor
determinista (`app/engine.py`, `app/slots.py`); el LLM solo redacta FAQ. La tercera es cosmética:
revela el prompt, no datos de clientes ni de la base.

## Consecuencias

**A favor**

- Un modelo enganchado ya no puede inventar precio, promesa, enlace ni teléfono: 22 ataques del
  archivo de pruebas se descartan con el motivo esperado, y uno de ellos comprueba además que
  **aunque el modelo obedezca, el motor no cancela citas ni hace handoff**.
- El cliente no puede cerrar la etiqueta del mensaje (`</mensaje_cliente>` aparece una sola vez),
  y el mensaje largo se recorta antes de gastar tokens.
- Cada descarte deja rastro operativo (`ambar.llm`) sin datos personales.
- Fallback siempre disponible: sin key, sin red o con la guardia actuando, la respuesta local
  responde igual (`272 passed`).

**En contra / a tener en cuenta**

- Reglas de texto frágiles por naturaleza: un dominio nuevo o una promesa escrita de otra forma
  puede colarse. La guardia se corrige ampliando `_RE_*` y su caso en `tests/test_llm_guardia.py`.
- `_guardia` recalcula `_contexto(datos)` en cada respuesta: irrelevante al volumen de la demo.
- Si `datos` viniera incompleto, `_contexto` lanzaría y el `except` haría caer la respuesta a las
  reglas locales silenciosamente (queda el `log.warning` con el tipo de excepción).
- El texto del modelo sigue teniendo `max_tokens: 220`; el tope de 600 caracteres de la guardia es
  coherente con eso y debe revisarse si algún día sube.

## Verificaciones pendientes (VERIFICAR)

1. Probar la guardia contra el modelo real con los ataques de `ATAQUES` (hoy: modelo simulado).
2. Medir cuántos descartes produce en tráfico normal (si son muchos, el prompt del sistema pide
   demasiado y hay que ajustarlo, no bajar la guardia).
3. Evaluar si conviene subir `MAX_ENTRADA_CHARS` para clientes que mandan audios transcritos largos.
