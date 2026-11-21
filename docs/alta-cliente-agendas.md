# Alta de un cliente con varias agendas

Guía para dejar a un negocio nuevo con **una persona por calendario**: qué se crea en la cuenta de
Google del negocio, cómo se comparte, cómo se autoriza el token y qué pasa cuando alguien renombra,
pierde el acceso o se va. El diseño está en el [ADR 0010](adr/0010-varias-agendas.md).

Tiempo: 15 minutos, más lo que tarde cada manicurista en aceptar el calendario compartido.

## 0. Antes de empezar

- Cuenta de Google **del negocio** (no la personal de una empleada): si alguien se va, sale del
  calendario, no del sistema.
- `CALENDAR_AGENDAS_PREFIJO` decidido de antemano. Es el texto que separa un calendario de agenda de
  cualquier otro calendario de la cuenta. Recomendado: `Agenda · ` (con espacio detrás).
- Token con alcance suficiente (paso 3). Sin él, el bot sigue agendando en SQLite, pero no ve
  calendarios y el modo multiagenda queda apagado.

## 1. Crear los calendarios

En Google Calendar del negocio, uno por persona, con el prefijo exacto:

```
Agenda · Ana
Agenda · Laura
```

- **El prefijo es obligatorio.** Un calendario sin él (`Cumpleaños`, `Personal`) no se muestra:
  sirve para que la cuenta del negocio no se convierta en el equipo por accidente.
- El nombre que ve la clienta es lo que sigue al prefijo (`Ana`, `Laura`). Nada más.
- Los horarios de atención siguen saliendo de `data/barberia.json`; **no** se configuran por
  persona en v1. Un día libre se marca con un evento de todo el día en su calendario (ADR 0007).
- Más de 9 agendas activas: el menú de WhatsApp muestra las 9 primeras por orden y lo anota en el
  log. La paginación está fuera de alcance.

## 2. Compartir con las chicas (permiso revocable)

Desde cada calendario → *Configuración y uso compartido* → *Compartir con determinadas personas*,
con permiso **«Hacer cambios en el evento»** (o «Ver todos los detalles del evento» si solo debe
consultarlo). El bot no necesita que ellas tengan acceso; el acceso es para que vean su agenda y
aparten cosas a mano.

Revocar es quitar la persona de esa misma lista: no toca las citas ya agendadas. El bot las sigue
viendo porque lee con el token del negocio.

## 3. Autorizar el token

El bot usa un JSON de *usuario autorizado* (el mismo formato del token de Google Workspace que ya
usa Hermes en `~/AppData/Local/hermes/google_token.json`):

```json
{"token": "...", "refresh_token": "...", "client_id": "...", "client_secret": "...",
 "token_uri": "https://oauth2.googleapis.com/token", "scopes": ["..."], "expiry": "..."}
```

Para multiagenda el token necesita **poder listar calendarios**, no solo eventos. El alcance que
cubre todo el flujo es:

```
https://www.googleapis.com/auth/calendar
```

(`calendar.readonly` + escritura de eventos también sirve; `calendar.events` **no**: con ese alcance
`calendarList.list` responde 403 y el bot se queda sin equipo.)

Verificar el alcance **sin imprimir el token** (solo lee el campo `scopes`):

```bash
python -c "import json,os;print(json.load(open(os.path.expanduser('~/AppData/Local/hermes/google_token.json')))['scopes'])"
```

Si no incluye `auth/calendar` (ni `calendar.readonly`), hay que re-autorizar: se genera un token
nuevo con el alcance completo, se reemplaza el archivo y se reinicia el servicio. El JSON **no se
sube al repo** ni se pega en tickets: se entrega como secreto (`GOOGLE_TOKEN_JSON` en Render, o
archivo en `/etc/secrets/google_token.json`).

Ejemplo de sesión de re-autorización con `google-auth-oauthlib` en la máquina de quien administra
(no en el servidor, no en el repo, no en el chat):

```python
from google_auth_oauthlib.flow import InstalledAppFlow
SCOPES = ["https://www.googleapis.com/auth/calendar"]
flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
flow.run_local_server(port=0).to_json("google_token.json")   # abre el navegador
```

## 4. Configurar el bot

```
CALENDAR_AGENDAS_PREFIJO=Agenda · 
CALENDAR_AGENDAS_TTL_S=300
```

Y el token como siempre (`GOOGLE_TOKEN_JSON`). `GOOGLE_CALENDAR_ID` deja de importar cuando hay
agendas: las citas van al calendario de la agenda elegida.

Comprobación rápida: `GET /health` debe reportar las agendas activas y ninguna huérfana; si el
listado falla, se conserva la última lista buena y se avisa una vez por Telegram.

## 5. Qué ve la clienta

- Con **dos o más** agendas activas: tras elegir servicio, «¿Con quién prefieres?» (Ana, Laura,
  Cualquiera). Con **una sola** no pregunta. Con **cero** vuelve al modo de siempre.
- «Cualquiera» reserva con quien tenga menos citas ese día (y, en empate, con quien la atendió la
  última vez) y se lo dice en la confirmación: «Quedas vie 21 nov 10:00 con Ana…».
- Mover y cancelar usan **solo** los horarios de la agenda de esa cita.

## 6. Renombrar, dar de baja, quitar el prefijo

| Cambio en Google | Qué pasa |
|---|---|
| Renombrar `Agenda · Laura` → `Agenda · Sofía` | La siguiente cita sale como «Sofía». Las citas viejas conservan «Laura» (el nombre se guarda con la cita). |
| Quitar el prefijo de un calendario | Tras **dos** lecturas (≈10 min con el TTL por defecto) deja de ofrecerse. Sus citas futuras siguen válidas y aparecen en «Citas por reasignar» del resumen diario. |
| Quitar el acceso del token a ese calendario | Igual que el anterior: ese calendario se degrada y los demás siguen agendando. |
| Borrar el calendario | Las citas quedan sin agenda; entran en «Citas por reasignar» y el dueño las reubica a mano. |
| Se va una manicurista | Se quita del calendario compartido. Sus citas futuras siguen válidas hasta que el dueño las mueva. |

La baja es suave a propósito (`AUSENCIAS_PARA_BAJA = 2`): un fallo de red o un 500 de Google no
vacía el equipo.

## 7. Reversa

Vaciar `CALENDAR_AGENDAS_PREFIJO` en el panel y reiniciar: el bot vuelve al modo de un solo
calendario sin redesplegar. La migración de SQLite es aditiva; volver al código anterior solo es
seguro mientras no haya citas con `agenda_id` distinto de `''` — si las hay, resuélvelas antes.

## 8. Pruebas de aceptación (antes de entregarlo)

Con una cuenta de Google de pruebas y el número de pruebas de Meta:

1. Tres calendarios: `Agenda · Ana`, `Agenda · Laura` y uno sin prefijo → el bot ve solo dos.
2. Agendar «cualquiera» varias veces → reparte según la regla y cada evento cae en el calendario
   correcto.
3. Elegir a Laura → solo se muestran horarios de Laura.
4. Evento de todo el día en el calendario de Ana → Ana desaparece ese día (para «cualquiera» y para
   quien la elija).
5. Renombrar, quitar el prefijo, quitar el acceso y desconectar la red → ver la tabla del punto 6.
6. Con anticipo activo: apartar con «cualquiera», mandar el comprobante → la cita queda en la
   agenda asignada y el aviso dice con quién.
