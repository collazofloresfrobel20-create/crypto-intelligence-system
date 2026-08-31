"""
Autenticación simple de sesión para un sistema privado de un solo usuario/grupo pequeño.
No es multi-usuario ni pretende serlo: usuario y contraseña fijos por variable de entorno,
un token de sesión aleatorio guardado en SQLite, cookie httponly. Suficiente para "que solo
tenga acceso yo o gente relacionada" sin la complejidad de un sistema de cuentas real.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request, HTTPException

from .config import settings
from .db import get_conn

SESSION_COOKIE = "cis_session"
SESSION_DAYS = 30

_PUBLIC_PATHS = {"/login", "/api/auth/login", "/favicon.ico"}
_PUBLIC_PREFIXES = ("/login-assets",)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username.strip(), settings.AUTH_USERNAME)
    pass_ok = hmac.compare_digest(_hash(password), _hash(settings.AUTH_PASSWORD))
    return user_ok and pass_ok


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_DAYS)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, now.isoformat(), expires.isoformat()),
        )
    return token


def destroy_session(token: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def is_valid_session(token: str | None) -> bool:
    if not token:
        return False
    with get_conn() as conn:
        row = conn.execute("SELECT expires_at FROM sessions WHERE token = ?", (token,)).fetchone()
    if not row:
        return False
    expires = datetime.fromisoformat(row["expires_at"])
    return datetime.now(timezone.utc) < expires


def is_public_path(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


def require_session(request: Request):
    """Dependency para proteger rutas de API individuales."""
    token = request.cookies.get(SESSION_COOKIE)
    if not is_valid_session(token):
        raise HTTPException(401, "No autenticado")


def client_key(request: Request) -> str:
    """
    Identificador del cliente para el rate-limit de login. Si el sistema queda expuesto
    detrás de Cloudflare Tunnel, la IP real del visitante viene en CF-Connecting-IP (Cloudflare
    la agrega siempre); request.client.host sería la IP interna del túnel, no la real.
    """
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")


def check_login_rate_limit(key: str):
    """Lanza 429 si el cliente está bloqueado por demasiados intentos fallidos recientes."""
    with get_conn() as conn:
        row = conn.execute("SELECT locked_until FROM login_attempts WHERE client_key = ?", (key,)).fetchone()
    if row and row["locked_until"]:
        locked_until = datetime.fromisoformat(row["locked_until"])
        if datetime.now(timezone.utc) < locked_until:
            remaining_min = int((locked_until - datetime.now(timezone.utc)).total_seconds() / 60) + 1
            raise HTTPException(429, f"Demasiados intentos fallidos. Intenta de nuevo en ~{remaining_min} min.")


def register_login_failure(key: str):
    with get_conn() as conn:
        row = conn.execute("SELECT failed_count FROM login_attempts WHERE client_key = ?", (key,)).fetchone()
        count = (row["failed_count"] if row else 0) + 1
        locked_until = None
        if count >= settings.LOGIN_MAX_ATTEMPTS:
            locked_until = (datetime.now(timezone.utc) + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)).isoformat()
        if row:
            conn.execute(
                "UPDATE login_attempts SET failed_count = ?, locked_until = ? WHERE client_key = ?",
                (count, locked_until, key),
            )
        else:
            conn.execute(
                "INSERT INTO login_attempts (client_key, failed_count, locked_until) VALUES (?, ?, ?)",
                (key, count, locked_until),
            )


def register_login_success(key: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM login_attempts WHERE client_key = ?", (key,))
