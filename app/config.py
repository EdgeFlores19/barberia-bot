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

# Presupuesto duro (capa 2 del esquema de acceso)
MAX_MENSAJES_POR_SESION = int(os.getenv("MAX_MENSAJES_POR_SESION", "40"))
MAX_MENSAJES_POR_DIA = int(os.getenv("MAX_MENSAJES_POR_DIA", "300"))

# LLM (DeepSeek). Sin key, el bot funciona con reglas locales.
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_URL = os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "12"))

# Google Calendar: OPCIONAL. Sin calendar id o sin token, el bot agenda igual.
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "").strip()
CALENDAR_TOKEN_FILE = os.getenv(
    "GOOGLE_TOKEN_JSON",
    str(Path.home() / "AppData" / "Local" / "hermes" / "google_token.json"),
)
CALENDAR_TZ = os.getenv("CALENDAR_TZ", "America/Mexico_City")
CALENDAR_TIMEOUT_S = float(os.getenv("CALENDAR_TIMEOUT_S", "8"))

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
