"""Configuración por entorno. Sin secretos en el repo."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "data" / "barberia.json"
STATIC_DIR = BASE_DIR / "static"

# Base de datos (fuente de verdad de las citas)
DB_FILE = Path(os.getenv("DB_FILE", str(BASE_DIR / "barberia.db")))

# Citas de ejemplo al arrancar con la agenda vacía.
# En el plan free de Render el disco se reinicia junto con el servicio.
SEED_DEMO = os.getenv("SEED_DEMO", "1").strip().lower() not in {"0", "false", "no", ""}

# Acceso a la demo
DEMO_CODE = os.getenv("DEMO_CODE", "navajazo")

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
