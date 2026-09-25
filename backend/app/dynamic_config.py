"""
Umbrales de hard filters compartidos entre el Worker de Cloudflare (dashboard, donde se
editan), GitHub Actions (donde se aplican al correr el ciclo) y el modo local. Se guardan
en la tabla `system_config` de la misma base (Turso o SQLite local) en vez de vivir solo en
memoria de un proceso — así todos ven siempre el mismo valor, sin importar quién lo corrió.
"""
from datetime import datetime, timezone

from .config import settings
from .db import get_conn

def _enum_caster(valid: set[str]):
    """Constructor de "caster" para parámetros de texto con valores válidos limitados (ej.
    'shadow'/'active') -- reusa el mismo mecanismo de excepción-para-descartar que ya usan
    load_dynamic_config()/save_dynamic_config() para float/int, sin tener que tocar esa lógica."""
    def caster(v):
        v = str(v)
        if v not in valid:
            raise ValueError(f"valor inválido: {v!r} (válidos: {sorted(valid)})")
        return v
    return caster


_ADJUSTABLE_PARAMS = {
    "MIN_LIQUIDITY_USD": float,
    "MIN_VOLUME_24H_USD": float,
    "MAX_MARKET_CAP_USD": float,
    "MIN_HOLDERS": int,
    "MAX_LISTING_AGE_DAYS": int,
    "MAX_CANDIDATES_PER_RUN": int,
    "MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY": int,
    # Fase 1 (2026-09-24): decisión humana, nunca tocada por diagnosis.py (auto-corrección).
    "ML_SCORING_MODE": _enum_caster({"shadow", "active"}),
    # Fase 2 (2026-09-24): idem -- decisión humana, nunca tocada por diagnosis.py.
    "ENSEMBLE_JUDGE_MODE": _enum_caster({"shadow", "active"}),
}


def load_dynamic_config():
    """Lee overrides guardados en `system_config` y los aplica sobre `settings` en memoria."""
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM system_config").fetchall()
    for row in rows:
        key, value = row["key"], row["value"]
        cast = _ADJUSTABLE_PARAMS.get(key)
        if not cast:
            continue
        try:
            setattr(settings, key, cast(value))
        except (TypeError, ValueError):
            pass


def save_dynamic_config(updates: dict) -> dict:
    """Guarda overrides en `system_config` y los aplica de inmediato a `settings`."""
    applied = {}
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for key, value in updates.items():
            cast = _ADJUSTABLE_PARAMS.get(key)
            if not cast or value is None:
                continue
            try:
                casted = cast(value)
            except (TypeError, ValueError):
                continue
            conn.execute(
                "INSERT INTO system_config (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, str(casted), now),
            )
            setattr(settings, key, casted)
            applied[key] = casted
    return applied
