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
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    # Cuota diaria gratuita confirmada en producción: 20 llamadas/día POR PROYECTO de Google
    # Cloud (no por key — varias keys del mismo proyecto comparten la misma cuota). El cuello
    # de botella real es el modelo "smart" (4 llamadas/token: bull/bear/mediador/juez) — con
    # una sola key, ~5 tokens/día. Cada key adicional AQUÍ debe venir de un proyecto de Google
    # Cloud DISTINTO (créalas en aistudio.google.com/apikey -> "Create API key in new project")
    # para que cada una traiga su propia cuota independiente. gemini_client.py rota
    # automáticamente a la siguiente key de esta lista cuando la actual se queda sin cuota del
    # día, así que basta con llenarla y no tocar nada más. El modelo "fast" (7 analistas) no
    # necesita esto: su cuota gratuita es mucho más alta y no se agotó ni una vez en pruebas.
    GEMINI_SMART_API_KEYS = [
        k.strip() for k in os.getenv("GEMINI_SMART_API_KEYS", "").split(",") if k.strip()
    ]
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

    # --- Higiene de datos (Plan v2, B0, 2026-09-28) ---
    # Medido: ~350 filas/día de control eran de ~360 tokens MUERTOS (mediana de volumen 24h de
    # $596, 76% ya offline/delisted): nunca se pueden evaluar, inflan Turso y alargan el ciclo.
    # Los tokens con volumen por debajo de este piso (o marcados offline) se re-registran como
    # máximo una vez cada DEAD_TOKEN_RETRACK_HOURS en vez de a diario.
    DEAD_VOLUME_FLOOR_USD = _float("DEAD_VOLUME_FLOOR_USD", 1_000)
    DEAD_TOKEN_RETRACK_HOURS = _int("DEAD_TOKEN_RETRACK_HOURS", 168)

    # --- Pipeline ---
    MAX_CANDIDATES_PER_RUN = _int("MAX_CANDIDATES_PER_RUN", 15)
    PREDICTION_HORIZON_DAYS = _int("PREDICTION_HORIZON_DAYS", 7)
    # Regla dura del Juez: por debajo de este confidence_score, el veredicto no puede ser
    # "Strong Opportunity" (sí puede ser "Watchlist"/"High Risk"/etc). Antes vivía como texto
    # fijo dentro del prompt -- la auto-corrección podía "proponer" cambiarlo pero no existía
    # ningún parámetro real que aplicar. Ahora es ajustable como los demás hard filters.
    MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY = _int("MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY", 40)

    # --- Fase 1 (2026-09-24): clasificador ML como pre-filtro de candidatos ---
    # Fallback obligatorio: con menos casos evaluados que esto no se entrena nada, el sistema
    # sigue usando filters.rank_candidates() sin cambios -- no se lanza un modelo mal entrenado
    # a producción solo porque "ya se puede".
    MIN_SAMPLES_FOR_ML = _int("MIN_SAMPLES_FOR_ML", 30)
    # "shadow" (por defecto): el modelo se entrena y se loguea qué habría elegido, pero la
    # selección real de candidatos sigue siendo la heurística de siempre. "active": el modelo
    # reemplaza a la heurística (con el mismo fallback si no hay modelo/muestra todavía). El
    # cambio a "active" es una decisión humana desde el dashboard, informada por el benchmark
    # de la Fase 1.5 -- nunca automática.
    ML_SCORING_MODE = os.getenv("ML_SCORING_MODE", "shadow")

    # --- Plan v2, B2 (2026-09-28): modelos de precio (features gratuitas de klines) ---
    # "shadow" (por defecto): se calculan y se guardan pm_* en cada fila, pero los candidatos se
    # siguen eligiendo como antes. "active": los candidatos se eligen con price_models
    # (excluye el cuartil de mayor riesgo de caida y ordena por exito sostenido). Decision humana
    # desde el dashboard, informada por el criterio prerregistrado en PREREGISTRO.md.
    PRICE_MODEL_MODE = os.getenv("PRICE_MODEL_MODE", "shadow")

    # --- Plan v2, C1 (2026-09-28): techo de confidence si falta una fuente critica ---
    # Si klines o GoPlus no respondieron, el confidence no puede llegar al corte de
    # "Strong Opportunity" (MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY - 1 es el techo: se reutiliza el
    # corte que ya existe en vez de inventar un numero). "shadow" (por defecto): solo se guarda
    # confidence_score_capped y se muestra el aviso; "active": ademas, un "Strong Opportunity" con
    # una fuente critica faltante baja a "Watchlist". Decision humana desde el dashboard.
    CONFIDENCE_CAP_MODE = os.getenv("CONFIDENCE_CAP_MODE", "shadow")

    # --- Fase 2 (2026-09-24): segunda opinión del Juez vía Groq (free tier permanente, sin
    # facturación requerida -- a diferencia del grounding de Gemini que dejó de ser gratis) ---
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    # Confirmar el nombre exacto contra https://console.groq.com/docs/models al desplegar --
    # los modelos gratuitos de Groq rotan con cierta frecuencia, más que los de Gemini.
    GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    # "shadow" (por defecto): se llama a Groq y se guarda su veredicto + si coincide con
    # Gemini, pero el veredicto final que se usa (dashboard, Telegram) sigue siendo el de
    # Gemini sin tocar -- primero se mide si el desacuerdo predice peor resultado real, no se
    # asume. "active": si Gemini dice "Strong Opportunity" y Groq no coincide, el veredicto
    # final baja a "Insufficient Evidence". Decisión humana desde el dashboard, nunca
    # automática ni tocada por el auto-corrector.
    ENSEMBLE_JUDGE_MODE = os.getenv("ENSEMBLE_JUDGE_MODE", "shadow")

    # --- Jev / TypeSafe (PROPUESTA_JEV_CIS.md): sensor semántico en sombra, mejor esfuerzo.
    # La key la pone el usuario en backend/.env (o como secret de GitHub); nunca en el repo.
    TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")
    # Versión fijada a propósito, NO el alias "jev-latest": un alias que se mueve cambiaría las
    # respuestas sin que cambie nuestro código.
    JEV_MODEL = os.getenv("JEV_MODEL", "jev-1.13.0")

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
