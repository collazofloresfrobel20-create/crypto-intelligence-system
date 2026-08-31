const SESSION_COOKIE = "cis_session";

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function getCookie(request, name) {
  const header = request.headers.get("Cookie") || "";
  const match = header.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export function clientKey(request, env) {
  return request.headers.get("cf-connecting-ip") || "unknown";
}

export function verifyCredentials(env, username, password) {
  return (
    timingSafeEqual(String(username || ""), env.AUTH_USERNAME) &&
    timingSafeEqual(String(password || ""), env.AUTH_PASSWORD)
  );
}

export async function checkLoginRateLimit(env, key) {
  const raw = await env.SESSIONS.get(`loginfail:${key}`);
  if (!raw) return { blocked: false };
  const data = JSON.parse(raw);
  const maxAttempts = parseInt(env.LOGIN_MAX_ATTEMPTS || "5", 10);
  if (data.count >= maxAttempts) {
    return { blocked: true };
  }
  return { blocked: false };
}

export async function registerLoginFailure(env, key) {
  const maxAttempts = parseInt(env.LOGIN_MAX_ATTEMPTS || "5", 10);
  const lockoutMinutes = parseInt(env.LOGIN_LOCKOUT_MINUTES || "15", 10);
  const raw = await env.SESSIONS.get(`loginfail:${key}`);
  const data = raw ? JSON.parse(raw) : { count: 0 };
  data.count += 1;
  // TTL: mientras no llegue al máximo, expira rápido (ventana de intentos corta); al
  // llegar al máximo, el registro dura el tiempo del bloqueo completo.
  const ttl = data.count >= maxAttempts ? lockoutMinutes * 60 : 10 * 60;
  await env.SESSIONS.put(`loginfail:${key}`, JSON.stringify(data), { expirationTtl: ttl });
}

export async function registerLoginSuccess(env, key) {
  await env.SESSIONS.delete(`loginfail:${key}`);
}

export async function createSession(env) {
  const token = crypto.randomUUID() + crypto.randomUUID();
  const days = parseInt(env.SESSION_DAYS || "30", 10);
  await env.SESSIONS.put(`session:${token}`, "1", { expirationTtl: days * 24 * 3600 });
  return { token, maxAge: days * 24 * 3600 };
}

export async function destroySession(env, token) {
  if (token) await env.SESSIONS.delete(`session:${token}`);
}

export async function isValidSession(env, request) {
  const token = getCookie(request, SESSION_COOKIE);
  if (!token) return false;
  const value = await env.SESSIONS.get(`session:${token}`);
  return value !== null;
}

export function sessionCookieHeader(token, maxAge) {
  return `${SESSION_COOKIE}=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${maxAge}`;
}

export function clearCookieHeader() {
  return `${SESSION_COOKIE}=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0`;
}

export function getSessionToken(request) {
  return getCookie(request, SESSION_COOKIE);
}

export { SESSION_COOKIE };
