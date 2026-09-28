"""
Modelos de precio v2 (Plan v2, B2, 2026-09-28): selectores y modelo de riesgo entrenados con el
FEATURE STORE gratuito (features de klines 1h de los 7 días previos), no con el historial de
análisis LLM. Ver PREREGISTRO.md para el criterio de activación, fijado ANTES de mirar resultados.

Por qué existe: los análisis LLM son ~5/día (54 tokens con resultado), pero el control (tokens
descartados con precio) aporta ~300 tokens sin gastar una llamada de LLM. Medido en una muestra de
633 filas / 250 tokens (validación agrupada por token):
  * la volatilidad predice IGUAL tocar +20% (AUC 0.693) que caer >=20% (0.696): un modelo
    entrenado solo con 'tocó +20%' aprende 'elige tokens volátiles', no 'elige oportunidades'.
  * el momentum de 7 días es la única feature que predice subir sin predecir caer.
Por eso se entrenan TRES modelos con las mismas features y etiquetas distintas:
  touch20      tocó +20% en algún momento (etiqueta histórica, comparable con todo lo anterior)
  sustained10  cerró el horizonte en +10% o más (más realista, más ruidosa)
  drop20       cayó >=20% en algún momento (modelo de RIESGO, lo más sólido que hay hoy)

Features (fijadas de antemano; no se ajustan mirando resultados): log10(volumen/market cap),
volatilidad horaria 7d y momentum 7d. LogisticRegression con regularización fuerte (C=0.3): pocos
tokens distintos, así que un modelo simple y regularizado.

Todo se guarda en la tabla `ml_models` (kind='price_<label>'), como el resto de modelos.
"""
import math
import random
from collections import defaultdict

from .db import get_conn

LABELS = ("touch20", "sustained10", "drop20")
KIND_PREFIX = "price_"
FEATURE_NAMES = ["vol_mcap_log10", "vol_hourly_pct", "momentum_7d_pct"]
MIN_TOKENS_TO_TRAIN = 60          # con menos tokens distintos no se entrena (fallback: heurística)
CV_SPLITS = 100
TEST_TOKEN_FRACTION = 0.3
LOGREG_C = 0.3

# Criterio de activación PRERREGISTRADO (ver PREREGISTRO.md). No editar tras ver resultados.
ACTIVATION_MIN_TOKENS = 200
ACTIVATION_MIN_DAUC = 0.03
ACTIVATION_MAX_TOPQ_DROP_EXCESS = 0.0


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def label_of(row: dict, label: str) -> int | None:
    if label == "touch20":
        v = _num(row.get("max_return_pct"))
        return None if v is None else int(v >= 20.0)
    if label == "sustained10":
        v = _num(row.get("return_pct"))
        return None if v is None else int(v >= 10.0)
    if label == "drop20":
        v = _num(row.get("max_drawdown_pct"))
        return None if v is None else int(v <= -20.0)
    raise ValueError(label)


def feature_vector(volume_24h, market_cap, vol_hourly_pct, momentum_7d_pct) -> list[float] | None:
    vol, mcap = _num(volume_24h), _num(market_cap)
    vh, mo = _num(vol_hourly_pct), _num(momentum_7d_pct)
    if None in (vol, mcap, vh, mo) or mcap <= 0 or vol < 0:
        return None
    return [math.log10(vol / mcap + 1e-9), vh, mo]


def fetch_training_rows() -> list[dict]:
    """Filas evaluadas (analizadas Y control) con features de precio; una fila por predicción."""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            SELECT alpha_id, symbol, category, created_at, max_return_pct, return_pct, max_drawdown_pct,
                   volume_24h, market_cap, vol_hourly_pct, momentum_7d_pct
            FROM predictions
            WHERE status = 'evaluated' AND vol_hourly_pct IS NOT NULL AND momentum_7d_pct IS NOT NULL
            ORDER BY created_at ASC
            """
        ).fetchall()]
    out = []
    for r in rows:
        x = feature_vector(r["volume_24h"], r["market_cap"], r["vol_hourly_pct"], r["momentum_7d_pct"])
        if x is None:
            continue
        r["x"] = x
        r["token"] = r["alpha_id"] or r["symbol"]
        out.append(r)
    return out


def _auc(y, s) -> float:
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, s))


def _fit(X, y):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, C=LOGREG_C)).fit(X, y)


def train(rows: list[dict], label: str):
    """Modelo final (todas las filas con etiqueta). None si no hay dos clases o pocos tokens."""
    usable = [r for r in rows if label_of(r, label) is not None]
    y = [label_of(r, label) for r in usable]
    if len({r["token"] for r in usable}) < MIN_TOKENS_TO_TRAIN or len(set(y)) < 2:
        return None
    return _fit([r["x"] for r in usable], y)


def _top_quartile_stats(test_rows, scores, label_drop):
    k = max(5, len(test_rows) // 4)
    idx = sorted(range(len(test_rows)), key=lambda i: -scores[i])[:k]
    rets = [_num(test_rows[i].get("return_pct")) for i in idx]
    rets = [v for v in rets if v is not None]
    return {
        "mean_return": sum(rets) / len(rets) if rets else None,
        "drop_rate": sum(label_drop[i] for i in idx) / len(idx),
    }


def evaluate_grouped(rows: list[dict], label: str, splits: int = CV_SPLITS, seed: int = 9) -> dict:
    """Validación fuera de muestra AGRUPADA POR TOKEN: en cada partición ningún token está a la
    vez en entrenamiento y prueba (las filas de un mismo token no son independientes). Compara,
    con las MISMAS particiones, el modelo contra la línea base 'solo volumen/market cap'."""
    usable = [r for r in rows if label_of(r, label) is not None]
    tokens = sorted({r["token"] for r in usable})
    out = {"label": label, "n_rows": len(usable), "n_tokens": len(tokens),
           "base_rate": round(sum(label_of(r, label) for r in usable) / len(usable), 4) if usable else None}
    if len(tokens) < MIN_TOKENS_TO_TRAIN:
        out["warning"] = f"menos de {MIN_TOKENS_TO_TRAIN} tokens distintos: no se valida"
        return out
    rnd = random.Random(seed)
    aucs_m, aucs_b, deltas, deltas_floor, topq = [], [], [], [], []
    for _ in range(splits):
        test_tok = set(rnd.sample(tokens, max(1, int(len(tokens) * TEST_TOKEN_FRACTION))))
        tr = [r for r in usable if r["token"] not in test_tok]
        te = [r for r in usable if r["token"] in test_tok]
        ytr = [label_of(r, label) for r in tr]
        yte = [label_of(r, label) for r in te]
        if len(set(ytr)) < 2 or len(set(yte)) < 2:
            continue
        model = _fit([r["x"] for r in tr], ytr)
        base = _fit([[r["x"][0]] for r in tr], ytr)
        pm = model.predict_proba([r["x"] for r in te])[:, 1]
        pb = base.predict_proba([[r["x"][0]] for r in te])[:, 1]
        am, ab = _auc(yte, pm), _auc(yte, pb)
        # 'floor': una línea base por debajo de 0.5 solo es ruido (un modelo sin señal rinde 0.5),
        # comparar contra ella infla la mejora; el criterio de activación usa max(base, 0.5).
        aucs_m.append(am); aucs_b.append(ab); deltas.append(am - ab); deltas_floor.append(am - max(ab, 0.5))
        drop = [label_of(r, "drop20") or 0 for r in te]
        topq.append((_top_quartile_stats(te, pm, drop), sum(drop) / len(drop)))
    if not deltas:
        out["warning"] = "sin particiones válidas (falta una clase)"
        return out
    deltas.sort(); deltas_floor.sort()
    q = lambda xs, p: xs[min(len(xs) - 1, int(len(xs) * p))]
    rets = [t[0]["mean_return"] for t in topq if t[0]["mean_return"] is not None]
    out.update({
        "splits": len(deltas),
        "auc_model": round(sum(aucs_m) / len(aucs_m), 4),
        "auc_baseline_vol_mcap": round(sum(aucs_b) / len(aucs_b), 4),
        "dauc_mean": round(sum(deltas) / len(deltas), 4),
        "dauc_ci95": [round(q(deltas, 0.025), 4), round(q(deltas, 0.975), 4)],
        "dauc_floor_mean": round(sum(deltas_floor) / len(deltas_floor), 4),
        "dauc_floor_ci95": [round(q(deltas_floor, 0.025), 4), round(q(deltas_floor, 0.975), 4)],
        "topq_mean_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "topq_drop20_rate": round(sum(t[0]["drop_rate"] for t in topq) / len(topq), 4),
        "test_drop20_rate": round(sum(t[1] for t in topq) / len(topq), 4),
    })
    return out


def out_of_time_check(rows: list[dict], label: str, train_frac: float = 0.7) -> dict:
    """Entrena con el 70% MÁS ANTIGUO (por fecha) y prueba en el 30% más reciente, sin importar
    tokens: el mercado cambia de régimen (el acierto del control pasó de 21% a 43% entre semanas)."""
    usable = sorted([r for r in rows if label_of(r, label) is not None], key=lambda r: r["created_at"])
    cut = int(len(usable) * train_frac)
    tr, te = usable[:cut], usable[cut:]
    ytr, yte = [label_of(r, label) for r in tr], [label_of(r, label) for r in te]
    if len(set(ytr)) < 2 or len(set(yte)) < 2:
        return {"warning": "sin dos clases en entrenamiento o prueba"}
    model = _fit([r["x"] for r in tr], ytr)
    base = _fit([[r["x"][0]] for r in tr], ytr)
    am = _auc(yte, model.predict_proba([r["x"] for r in te])[:, 1])
    ab = _auc(yte, base.predict_proba([[r["x"][0]] for r in te])[:, 1])
    return {"n_train": len(tr), "n_test": len(te), "auc_model": round(am, 4),
            "auc_baseline_vol_mcap": round(ab, 4), "dauc": round(am - ab, 4),
            "dauc_floor": round(am - max(ab, 0.5), 4)}


def activation_verdict(sustained_eval: dict, oot: dict) -> dict:
    """Aplica el criterio PRERREGISTRADO de PREREGISTRO.md sobre la etiqueta primaria."""
    checks = {
        f">= {ACTIVATION_MIN_TOKENS} tokens distintos": (sustained_eval.get("n_tokens") or 0) >= ACTIVATION_MIN_TOKENS,
        f"mejora media sobre max(base, 0.5) >= +{ACTIVATION_MIN_DAUC}": (sustained_eval.get("dauc_floor_mean") or -1) >= ACTIVATION_MIN_DAUC,
        "IC95 de esa mejora por encima de 0": (sustained_eval.get("dauc_floor_ci95") or [-1, 0])[0] > 0,
        "cuartil superior no cae >=20% más que la base":
            (sustained_eval.get("topq_drop20_rate") is not None
             and sustained_eval["topq_drop20_rate"] <= sustained_eval.get("test_drop20_rate", 0) + ACTIVATION_MAX_TOPQ_DROP_EXCESS),
        "prueba fuera de tiempo con mejora sobre max(base, 0.5) > 0": (oot.get("dauc_floor") or -1) > 0,
    }
    return {"checks": checks, "meets_criteria": all(checks.values())}


# ---- Uso en el ciclo -------------------------------------------------------------------------

_MODELS: dict | None = None


def _load_all() -> dict:
    """Carga los 3 modelos UNA vez por proceso (un ciclo = un proceso): evita 1 consulta a la
    base por token."""
    global _MODELS
    if _MODELS is None:
        from .ml_scoring import load_model
        loaded = {}
        for label in LABELS:
            try:
                loaded[label] = load_model(KIND_PREFIX + label)
            except Exception:
                loaded[label] = None
        _MODELS = loaded
    return _MODELS


def reset_cache():
    global _MODELS
    _MODELS = None


def score_token(token: dict, features: dict) -> dict:
    """Probabilidad de cada etiqueta para un token, a partir de sus features ya calculadas
    (`market_stats.price_features`). Dict con las 3 claves; None donde no hay modelo o faltan
    features -- nunca se inventa un valor."""
    x = feature_vector(token.get("volume24h"), token.get("marketCap"),
                       features.get("vol_hourly_pct"), features.get("momentum_7d_pct"))
    models = _load_all()
    out = {}
    for label in LABELS:
        m = models.get(label)
        if x is None or m is None:
            out[label] = None
            continue
        try:
            out[label] = round(float(m.predict_proba([x])[0][1]), 4)
        except Exception:
            out[label] = None
    return out


def score_columns(token: dict, features: dict) -> dict:
    """Las 3 probabilidades con el nombre de columna de `predictions` (pm_*)."""
    sc = score_token(token, features)
    return {"pm_" + label: sc[label] for label in LABELS}


def rank_by_price_model(tokens: list[dict], features_by_alpha: dict) -> list[dict] | None:
    """Ranking pre-registrado: primero se excluye el cuartil de mayor riesgo de caída (drop20),
    y el resto se ordena por probabilidad de éxito sostenido (sustained10). None si no hay
    modelos o ningún token tiene features -- el caller debe caer a la heurística."""
    scored = []
    for t in tokens:
        f = features_by_alpha.get(t.get("alphaId"))
        if not f:
            continue
        s = score_token(t, f)
        if s["sustained10"] is None or s["drop20"] is None:
            continue
        scored.append((s, t))
    if not scored:
        return None
    risks = sorted(s["drop20"] for s, _ in scored)
    cutoff = risks[int(len(risks) * 0.75)] if len(risks) >= 4 else float("inf")
    kept = [(s, t) for s, t in scored if s["drop20"] <= cutoff]
    kept.sort(key=lambda st: st[0]["sustained10"], reverse=True)
    return [t for _, t in kept]
