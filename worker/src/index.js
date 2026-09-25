import { getDb, rowWithJson } from "./db.js";
import * as auth from "./auth.js";
import * as gh from "./github.js";
import { getPerformanceStats, getFilterEfficacyStats, getHistoryOverview } from "./backtesting.js";

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });

// Única fuente de verdad de los 5 veredictos posibles en este archivo (espejo de
// agents/schemas.py::VERDICT_VALUES en el backend Python -- no hay forma de compartir el
// literal entre Python y JS, así que se mantienen sincronizados a mano).
const VERDICT_VALUES = ["Strong Opportunity", "Watchlist", "High Risk / Speculative", "Reject", "Insufficient Evidence"];

const ADJUSTABLE_PARAMS = {
  MIN_LIQUIDITY_USD: "float",
  MIN_VOLUME_24H_USD: "float",
  MAX_MARKET_CAP_USD: "float",
  MIN_HOLDERS: "int",
  MAX_LISTING_AGE_DAYS: "int",
  MAX_CANDIDATES_PER_RUN: "int",
  MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY: "int",
  // Fase 1/2 (2026-09-24): decisión humana, nunca tocada por el flujo de auto-corrección.
  ML_SCORING_MODE: "enum:shadow,active",
  ENSEMBLE_JUDGE_MODE: "enum:shadow,active",
  // Fase 5 (2026-09-24): idem -- qué veredictos disparan Telegram, decisión humana.
  TELEGRAM_NOTIFY_VERDICTS: "multienum:" + VERDICT_VALUES.join(","),
};

async function saveDynamicConfig(db, updates) {
  const applied = {};
  const now = new Date().toISOString();
  for (const [key, rawValue] of Object.entries(updates)) {
    const kind = ADJUSTABLE_PARAMS[key];
    if (!kind || rawValue === null || rawValue === undefined) continue;
    let value;
    if (kind === "int") value = parseInt(rawValue, 10);
    else if (kind === "float") value = parseFloat(rawValue);
    else if (kind.startsWith("enum:")) {
      const valid = kind.slice("enum:".length).split(",");
      value = String(rawValue);
      if (!valid.includes(value)) continue;
    } else if (kind.startsWith("multienum:")) {
      const valid = kind.slice("multienum:".length).split(",");
      const items = Array.isArray(rawValue) ? rawValue.map(String) : String(rawValue).split(",").map((s) => s.trim());
      value = items.filter((x) => valid.includes(x));
      if (!value.length) continue;
    } else continue;
    if (typeof value === "number" && Number.isNaN(value)) continue;
    // String([...]) une con comas de forma nativa en JS -- mismo formato que ya usa
    // config.py del lado de Python para parsear TELEGRAM_NOTIFY_VERDICTS.
    await db.run(
      "INSERT INTO system_config (key, value, updated_at) VALUES (?, ?, ?) " +
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
      [key, String(value), now]
    );
    applied[key] = value;
  }
  return applied;
}

async function getConfigOverrides(db) {
  const rows = await db.all("SELECT key, value FROM system_config");
  const overrides = {};
  for (const r of rows) overrides[r.key] = r.value;
  return overrides;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // ---------- Rutas públicas (login) ----------
    if (path === "/login" || path.startsWith("/login-assets")) {
      return env.ASSETS.fetch(request);
    }
    if (path === "/api/auth/login" && request.method === "POST") {
      return handleLogin(request, env);
    }
    if (path === "/favicon.ico") {
      return env.ASSETS.fetch(request);
    }

    // ---------- Auth gate ----------
    const authed = await auth.isValidSession(env, request);
    if (!authed) {
      if (path.startsWith("/api/")) return json({ detail: "No autenticado" }, 401);
      return Response.redirect(new URL("/login", request.url), 302);
    }

    if (path === "/api/auth/logout" && request.method === "POST") {
      const token = auth.getSessionToken(request);
      await auth.destroySession(env, token);
      return new Response(JSON.stringify({ ok: true }), {
        headers: { "content-type": "application/json", "Set-Cookie": auth.clearCookieHeader() },
      });
    }

    if (path.startsWith("/api/")) {
      try {
        return await handleApi(path, request, env);
      } catch (e) {
        return json({ detail: String(e && e.message ? e.message : e) }, 500);
      }
    }

    // Dashboard estático (index.html, etc.)
    return env.ASSETS.fetch(request);
  },
};

async function handleLogin(request, env) {
  const body = await request.json().catch(() => ({}));
  const key = auth.clientKey(request, env);

  const { blocked } = await auth.checkLoginRateLimit(env, key);
  if (blocked) {
    return json({ detail: "Demasiados intentos fallidos. Intenta de nuevo más tarde." }, 429);
  }

  if (!auth.verifyCredentials(env, body.username, body.password)) {
    await auth.registerLoginFailure(env, key);
    return json({ detail: "Usuario o contraseña incorrectos" }, 401);
  }

  await auth.registerLoginSuccess(env, key);
  const { token, maxAge } = await auth.createSession(env);
  return new Response(JSON.stringify({ ok: true }), {
    headers: { "content-type": "application/json", "Set-Cookie": auth.sessionCookieHeader(token, maxAge) },
  });
}

async function handleApi(path, request, env) {
  const db = getDb(env);
  const url = new URL(request.url);

  // ---- Actualizar sistema: dispara el workflow de GitHub Actions ----
  if (path === "/api/update" && request.method === "POST") {
    await gh.dispatchUpdateWorkflow(env);
    return json({ dispatched: true, message: "Actualización disparada en GitHub Actions. Puede tardar varios minutos en aparecer." });
  }

  if (path === "/api/runs" && request.method === "GET") {
    const runs = await db.all("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 20");
    return json(runs.map(rowWithJson));
  }

  const runMatch = path.match(/^\/api\/runs\/([^/]+)$/);
  if (runMatch && request.method === "GET") {
    const row = await db.one("SELECT * FROM pipeline_runs WHERE id = ?", [runMatch[1]]);
    if (!row) return json({ detail: "run no encontrado" }, 404);
    return json(rowWithJson(row));
  }

  if (path === "/api/github/runs" && request.method === "GET") {
    return json(await gh.getRecentWorkflowRuns(env));
  }

  // ---- Predicciones ----
  if (path === "/api/predictions" && request.method === "GET") {
    const category = url.searchParams.get("category") || "analyzed";
    const runId = url.searchParams.get("run_id");
    const limit = parseInt(url.searchParams.get("limit") || "200", 10);
    let sql = "SELECT * FROM predictions WHERE category = ?";
    const args = [category];
    if (runId) {
      sql += " AND run_id = ?";
      args.push(runId);
    }
    sql += " ORDER BY created_at DESC LIMIT ?";
    args.push(limit);
    const rows = await db.all(sql, args);
    return json(rows.map(rowWithJson));
  }
  if (path === "/api/predictions/count" && request.method === "GET") {
    const category = url.searchParams.get("category") || "analyzed";
    const row = await db.one("SELECT COUNT(*) as n FROM predictions WHERE category = ?", [category]);
    return json({ category, count: row ? row.n : 0 });
  }

  const predMatch = path.match(/^\/api\/predictions\/(\d+)$/);
  if (predMatch && request.method === "GET") {
    const row = await db.one("SELECT * FROM predictions WHERE id = ?", [predMatch[1]]);
    if (!row) return json({ detail: "no encontrado" }, 404);
    return json(rowWithJson(row));
  }

  // ---- Backtesting / historial ----
  if (path === "/api/backtest/stats" && request.method === "GET") {
    return json(await getPerformanceStats(db));
  }
  if (path === "/api/backtest/filter-efficacy" && request.method === "GET") {
    return json(await getFilterEfficacyStats(db));
  }
  if (path === "/api/history/overview" && request.method === "GET") {
    return json(await getHistoryOverview(db));
  }

  // ---- Diagnósticos / auto-corrección ----
  if (path === "/api/diagnoses/latest" && request.method === "GET") {
    const row = await db.one("SELECT * FROM diagnoses ORDER BY created_at DESC LIMIT 1");
    if (!row) return json({ detail: "aún no hay diagnósticos generados" }, 404);
    return json(rowWithJson(row));
  }
  if (path === "/api/diagnoses" && request.method === "GET") {
    const rows = await db.all("SELECT * FROM diagnoses ORDER BY created_at DESC LIMIT 20");
    return json(rows.map(rowWithJson));
  }

  const applyMatch = path.match(/^\/api\/diagnoses\/(\d+)\/apply$/);
  if (applyMatch && request.method === "POST") {
    const diagId = applyMatch[1];
    const diag = await db.one("SELECT * FROM diagnoses WHERE id = ?", [diagId]);
    if (!diag) return json({ applied: [], message: "diagnóstico no encontrado" }, 404);
    const proposed = diag.proposed_adjustments ? JSON.parse(diag.proposed_adjustments) : [];
    const body = await request.json().catch(() => ({}));
    const acceptedParams = body.accepted_params || null;

    const updates = {};
    for (const adj of proposed) {
      if (!(adj.parameter in ADJUSTABLE_PARAMS)) continue;
      if (acceptedParams && !acceptedParams.includes(adj.parameter)) continue;
      updates[adj.parameter] = adj.proposed_value;
    }
    const appliedValues = await saveDynamicConfig(db, updates);
    const applied = Object.entries(appliedValues).map(([parameter, new_value]) => ({ parameter, new_value }));

    await db.run("UPDATE diagnoses SET status = 'applied', applied_adjustments = ? WHERE id = ?", [
      JSON.stringify(applied),
      diagId,
    ]);
    return json({ applied });
  }

  const rollbackMatch = path.match(/^\/api\/diagnoses\/(\d+)\/rollback$/);
  if (rollbackMatch && request.method === "POST") {
    // Fase 0c (2026-09-24): revierte al valor que el diagnóstico tenía registrado como
    // 'current_value' al momento de proponerse -- no necesariamente el más reciente si otro
    // ajuste tocó el mismo parámetro después. current_value es texto libre del LLM (ej.
    // "$50,000"), así que nunca se falla en silencio: lo que no se pudo revertir se reporta.
    const diagId = rollbackMatch[1];
    const diag = await db.one("SELECT * FROM diagnoses WHERE id = ?", [diagId]);
    if (!diag) return json({ rolled_back: [], failed: [], message: "diagnóstico no encontrado" }, 404);
    if (diag.status !== "applied") {
      return json({ rolled_back: [], failed: [], message: "este diagnóstico no está en estado 'applied', no hay nada que deshacer" });
    }
    const appliedAdjustments = diag.applied_adjustments ? JSON.parse(diag.applied_adjustments) : [];
    const proposed = diag.proposed_adjustments ? JSON.parse(diag.proposed_adjustments) : [];
    const proposedByParam = {};
    for (const p of proposed) proposedByParam[p.parameter] = p.current_value;

    const updates = {};
    const failed = [];
    for (const item of appliedAdjustments) {
      const param = item.parameter;
      if (!(param in ADJUSTABLE_PARAMS)) {
        failed.push({ parameter: param, reason: "parámetro ya no es ajustable" });
        continue;
      }
      const rawOriginal = proposedByParam[param];
      if (rawOriginal === undefined || rawOriginal === null) {
        failed.push({ parameter: param, reason: "no se encontró el valor original en este diagnóstico" });
        continue;
      }
      const cleaned = String(rawOriginal).replace(/[^\d.\-]/g, "");
      if (!cleaned || cleaned === "-" || cleaned === "." || cleaned === "-.") {
        failed.push({ parameter: param, reason: `no se pudo interpretar '${rawOriginal}' como número` });
        continue;
      }
      updates[param] = cleaned;
    }

    const appliedValues = Object.keys(updates).length ? await saveDynamicConfig(db, updates) : {};
    const rolledBack = Object.entries(appliedValues).map(([parameter, restored_value]) => ({ parameter, restored_value }));
    for (const param of Object.keys(updates)) {
      if (!(param in appliedValues)) failed.push({ parameter: param, reason: "no se pudo aplicar el valor restaurado" });
    }

    if (rolledBack.length) {
      await db.run("UPDATE diagnoses SET status = 'rolled_back' WHERE id = ?", [diagId]);
    }
    return json({ rolled_back: rolledBack, failed });
  }

  // ---- Configuración ----
  if (path === "/api/config" && request.method === "GET") {
    const overrides = await getConfigOverrides(db);
    const defaults = {
      MIN_LIQUIDITY_USD: 50000, MIN_VOLUME_24H_USD: 100000, MAX_MARKET_CAP_USD: 50000000,
      MIN_HOLDERS: 200, MAX_LISTING_AGE_DAYS: 120, MAX_CANDIDATES_PER_RUN: 15,
      MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY: 40,
    };
    const cfg = { ...defaults, ...overrides };
    for (const k of Object.keys(defaults)) cfg[k] = Number(cfg[k]);
    return json({
      ...cfg,
      ML_SCORING_MODE: overrides.ML_SCORING_MODE || "shadow",
      MIN_SAMPLES_FOR_ML: 30,
      ENSEMBLE_JUDGE_MODE: overrides.ENSEMBLE_JUDGE_MODE || "shadow",
      TELEGRAM_NOTIFY_VERDICTS: overrides.TELEGRAM_NOTIFY_VERDICTS
        ? overrides.TELEGRAM_NOTIFY_VERDICTS.split(",")
        : ["Strong Opportunity"],
      VERDICT_VALUES,
      PREDICTION_HORIZON_DAYS: 7,
      GEMINI_MODEL_FAST: "gemini-3.5-flash-lite",
      GEMINI_MODEL_SMART: "gemini-3.5-flash",
      GEMINI_ENABLE_SEARCH_GROUNDING: env.GEMINI_ENABLE_SEARCH_GROUNDING === "true",
      LOGIN_MAX_ATTEMPTS: parseInt(env.LOGIN_MAX_ATTEMPTS || "5", 10),
      LOGIN_LOCKOUT_MINUTES: parseInt(env.LOGIN_LOCKOUT_MINUTES || "15", 10),
    });
  }
  if (path === "/api/config" && request.method === "POST") {
    const body = await request.json().catch(() => ({}));
    const applied = await saveDynamicConfig(db, body);
    return json({ updated: applied });
  }

  const mlModelMatch = path.match(/^\/api\/ml-models\/([\w-]+)$/);
  if (mlModelMatch && request.method === "GET") {
    const kind = mlModelMatch[1];
    const row = await db.one(
      "SELECT kind, trained_at, n_samples, metrics FROM ml_models WHERE kind = ? ORDER BY trained_at DESC LIMIT 1",
      [kind]
    );
    if (!row) return json({ detail: `aún no hay ningún modelo entrenado de tipo '${kind}'` }, 404);
    return json(rowWithJson(row));
  }

  return json({ detail: "no encontrado" }, 404);
}
