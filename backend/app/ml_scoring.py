"""
Clasificador estadístico de pre-filtro (Fase 1, 2026-09-24): en vez de truncar candidatos con
un ranking heurístico fijo (filters.rank_candidates), aprende del propio historial evaluado del
sistema qué combinación de features tiende a terminar en un retorno máximo >= 20% -- para no
gastar cupo de Gemini en candidatos con baja probabilidad real de convertirse en oportunidad.

Features usadas (las que ya existían en el sistema, tal como se pidió -- no se inventan
features nuevas en esta fase): edad del listing, variación de precio 24h, volumen 24h, liquidez
y market cap. Este último no se pidió explícitamente pero ya es una columna existente y hace
falta para poder comparar de forma justa contra el ranking heurístico actual (que usa
volumen/market cap), así que se incluye por la misma razón que los demás: ya existe, no es una
señal nueva.

Split temporal, no aleatorio: el historial se ordena por fecha y se parte cronológicamente en
train/validación/shadow-test. Mezclar aleatoriamente filtraría información del futuro al
entrenamiento y ocultaría cambios de régimen de mercado -- el tramo más reciente siempre queda
fuera del entrenamiento, como prueba honesta fuera de muestra.

Todo modelo entrenado se guarda en la tabla `ml_models` (Turso/SQLite), nunca en el repo: los
runners de GitHub Actions son máquinas desechables sin disco persistente ni permisos de
escritura al repo -- Turso ya es la única fuente de verdad persistente de todo el sistema.

Fase 3 (2026-09-24): este módulo también entrena la curva de calibración del confidence_score
(kind='calibration' en `ml_models`, misma tabla que el selector de la Fase 1) -- corrige, con
IsotonicRegression, qué tan honesto es el confidence_score que el Juez ya produce hoy, sin
cambiar selección ni ranking de candidatos.
"""
import base64
import io
import json
from datetime import datetime, timezone

from .db import get_conn

FEATURE_KEYS = [
    "listing_age_days_at_discovery",
    "pct_change_24h_at_discovery",
    "volume_24h",
    "liquidity",
    "market_cap",
]

# Fase 4 (2026-09-24): señal de GoPlus (goplus.extract_security_features), OPCIONAL a
# diferencia de FEATURE_KEYS -- GoPlus tiene cobertura imperfecta (chains no soportadas,
# llamadas que fallan pese a los reintentos) y exigirla como obligatoria haría que el
# clasificador cayera al fallback heurístico la mayoría de las veces. Si falta, se imputa a un
# valor neutral en vez de descartar la fila/candidato.
OPTIONAL_FEATURE_DEFAULTS = {
    "creator_percent": 0.0,
    "owner_percent": 0.0,
    "lp_holders_locked_pct": 0.0,
    "is_honeypot": 0,
    "is_mintable": 0,
}
ALL_FEATURE_KEYS = FEATURE_KEYS + list(OPTIONAL_FEATURE_DEFAULTS.keys())

SUCCESS_THRESHOLD = 20.0  # mismo umbral que backtesting.FULL_SUCCESS_THRESHOLD -- no se inventa uno nuevo


def _fetch_evaluated_rows() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT created_at, max_return_pct, return_pct, return_at_day3_pct,
                   listing_age_days_at_discovery, pct_change_24h_at_discovery,
                   volume_24h, liquidity, market_cap,
                   creator_percent, owner_percent, lp_holders_locked_pct, is_honeypot, is_mintable
            FROM predictions
            WHERE status = 'evaluated'
            ORDER BY created_at ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _row_features(row: dict) -> list[float] | None:
    """Las FEATURE_KEYS son obligatorias (si falta cualquiera, esta fila no sirve para
    entrenar/rankear). Las OPTIONAL_FEATURE_DEFAULTS se imputan si faltan -- así una fila sin
    datos de GoPlus (o de antes de la Fase 4) sigue siendo usable."""
    required = [row.get(k) for k in FEATURE_KEYS]
    if any(v is None for v in required):
        return None
    optional = [
        row.get(k) if row.get(k) is not None else OPTIONAL_FEATURE_DEFAULTS[k]
        for k in OPTIONAL_FEATURE_DEFAULTS
    ]
    try:
        return [float(v) for v in required + optional]
    except (TypeError, ValueError):
        return None


def _row_label(row: dict) -> int:
    return 1 if (row.get("max_return_pct") or 0) >= SUCCESS_THRESHOLD else 0


def _sustained_return_flag(row: dict) -> int | None:
    """Métrica secundaria pedida explícitamente por el usuario, solo con fines de observación
    futura -- NO se usa para entrenar todavía, no reemplaza la etiqueta actual. A diferencia de
    max_return_pct (que puede ser un pico de un instante seguido de un desplome), esto mide si
    el retorno se sostuvo hasta el día 3 o hasta el final del horizonte de 7 días."""
    day3 = row.get("return_at_day3_pct")
    final = row.get("return_pct")
    candidates = [v for v in (day3, final) if v is not None]
    if not candidates:
        return None
    return int(max(candidates) >= 10.0)


def _temporal_split(rows: list[dict], train_frac: float = 0.6, val_frac: float = 0.2):
    """Parte `rows` (ya ordenadas por created_at ASC) en tres bloques cronológicos, sin barajar."""
    n = len(rows)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    return rows[:train_end], rows[train_end:val_end], rows[val_end:]


def build_training_set():
    """Devuelve (train_rows, val_rows, shadow_rows) -- dicts crudos, ya filtrados a los que
    tienen todas las features, partidos cronológicamente. No featuriza todavía: cada consumidor
    (train_model, benchmark_against_heuristic) decide qué necesita de cada fila."""
    rows = _fetch_evaluated_rows()
    usable = [r for r in rows if _row_features(r) is not None]
    return _temporal_split(usable)


def _to_xy(rows: list[dict]):
    X = [_row_features(r) for r in rows]
    y = [_row_label(r) for r in rows]
    return X, y


def has_both_classes(rows: list[dict]) -> bool:
    """True si `rows` contiene al menos un caso de éxito y uno de fracaso -- entrenar con una
    sola clase no tiene sentido (el modelo no tendría nada que aprender a distinguir)."""
    labels = {_row_label(r) for r in rows}
    return len(labels) >= 2


def train_model(train_rows: list[dict], val_rows: list[dict]):
    """Entrena LogisticRegression (no LightGBM: cero dependencias de sistema nuevas en el
    runner de GitHub Actions, artefacto minúsculo, interpretable). class_weight='balanced'
    porque el problema está desbalanceado (pocos "éxito" reales sobre el total de candidatos) --
    sin esto un modelo que siempre prediga "no" tendría accuracy alta y sería inútil. Devuelve
    (modelo, métricas) -- las métricas son precision/recall/PR-AUC/falsos negativos sobre
    validación, no accuracy, por la misma razón de desbalance."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import precision_score, recall_score, average_precision_score

    X_train, y_train = _to_xy(train_rows)
    X_val, y_val = _to_xy(val_rows)

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
    ])
    model.fit(X_train, y_train)

    metrics = {
        "n_train": len(X_train), "n_val": len(X_val),
        "positives_in_train": int(sum(y_train)), "positives_in_val": int(sum(y_val)),
    }
    if X_val and len(set(y_val)) > 1:
        y_pred = model.predict(X_val)
        y_proba = model.predict_proba(X_val)[:, 1]
        metrics.update({
            "precision": round(precision_score(y_val, y_pred, zero_division=0), 4),
            "recall": round(recall_score(y_val, y_pred, zero_division=0), 4),
            "pr_auc": round(average_precision_score(y_val, y_proba), 4),
            "false_negatives": int(sum(1 for yt, yp in zip(y_val, y_pred) if yt == 1 and yp == 0)),
        })
    else:
        metrics["warning"] = "conjunto de validación insuficiente o de una sola clase -- precision/recall/PR-AUC no calculados"

    positives = [r for r in train_rows + val_rows if _row_label(r) == 1]
    sustained_flags = [_sustained_return_flag(r) for r in positives]
    sustained_flags = [f for f in sustained_flags if f is not None]
    if sustained_flags:
        metrics["sustained_rate_among_peaks"] = round(sum(sustained_flags) / len(sustained_flags), 4)

    return model, metrics


def benchmark_against_heuristic(eval_rows: list[dict], model) -> dict:
    """Fase 1.5: compara, sobre el mismo conjunto de evaluación, el clasificador ML contra el
    ranking heurístico actual (filters.heuristic_score) -- para que activar
    ML_SCORING_MODE=active sea una decisión informada por datos, no una corazonada. Simula,
    para cada método, qué top-N habría elegido (mismo N que MAX_CANDIDATES_PER_RUN) y cuántas
    de esas elecciones de verdad terminaron en éxito."""
    from . import filters
    from .config import settings
    from sklearn.metrics import average_precision_score

    y_true, ml_scores, heuristic_scores = [], [], []
    for r in eval_rows:
        feats = _row_features(r)
        if feats is None:
            continue
        y_true.append(_row_label(r))
        ml_scores.append(float(model.predict_proba([feats])[0][1]))
        token_like = {
            "marketCap": r.get("market_cap"), "volume24h": r.get("volume_24h"),
            "percentChange24h": r.get("pct_change_24h_at_discovery"),
        }
        heuristic_scores.append(filters.heuristic_score(token_like))

    if len(y_true) < 2 or len(set(y_true)) < 2:
        return {"warning": "conjunto de benchmark insuficiente o de una sola clase -- comparación no calculada"}

    def top_n_metrics(scores):
        n = min(settings.MAX_CANDIDATES_PER_RUN, len(scores))
        ranked_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
        selected = set(ranked_idx)
        tp = sum(1 for i in selected if y_true[i] == 1)
        total_positives = sum(y_true)
        fn = total_positives - tp
        return {
            "candidates_investigated": len(selected),
            "strong_opportunities_found": tp,
            "false_negatives": fn,
            "precision_top_n": round(tp / len(selected), 4) if selected else 0.0,
            "recall_top_n": round(tp / total_positives, 4) if total_positives else 0.0,
        }

    return {
        "n_eval": len(y_true),
        "positives_in_eval": sum(y_true),
        "ml": {"pr_auc": round(average_precision_score(y_true, ml_scores), 4), **top_n_metrics(ml_scores)},
        "heuristic": {"pr_auc": round(average_precision_score(y_true, heuristic_scores), 4), **top_n_metrics(heuristic_scores)},
    }


def score_candidate(features: dict) -> float | None:
    """features: dict con las claves de FEATURE_KEYS (obligatorias) y opcionalmente las de
    OPTIONAL_FEATURE_DEFAULTS (Fase 4, se imputan si faltan). Devuelve la probabilidad estimada
    [0,1] de que este candidato termine en éxito, o None si no hay modelo entrenado todavía, si
    faltan features obligatorias, o si el modelo cargado no coincide en dimensión con las
    features actuales (ej. un modelo entrenado antes de la Fase 4, con menos columnas) -- en
    cualquiera de esos casos el caller debe caer de vuelta a filters.rank_candidates()."""
    model = load_model("selector")
    if model is None:
        return None
    required = [features.get(k) for k in FEATURE_KEYS]
    if any(v is None for v in required):
        return None
    optional = [
        features.get(k) if features.get(k) is not None else OPTIONAL_FEATURE_DEFAULTS[k]
        for k in OPTIONAL_FEATURE_DEFAULTS
    ]
    try:
        values = [float(v) for v in required + optional]
    except (TypeError, ValueError):
        return None
    try:
        return float(model.predict_proba([values])[0][1])
    except Exception:
        # Defensivo: un modelo serializado con una dimensión de features distinta (ej. de
        # antes de la Fase 4) lanzaría aquí en vez de romper el ciclo -- se trata igual que
        # "no hay modelo utilizable".
        return None


def save_model(model, kind: str, n_samples: int, metrics: dict):
    import joblib
    buf = io.BytesIO()
    joblib.dump(model, buf)
    blob = base64.b64encode(buf.getvalue()).decode("ascii")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ml_models (kind, trained_at, n_samples, metrics, model_blob) VALUES (?,?,?,?,?)",
            (kind, datetime.now(timezone.utc).isoformat(), n_samples,
             json.dumps(metrics, ensure_ascii=False), blob),
        )


def load_model(kind: str):
    import joblib
    with get_conn() as conn:
        row = conn.execute(
            "SELECT model_blob FROM ml_models WHERE kind = ? ORDER BY trained_at DESC LIMIT 1",
            (kind,),
        ).fetchone()
    if not row or not row["model_blob"]:
        return None
    raw = base64.b64decode(row["model_blob"])
    return joblib.load(io.BytesIO(raw))


def build_calibration_set() -> list[dict]:
    """Fase 3 (2026-09-24): casos evaluados con confidence_score y resultado real, para ajustar
    la curva de calibración -- misma fuente que backtesting.get_performance_stats() (analyzed +
    evaluated), no una consulta nueva con criterios distintos."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT confidence_score, thesis_result
            FROM predictions
            WHERE status = 'evaluated' AND category = 'analyzed'
              AND confidence_score IS NOT NULL AND thesis_result IS NOT NULL
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _hit_weight(thesis_result: str) -> float:
    """Mismo criterio de 'acierto' que ya usa backtesting.get_performance_stats() en su fórmula
    de success_rate_pct (yes=1, partial=0.5, no=0) -- la calibración tiene que corregir la
    MISMA definición de acierto que el resto del dashboard ya muestra, no inventar una segunda."""
    return {"yes": 1.0, "partial": 0.5, "no": 0.0}.get(thesis_result, 0.0)


def train_calibration_model(rows: list[dict]):
    """Ajusta IsotonicRegression: confidence_score crudo (0-100) -> tasa de acierto empírica.
    Isotonic (monótona no-decreciente) porque la única corrección que tiene sentido aquí es
    'un confidence más alto nunca debería implicar peor tasa de acierto real' -- no se le pide
    a este modelo que aprenda una forma arbitraria, solo que enderece la curva."""
    from sklearn.isotonic import IsotonicRegression

    X = [r["confidence_score"] for r in rows]
    y = [_hit_weight(r["thesis_result"]) for r in rows]
    model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    model.fit(X, y)
    metrics = {"n_samples": len(rows)}
    return model, metrics


def calibrate_confidence(confidence_score) -> float | None:
    """Devuelve el confidence_score calibrado (0-100, misma escala que el original) o None si
    no hay modelo de calibración todavía o no se pasó un confidence_score -- el caller debe
    dejar la columna en NULL en ese caso, nunca inventar un valor."""
    if confidence_score is None:
        return None
    model = load_model("calibration")
    if model is None:
        return None
    try:
        return round(float(model.predict([float(confidence_score)])[0]) * 100, 1)
    except (TypeError, ValueError):
        return None


def latest_model_meta(kind: str) -> dict | None:
    """Metadata del modelo más reciente de este tipo, sin el blob (para mostrar en el
    dashboard: cuándo se entrenó, con cuántas muestras, y sus métricas/benchmark)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT kind, trained_at, n_samples, metrics FROM ml_models WHERE kind = ? ORDER BY trained_at DESC LIMIT 1",
            (kind,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("metrics"):
        try:
            d["metrics"] = json.loads(d["metrics"])
        except (TypeError, json.JSONDecodeError):
            pass
    return d
