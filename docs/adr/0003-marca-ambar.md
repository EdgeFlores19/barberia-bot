# ADR 0003 — Identidad pública del producto: la marca es **Ambar**

- **Estado:** aceptado y aplicado
- **Fecha:** 2026-11-01
- **Commit:** `367c5c5` — `rebrand: el nombre correcto es Ambar (antes Nodeva)`
- **Alcance:** qué se renombra, qué se conserva con el nombre viejo y por qué.
- **Decisión del dueño:** "el nombre correcto sera ambar" (2026-11-01).

## Contexto

La demo se publicó como **Nodeva** en el JSON del negocio, el chat web, el título de la app y los
dos documentos legales. El nombre comercial correcto es **Ambar**; "Nodeva" era el nombre de
trabajo. Como el sitio es público y las páginas de privacidad y condiciones ya están en revisión de
Meta, el desajuste no es cosmético: el nombre equivocado quedaba en documentos con validez legal.

Un detalle que ayudó a confirmar la intención: el nombre nuevo ya estaba presente en el repo sin
haber sido decidido explícitamente — el Instagram de la demo era `@ambar.unas.ags` y el prefijo de
folio ya era `AMB` (`app/store.py:17`, `PREFIJO_FOLIO = "AMB"` → `AMB-7K4Q`).

## Decisión

**La marca del producto, del sitio y de los documentos legales es «Ambar».** Se escribió **sin
acento** porque así lo escribió el dueño; queda pendiente su confirmación de si debe ser «Ámbar»
(ver "Decisiones que no se tomaron aquí").

Se reemplazó **«Nodeva» → «Ambar»** en **7 archivos / 18 ocurrencias**, conservando UTF-8 y sin
tocar otra cosa:

| Archivo | Ocurrencias | Qué se ve |
|---|---|---|
| `data/barberia.json` | 1 | `"nombre": "Ambar"` — lo lee el motor en saludos, confirmaciones y `/api/negocio` |
| `static/index.html` | 2 | `<title>` y cabecera del chat |
| `static/privacidad.html` | 5 | responsable del tratamiento y pie de página |
| `static/terminos.html` | 6 | identificación del servicio y pie de página |
| `README.md` | 1 | título del repo |
| `app/main.py` | 1 | `FastAPI(title="Ambar — bot demo")` |
| `tests/test_privacidad.py` | 2 | aserciones de marca en los legales |

(Conteo verificado sobre el contenido del commit: `git show 367c5c5:<archivo> | grep -o Ambar | wc -l`.)

Verificación en runtime (servidor local, no solo tests): `GET /` renderiza «Ambar» y cero
«Nodeva»; el saludo pasa a *"¡Qué onda! Soy el asistente de Ambar."*; `GET /api/negocio` devuelve
`"nombre": "Ambar"`; `/privacidad` y `/terminos` sirven 5 y 6 ocurrencias de la marca.
Suite: **192 passed / 192 recogidos**. `git grep -i nodeva` solo devuelve el correo real.

## Lo que se conserva con el nombre viejo (a propósito)

| Elemento | Valor | Por qué no se renombra |
|---|---|---|
| Correo de privacidad | `nodeva3010@gmail.com` | Es un buzón **existente** y es el canal que el aviso de privacidad declara para derechos ARCO. Renombrarlo en el texto sin crear el buzón nuevo dejaría el aviso falso |
| App de Meta | `Nodevabot`, App ID `1148282351009693` | El nombre vive en un panel externo; cambiarlo ahí no aporta al usuario final y conviene hacerlo, si acaso, **antes** del App Review, no durante |
| Repo, base y URL | `barberia-bot`, `barberia.db`, `navajazo-bot.onrender.com` | Identidad técnica: renombrar el repo rompe enlaces y CI, y cambiar el servicio en Render cambia la URL del webhook ya verificada en Meta |
| Prefijo de folio | `AMB-` | Ya coincide con la marca; cambiarlo invalidaría folios ya entregados |

La regla que queda: **la marca visible es Ambar; la identidad técnica y los buzones existentes no
se tocan para no romper el canal ni los documentos legales.**

## Alternativas evaluadas

| Opción | Veredicto |
|---|---|
| **A.** Renombrar solo lo visible (JSON, HTML, README, título, tests) | **Elegido**: cubre todo lo que lee el cliente, cero riesgo operativo |
| **B.** Renombrar todo, incluido repo, servicio de Render y app de Meta | Descartado: cambia la URL del webhook ya verificada, obliga a re-verificar en Meta y reescribir enlaces, sin beneficio para el cliente |
| **C.** Dejar «Nodeva» y corregir después del App Review | Descartado: el nombre equivocado quedaría en documentos legales ya enviados a revisión |
| **D.** Cambiar también el correo a `ambar...@gmail.com` | Descartado: exige crear el buzón y actualizar el aviso; hoy nadie leería ese correo |

## Consecuencias

**A favor**

- Marca consistente entre chat, JSON, README y ambos documentos legales.
- Los tests de privacidad ahora **exigen** «Ambar», así que una regresión de nombre rompe CI.
- Sin cambios de arquitectura: 18 sustituciones de texto, 0 líneas de lógica.

**En contra / riesgos**

- El correo de los legales sigue diciendo "nodeva": es deliberado, pero se ve raro; se resuelve
  creando el buzón nuevo y actualizando el aviso.
- La app de Meta sigue llamándose `Nodevabot`; si el dueño la renombra, nada en el repo se rompe.
- El repo no tiene tilde en la marca; si el dueño pide «Ámbar» hay que tocar los mismos 7 archivos
  (el test de privacidad es el que detecta lo que falte).

## Decisiones que no se tomaron aquí (dueño)

1. **«Ambar» o «Ámbar»** con acento, en el sitio y en los legales. Hoy: sin acento.
2. **Renombrar la app de Meta** de `Nodevabot` a Ambar (idealmente antes del App Review).
3. **`"instagram": "@ambar.unas.ags"`** en `data/barberia.json:6`: no se sabe si es una cuenta real;
   si no lo es, debería ser un usuario ficticio o desaparecer de la demo.
4. **Buzón nuevo** para el aviso de privacidad, si se quiere retirar "nodeva" del correo.

## Verificaciones pendientes (VERIFICAR)

1. Que la marca con acento sea la preferida (afecta títulos, legales y capturas).
2. Que `@ambar.unas.ags` no exista como cuenta de otra persona antes de publicarla.
3. Nombre definitivo de la app en Meta al momento del App Review.
