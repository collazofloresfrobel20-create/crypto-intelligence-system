from pathlib import Path

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .db import init_db, get_conn, row_to_dict
from . import pipeline, backtesting, diagnosis, auth, scheduler, dynamic_config
from .config import settings

app = FastAPI(title="Crypto Intelligence System (CIS)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()
dynamic_config.load_dynamic_config()


@app.on_event("startup")
def _start_scheduler():
    scheduler.start()

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path
    if auth.is_public_path(path) or path.startswith("/static/login"):
        return await call_next(request)

    token = request.cookies.get(auth.SESSION_COOKIE)
    if not auth.is_valid_session(token):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "No autenticado"}, status_code=401)
        return RedirectResponse(url="/login")

    return await call_next(request)


@app.get("/login")
def login_page():
    return FileResponse(FRONTEND_DIR / "login.html")


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def api_login(body: LoginBody, request: Request, response: Response):
    key = auth.client_key(request)
    auth.check_login_rate_limit(key)

    if not auth.verify_credentials(body.username, body.password):
        auth.register_login_failure(key)
        raise HTTPException(401, "Usuario o contraseña incorrectos")

    auth.register_login_success(key)
    token = auth.create_session()
    response.set_cookie(
        auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
        max_age=auth.SESSION_DAYS * 24 * 3600,
    )
    return {"ok": True}


@app.post("/api/auth/logout")
def api_logout(request: Request, response: Response):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        auth.destroy_session(token)
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ---------- Ciclo de actualización (discovery + research + reevaluación + diagnóstico) ----------

@app.post("/api/update")
def trigger_update(background_tasks: BackgroundTasks):
    with get_conn() as conn:
        running = conn.execute(
            "SELECT id FROM pipeline_runs WHERE status = 'running'"
        ).fetchone()
    if running:
        raise HTTPException(400, f"Ya hay una actualización en curso: {running['id']}")

    run_id = pipeline.create_run("update")
    background_tasks.add_task(pipeline.run_update_cycle, run_id)
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "run no encontrado")
    return row_to_dict(row)


@app.get("/api/runs")
def list_runs():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 20").fetchall()
    return [row_to_dict(r) for r in rows]


# ---------- Predicciones (analyzed) y descartados (discarded) ----------

@app.get("/api/predictions")
def list_predictions(category: str = "analyzed", run_id: str | None = None, limit: int = 200):
    query = "SELECT * FROM predictions WHERE category = ?"
    params: list = [category]
    if run_id:
        query += " AND run_id = ?"
        params.append(run_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [row_to_dict(r) for r in rows]


@app.get("/api/predictions/count")
def count_predictions(category: str = "analyzed"):
    """Conteo real (sin el LIMIT de list_predictions). Encontrado en producción: el dashboard
    mostraba "200 descartados" en todos lados -- ese era el límite por defecto de la lista, no
    el total real (que resultó ser 9,346)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM predictions WHERE category = ?", (category,)
        ).fetchone()
    return {"category": category, "count": row["n"]}


@app.get("/api/predictions/{prediction_id}")
def get_prediction(prediction_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM predictions WHERE id = ?", (prediction_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no encontrado")
    return row_to_dict(row)


# ---------- Backtesting / rendimiento histórico ----------

@app.post("/api/backtest/evaluate")
def trigger_evaluation():
    count = backtesting.evaluate_due_predictions()
    marked = backtesting.update_current_marks()
    return {"newly_evaluated": count, "marked_to_market": marked}


@app.get("/api/backtest/stats")
def backtest_stats():
    return backtesting.get_performance_stats()


@app.get("/api/backtest/filter-efficacy")
def filter_efficacy():
    return backtesting.get_filter_efficacy_stats()


@app.get("/api/history/overview")
def history_overview():
    return backtesting.get_history_overview()


# ---------- Auto-corrección ----------

@app.get("/api/diagnoses/latest")
def api_latest_diagnosis():
    diag = diagnosis.get_latest_diagnosis()
    if not diag:
        raise HTTPException(404, "aún no hay diagnósticos generados")
    return diag


@app.get("/api/diagnoses")
def api_list_diagnoses():
    return diagnosis.list_diagnoses()


class ApplyAdjustmentsBody(BaseModel):
    accepted_params: list[str] | None = None


@app.post("/api/diagnoses/{diagnosis_id}/apply")
def api_apply_diagnosis(diagnosis_id: int, body: ApplyAdjustmentsBody):
    return diagnosis.apply_adjustments(diagnosis_id, body.accepted_params)


# ---------- Config ----------

@app.get("/api/config")
def get_config():
    return {
        "MIN_LIQUIDITY_USD": settings.MIN_LIQUIDITY_USD,
        "MIN_VOLUME_24H_USD": settings.MIN_VOLUME_24H_USD,
        "MAX_MARKET_CAP_USD": settings.MAX_MARKET_CAP_USD,
        "MIN_HOLDERS": settings.MIN_HOLDERS,
        "MAX_LISTING_AGE_DAYS": settings.MAX_LISTING_AGE_DAYS,
        "MAX_CANDIDATES_PER_RUN": settings.MAX_CANDIDATES_PER_RUN,
        "PREDICTION_HORIZON_DAYS": settings.PREDICTION_HORIZON_DAYS,
        "MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY": settings.MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY,
        "GEMINI_MODEL_FAST": settings.GEMINI_MODEL_FAST,
        "GEMINI_MODEL_SMART": settings.GEMINI_MODEL_SMART,
        "GEMINI_ENABLE_SEARCH_GROUNDING": settings.GEMINI_ENABLE_SEARCH_GROUNDING,
        "gemini_api_key_configured": bool(settings.GEMINI_API_KEY),
        "AUTO_UPDATE_ENABLED": settings.AUTO_UPDATE_ENABLED,
        "AUTO_UPDATE_INTERVAL_HOURS": settings.AUTO_UPDATE_INTERVAL_HOURS,
        "telegram_configured": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
        "LOGIN_MAX_ATTEMPTS": settings.LOGIN_MAX_ATTEMPTS,
        "LOGIN_LOCKOUT_MINUTES": settings.LOGIN_LOCKOUT_MINUTES,
    }


class ConfigUpdateBody(BaseModel):
    MIN_LIQUIDITY_USD: float | None = None
    MIN_VOLUME_24H_USD: float | None = None
    MAX_MARKET_CAP_USD: float | None = None
    MIN_HOLDERS: int | None = None
    MAX_LISTING_AGE_DAYS: int | None = None
    MAX_CANDIDATES_PER_RUN: int | None = None
    MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY: int | None = None


@app.post("/api/config")
def update_config(body: ConfigUpdateBody):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = dynamic_config.save_dynamic_config(updates)
    return {"updated": updated}
