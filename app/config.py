"""Configuración por entorno. Sin secretos en el repo."""
import os
import secrets
from pathlib import Path

from .telefono import t10

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "data" / "barberia.json"
STATIC_DIR = BASE_DIR / "static"

# Base de datos (fuente de verdad de las citas)
DB_FILE = Path(os.getenv("DB_FILE", str(BASE_DIR / "barberia.db")))

# Citas de ejemplo al arrancar con la agenda vacía.
# En el plan free de Render el disco se reinicia junto con el servicio.
SEED_DEMO = os.getenv("SEED_DEMO", "1").strip().lower() not in {"0", "false", "no", ""}

# Acceso a la demo. SIN valor por defecto: uno público (estaba en el repo) dejaba la demo
# abierta si la variable faltaba en Render. app/main.py no arranca sin él.
DEMO_CODE = os.getenv("DEMO_CODE", "").strip()
# Solo desarrollo local: permite arrancar sin DEMO_CODE (chat web abierto a cualquiera).
ALLOW_OPEN_DEMO = os.getenv("ALLOW_OPEN_DEMO", "").strip().lower() in {"1", "true", "yes", "si", "sí"}

# Chat web: sid firmados, tope de sesiones en memoria y rate limit por IP.
# SESSION_SECRET firma los sid que entrega /api/sesion. En Render va con
# `generateValue: true` (se genera una vez y se conserva); si falta, se genera una al
# arrancar y los sid vigentes dejan de servir en cada reinicio.
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
SESSION_SECRET_EFIMERA = not SESSION_SECRET
if SESSION_SECRET_EFIMERA:
    SESSION_SECRET = secrets.token_hex(32)
MAX_SESIONES_WEB = int(os.getenv("MAX_SESIONES_WEB", "2000"))
RATE_SESION_POR_MIN = int(os.getenv("RATE_SESION_POR_MIN", "10"))      # por IP
RATE_MENSAJE_POR_MIN = int(os.getenv("RATE_MENSAJE_POR_MIN", "60"))    # por IP
# Cómo leer la IP del cliente tras el proxy de Render. 0 = primera IP de X-Forwarded-For;
# N > 0 = la N-ésima desde la derecha (la que agrega un proxy de confianza).
IP_DESDE_DERECHA = int(os.getenv("IP_DESDE_DERECHA", "0"))

# Anticipo al agendar (ver app/anticipo.py). El monto y las políticas viven en el bloque
# "anticipo" del JSON del negocio; aquí solo van el interruptor y los datos de pago, que son
# del cliente y NO se commitean (variables secretas en Render).
#   ANTICIPO_ACTIVO: vacío = lo que diga el JSON; "1"/"0" = fuerza encendido/apagado.
_AA = os.getenv("ANTICIPO_ACTIVO", "").strip().lower()
ANTICIPO_ACTIVO: bool | None = None if not _AA else _AA in {"1", "true", "yes", "si", "sí"}
# VALIDAR_COMPROBANTE: igual que el anterior, para la lectura del comprobante (ADR 0011). Sirve para
# apagarla o encenderla desde Render sin tocar el JSON ni redesplegar código.
_VC = os.getenv("VALIDAR_COMPROBANTE", "").strip().lower()
VALIDAR_COMPROBANTE: bool | None = None if not _VC else _VC in {"1", "true", "yes", "si", "sí"}
ANTICIPO_BANCO = os.getenv("ANTICIPO_BANCO", "").strip()
ANTICIPO_CLABE = os.getenv("ANTICIPO_CLABE", "").replace(" ", "").strip()
ANTICIPO_TITULAR = os.getenv("ANTICIPO_TITULAR", "").strip()

# Presupuesto duro (capa 2 del esquema de acceso)
MAX_MENSAJES_POR_SESION = int(os.getenv("MAX_MENSAJES_POR_SESION", "40"))
MAX_MENSAJES_POR_DIA = int(os.getenv("MAX_MENSAJES_POR_DIA", "300"))

# LLM (DeepSeek). Sin key, el bot funciona con reglas locales.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_URL = os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "12"))
# Lectura del comprobante de anticipo (app/llm.leer_comprobante, ADR 0011): misma key y misma URL
# de DeepSeek, pero con el modelo que acepta imágenes. `deepseek-flash` acepta JPEG, PNG, GIF y WebP
# en bloques `image_url` y reescala cada imagen a ~1300×1300 px (tope de 1024 tokens) antes de
# inferir: el reescalado puede estropear dígitos chicos del comprobante (clave de rastreo, cuenta).
DEEPSEEK_VISION_MODEL = os.getenv("DEEPSEEK_VISION_MODEL", "deepseek-flash").strip()
# Tope del archivo que se baja de WhatsApp para leerlo (un comprobante pesa menos de 1 MB).
COMPROBANTE_MAX_BYTES = int(os.getenv("COMPROBANTE_MAX_BYTES", str(5 * 1024 * 1024)))

# Google Calendar: OPCIONAL. Sin calendar id o sin token, el bot agenda igual.
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "").strip()
CALENDAR_TOKEN_FILE = os.getenv(
    "GOOGLE_TOKEN_JSON",
    str(Path.home() / "AppData" / "Local" / "hermes" / "google_token.json"),
)
CALENDAR_TZ = os.getenv("CALENDAR_TZ", "America/Mexico_City")
CALENDAR_TIMEOUT_S = float(os.getenv("CALENDAR_TIMEOUT_S", "8"))
# Lectura de Calendar para que un evento puesto a mano bloquee ese horario en el bot.
# Solo actúa si el espejo está habilitado; CALENDAR_BLOQUEO=0 lo apaga sin tocar el espejo.
CALENDAR_BLOQUEO = os.getenv("CALENDAR_BLOQUEO", "1").strip() != "0"
CALENDAR_BLOQUEO_TTL_S = float(os.getenv("CALENDAR_BLOQUEO_TTL_S", "60"))
CALENDAR_BLOQUEO_REINTENTO_S = float(os.getenv("CALENDAR_BLOQUEO_REINTENTO_S", "120"))
# Varias agendas (una persona por calendario). Se descubren por el prefijo del nombre:
# el calendario "Manicura - Ana" se le presenta a la clienta como "Ana". Con el prefijo
# vacío el bot se queda con el calendario único de siempre (GOOGLE_CALENDAR_ID).
CALENDAR_AGENDAS_PREFIJO = os.getenv("CALENDAR_AGENDAS_PREFIJO", "").strip()
CALENDAR_AGENDAS_TTL_S = float(os.getenv("CALENDAR_AGENDAS_TTL_S", "300"))
# Lecturas seguidas sin ver una agenda antes de darla de baja. Con el TTL de 300 s, 6 ≈ 30 min: un
# permiso quitado por error o un calendario renombrado de paso no saca a nadie del equipo al instante.
CALENDAR_AGENDAS_AUSENCIAS = int(os.getenv("CALENDAR_AGENDAS_AUSENCIAS", "6") or "6")
# Cada cuánto late el bucle que mantiene fresco el caché de Calendar (sin bloquear a la
# clienta). Debe ser menor que CALENDAR_BLOQUEO_TTL_S para que el bloqueo casi nunca esté viejo.
CALENDAR_CICLO_S = float(os.getenv("CALENDAR_CICLO_S", "30"))

# Token de administración para GET /api/citas. Vacío = ruta deshabilitada (503).
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

# ---------- Adaptador de WhatsApp Cloud API ----------
# WA_MODO="consola" imprime el payload en el log en vez de llamar a Meta.
# Sirve para desarrollar y probar el adaptador sin credenciales ni número.
WA_MODO = os.getenv("WA_MODO", "graph").strip().lower()
WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "").strip()
WA_APP_SECRET = os.getenv("WA_APP_SECRET", "").strip()
WA_TOKEN = os.getenv("WA_TOKEN", "").strip()
WA_PHONE_ID = os.getenv("WA_PHONE_ID", "").strip()
# VERIFICAR en el changelog de Meta; configurable por entorno.
WA_API_VERSION = os.getenv("WA_API_VERSION", "v26.0").strip()
# Un cliente real puede escribir 60 mensajes; el tope de 40 es de la demo web.
MAX_MENSAJES_POR_SESION_WA = int(os.getenv("MAX_MENSAJES_POR_SESION_WA", "500"))

# Topes propios del canal WhatsApp.
WA_MAX_POR_HORA = int(os.getenv("WA_MAX_POR_HORA", "12"))              # por teléfono
WA_MAX_POR_DIA = int(os.getenv("WA_MAX_POR_DIA", "150"))               # por teléfono
WA_MAX_GLOBAL_POR_DIA = int(os.getenv("WA_MAX_GLOBAL_POR_DIA", "750"))  # todos

# Expiración de sesiones (minutos).
SESION_TTL_MIN = int(os.getenv("SESION_TTL_MIN", "30"))
SESION_HUMANO_TTL_MIN = int(os.getenv("SESION_HUMANO_TTL_MIN", "720"))

# Aviso de handoff al dueño: sin estas dos, solo se escribe en el log.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Concentrado de depósitos (app/concentrado.py). La hora sale del JSON del negocio
# (`anticipo.resumen_hora`); aquí se decide si el proceso lo dispara por su cuenta.
# RESUMEN_AUTO=0 lo apaga (el disparo manual por /api/depositos/enviar sigue funcionando).
RESUMEN_AUTO = os.getenv("RESUMEN_AUTO", "1").strip().lower() not in {"0", "false", "no"}
# Cada cuánto se pregunta si ya es la hora. 60 s basta: el reporte es de un día.
RESUMEN_CICLO_S = float(os.getenv("RESUMEN_CICLO_S", "60"))


def _lista_telefonos(valor: str) -> set[str]:
    """'521 55 0000 0011, 5500000022' -> {'5500000011', '5500000022'}"""
    return {t10(p) for p in valor.split(",") if t10(p)}


# Allowlist por últimos 10 dígitos. Vacía = canal abierto.
WA_ALLOWLIST = _lista_telefonos(os.getenv("WA_ALLOWLIST", ""))


def wa_habilitado() -> bool:
    """El canal se enciende solo si están las credenciales que se usan de verdad."""
    if WA_MODO == "consola":
        return bool(WA_VERIFY_TOKEN and WA_APP_SECRET)
    return all((WA_VERIFY_TOKEN, WA_APP_SECRET, WA_TOKEN, WA_PHONE_ID))
