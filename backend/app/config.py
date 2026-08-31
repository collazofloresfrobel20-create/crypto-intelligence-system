import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _float(name: str, default: float) -> float:
    v = os.getenv(name)
    return float(v) if v else default


def _int(name: str, default: int) -> int:
    v = os.getenv(name)
    return int(v) if v else default


class Settings:
    # --- Gemini ---
    # Una sola API key para todo el proyecto: el key solo autentica contra la API,
    # no hay beneficio en usar una key distinta por agente. Lo que sí importa es
    # el rate limit del tier gratuito (compartido por key, no por "agente"), por
    # eso el throttling en gemini_client.py serializa las llamadas.
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    # OJO: NO usar los alias "-latest" (gemini-flash-latest / gemini-flash-lite-latest).
    # Comprobado en producción: ese alias apuntaba a gemini-3.7-flash (recién lanzado), cuyo
    # tier gratuito tenía una cuota de LANZAMIENTO de solo 20 solicitudes/día — no las ~1500/día
    # que uno esperaría de un modelo Flash establecido. Un "-latest" puede cambiar de modelo real
    # de un día a otro sin aviso y romper la cuota. Por eso se fijan versiones concretas, un
    # escalón detrás de la más nueva (más estables, con cuota gratuita ya normalizada).
    GEMINI_MODEL_FAST = os.getenv("GEMINI_MODEL_FAST", "gemini-3.5-flash-lite")
    # Modelo con más capacidad de razonamiento para Bull/Bear/Mediador/Juez.
    GEMINI_MODEL_SMART = os.getenv("GEMINI_MODEL_SMART", "gemini-3.5-flash")
    GEMINI_MAX_RPM = _int("GEMINI_MAX_RPM", 12)  # margen bajo el límite free-tier (~15 RPM)
    GEMINI_MAX_RPD = _int("GEMINI_MAX_RPD", 1400)  # margen bajo el límite free-tier (~1500 RPD)
    # El grounding con Google Search dejó de ser gratis en ene-2026: requiere facturación
    # habilitada en el proyecto de Google Cloud, incluso para el primer uso. Con una key
    # puramente gratuita (sin billing) falla con 429 RESOURCE_EXHAUSTED de inmediato
    # (confirmado en pruebas). Por eso queda apagado por defecto; si más adelante activas
    # facturación en aistudio.google.com, pon esto en "true" para research con evidencia
    # verificada en vivo.
    GEMINI_ENABLE_SEARCH_GROUNDING = os.getenv("GEMINI_ENABLE_SEARCH_GROUNDING", "false").lower() == "true"

    # --- Base de datos ---
    DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "cis.db"))

    # --- Hard filters (Discovery -> candidatos tempranos) ---
    MIN_LIQUIDITY_USD = _float("MIN_LIQUIDITY_USD", 50_000)
    MIN_VOLUME_24H_USD = _float("MIN_VOLUME_24H_USD", 100_000)
    MAX_MARKET_CAP_USD = _float("MAX_MARKET_CAP_USD", 50_000_000)
    MIN_HOLDERS = _int("MIN_HOLDERS", 200)
    MAX_LISTING_AGE_DAYS = _int("MAX_LISTING_AGE_DAYS", 120)

    # --- Pipeline ---
    MAX_CANDIDATES_PER_RUN = _int("MAX_CANDIDATES_PER_RUN", 15)
    PREDICTION_HORIZON_DAYS = _int("PREDICTION_HORIZON_DAYS", 7)

    # --- GoPlus (sin key requerida para uso básico) ---
    GOPLUS_APP_KEY = os.getenv("GOPLUS_APP_KEY", "")
    GOPLUS_APP_SECRET = os.getenv("GOPLUS_APP_SECRET", "")

    # --- Acceso privado (usuario/contraseña únicos, sin cuentas múltiples) ---
    AUTH_USERNAME = os.getenv("AUTH_USERNAME", "PAI")
    AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "TUPU777")
    LOGIN_MAX_ATTEMPTS = _int("LOGIN_MAX_ATTEMPTS", 5)
    LOGIN_LOCKOUT_MINUTES = _int("LOGIN_LOCKOUT_MINUTES", 15)

    # --- Actualización automática (sin depender de acordarse de darle clic) ---
    AUTO_UPDATE_ENABLED = os.getenv("AUTO_UPDATE_ENABLED", "true").lower() == "true"
    AUTO_UPDATE_INTERVAL_HOURS = _float("AUTO_UPDATE_INTERVAL_HOURS", 12)

    # --- Alertas por Telegram (opcional: si no se configura, simplemente no se envían) ---
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    TELEGRAM_NOTIFY_VERDICTS = [
        v.strip() for v in os.getenv("TELEGRAM_NOTIFY_VERDICTS", "Strong Opportunity").split(",") if v.strip()
    ]


settings = Settings()
