# Barbería El Navajazo — bot de WhatsApp (demo)

Asistente conversacional para una barbería: responde preguntas frecuentes, agenda citas
y pasa la conversación a una persona cuando hace falta. Es un **proyecto muestra**: no está
conectado a un número real de WhatsApp, se usa desde un chat web con la estética de WhatsApp.

> Barbería ficticia. Todos los datos viven en `data/barberia.json` y se editan sin tocar código.

## Qué hace (v1)

- **FAQ**: precios, servicios, horario, ubicación, formas de pago, estacionamiento.
- **Agendar**: servicio → día/hora → nombre → confirmación. Slots de 30 min, hasta 7 días
  adelante, mínimo 1 hora de anticipación, sin dobles reservas.
- **Cancelar / reagendar** con el folio de la cita, libre hasta 2 h antes.
- **Hablar con el barbero**: handoff y se registra en la conversación.

Fuera de v1 a propósito: pagos, promociones, lealtad, multi-sucursal, multi-barbero,
recordatorios salientes 24 h antes y penalizaciones por cancelar.

## Cómo funciona el cerebro

Híbrido, con el LLM en un carril estrecho:

| Camino | Quién decide |
|---|---|
| Elegir servicio, día y hora | Botones y reglas deterministas (el LLM nunca inventa un horario) |
| FAQ y texto libre | DeepSeek redacta, **anclado** al JSON de la barbería |
| Sin `DEEPSEEK_API_KEY` o si la API falla | Matching local por tags; el bot sigue respondiendo |

## Arrancar en local

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Linux/macOS: .venv/bin/pip
cp .env.example .env                               # opcional: clave DeepSeek
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8077
```

Abre <http://127.0.0.1:8077> y usa el código de acceso `navajazo` (configurable en `DEMO_CODE`).

### Prueba automática del flujo completo

```bash
.venv/Scripts/python tests/smoke_test.py
```

Recorre código inválido → sesión → agendar → doble reserva → FAQ → handoff y falla si algo se rompe.

## Estructura

```
app/
  main.py      FastAPI: /, /api/sesion, /api/mensaje, /api/negocio, /health
  engine.py    motor conversacional determinista (máquina de estados + intenciones)
  llm.py       matching de FAQ local + capa DeepSeek opcional con fallback
  store.py     citas (en memoria en F1, SQLite en F2)
  config.py    configuración por variables de entorno
data/
  barberia.json   negocio, horario, reglas de agenda, servicios, FAQ
static/
  index.html      chat web estilo WhatsApp (acceso por código)
tests/
  smoke_test.py   prueba end-to-end contra el servidor
```

## Controles de acceso (es una demo pública)

1. `DEMO_CODE` como primera pantalla.
2. Presupuesto duro de mensajes: 40 por sesión, 300 por día.
3. Key de DeepSeek dedicada con saldo bajo, para que el techo de gasto sea real.

## Roadmap

- **F1 (hecho)**: datos + FAQ + chat local funcionando.
- **F2**: agenda con SQLite, cancelar/reagendar, sync opcional a Google Calendar.
- **F3**: deploy en Render, Docker, README con capturas, repo público.
