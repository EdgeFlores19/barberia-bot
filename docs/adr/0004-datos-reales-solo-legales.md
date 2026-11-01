# ADR 0004 — Los datos reales del negocio viven solo en los documentos legales

- **Estado:** aceptado y aplicado
- **Fecha:** 2026-11-01
- **Commit:** `c25bf86` — `fix(datos): dirección y teléfono ficticios en la demo, no los del aviso de privacidad`
- **Alcance:** qué datos del negocio usa la demo y dónde pueden aparecer los reales.
- **Corrección del dueño:** "Encontré un problema en el `barberia.json`, dejaste esta info y eso solo
  era para el aviso de privacidad y de condiciones" (2026-11-01).

## Contexto

Para que el aviso de privacidad y las condiciones de uso fueran válidos, hay que publicar los datos
del responsable: **domicilio Vidal Roldán Ávila #105, Col. Morelos 2, Aguascalientes, Ags., C.P.
20298**, **teléfono 449 548 5324** y **correo nodeva3010@gmail.com**. Al construir la demo, esos
mismos valores se copiaron al JSON del negocio (`data/barberia.json`), que es la fuente de lo que el
bot dice en el chat.

Eso es un problema por dos razones:

1. **Es el domicilio de una persona, no un local comercial.** El aviso lo exige por obligación
   legal; repetirlo en la demo lo expone sin que haga falta.
2. **La demo es pública y ficticia.** Un visitante que pregunta "¿dónde están?" recibe una
   dirección real y una indicación de que ahí atienden, cosa que la demo no debe afirmar.

## Decisión

**Dos conjuntos de datos, separados por destino:**

| Dato | Demo (`data/barberia.json`) | Documentos legales (`static/privacidad.html`, `static/terminos.html`) |
|---|---|---|
| Nombre | `Ambar` | `Ambar` |
| Dirección | `Av. Francisco I. Madero 328, Col. Centro, Aguascalientes, Ags., C.P. 20000` (ficticia) | **Vidal Roldán Ávila #105, Col. Morelos 2, C.P. 20298** (real, obligatoria) |
| Teléfono | `449 000 0000` (ficticio) | **449 548 5324** (real, obligatoria) |
| Correo | — | **nodeva3010@gmail.com** (real, obligatoria) |
| Precios y servicios | ficticios | no aplica |

La dirección ficticia **no se inventó nuevo**: es la que ya respondía la FAQ "¿dónde están?" en el
motor, así que el cambio no altera el guion de la demo, solo deja de mezclar el domicilio legal con
la respuesta del bot. El teléfono ficticio es una secuencia de ceros (`449 000 0000`), elegida para
que no pueda confundirse con una línea asignada.

Reglas que quedan fijas:

- Los datos reales aparecen **únicamente** en las dos páginas legales, que tienen que publicarlos.
- Ningún archivo de demo (JSON, seed, README, capturas, tests funcionales) repite domicilio,
  teléfono ni correo reales. `tests/test_privacidad.py` es la excepción deliberada: **verifica** que
  los legales los contengan.
- No se inventa RFC, razón social ni un correo ARCO distinto del que ya existe.
- Si algún día la demo se convierte en el negocio real, se cambia el JSON por los datos verdaderos;
  la separación de hoy no impide operar, solo evita publicarlos de más mientras sea muestra.

## Consecuencias

**A favor**

- El domicilio particular deja de estar en el archivo que alimenta al bot público.
- El motor sigue funcionando sin cambios: solo se editaron 2 líneas del JSON.
- Los legales no se tocaron: los tests (192 passed) verifican que siguen con los datos reales.

**En contra / a tener en cuenta**

- La demo ahora dice una dirección que no corresponde al negocio real; quien vea ambas cosas
  (demo y aviso de privacidad) notará la diferencia. Es el precio de separar los datos, y es
  preferible a publicar el domicilio donde no toca.
- El teléfono aparece en **8 mensajes** del motor (`app/engine.py`, `datos['telefono']`) y la
  dirección en 2 (respuesta de ubicación y confirmación de cita): cualquier dato que se cambie en el
  JSON cambia el texto del bot, sin tocar código.
- El historial de git conserva los datos reales en las versiones anteriores del JSON. **No se
  reescribe el historial**: ya están publicados a propósito en los legales.

## Decisiones que no se tomaron aquí (dueño)

1. **`"instagram": "@ambar.unas.ags"`** (`data/barberia.json:6`): si la cuenta existe y es del
   negocio, se queda; si no, debería ser ficticia o quitarse.
2. **Qué número quedará como WhatsApp público** del negocio: el 449 548 5324 está declarado en los
   legales, pero el canal de Meta hoy usa el número de prueba.
3. **Confirmar que el teléfono pueda publicarse** en el sitio y en el aviso (hoy ya está en ambos).

## Verificaciones pendientes (VERIFICAR)

1. Que `449 000 0000` no esté asignado a nadie (es improbable, pero no se comprobó).
2. Que `Av. Francisco I. Madero 328, Col. Centro` exista como dirección plausible en Aguascalientes
   — se reutilizó de la FAQ anterior, no se validó.
3. Que el aviso de privacidad cumpla el resto de la LFPDPPP (issue #5, abierto).
