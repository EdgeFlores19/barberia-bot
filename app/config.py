"""Configuración por entorno. Sin secretos en el repo."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "data" / "barberia.json"
STATIC_DIR = BASE_DIR / "static"

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
