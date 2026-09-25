import { createClient } from "@libsql/client/web";

/**
 * Envuelve el cliente de Turso para que cada consulta devuelva objetos planos
 * {columna: valor}, igual que el adaptador del lado Python (backend/app/db.py) — así la
 * lógica de agregación (backtesting.js) se lee igual de un lado y del otro.
 */
export function getDb(env) {
  // El cliente Python (libsql-client) mapea "libsql://" a WebSocket por defecto, y ese
  // handshake falló en pruebas reales contra Turso (400 Invalid response status). "https://"
  // fuerza HTTP plano. @libsql/client/web ya es HTTP-only por diseño, pero se normaliza igual
  // por si acaso y para que ambos lados traten la URL de forma idéntica.
  const httpUrl = (env.TURSO_DATABASE_URL || "").replace("libsql://", "https://");
  const client = createClient({
    url: httpUrl,
    authToken: env.TURSO_AUTH_TOKEN,
  });

  return {
    async all(sql, args = []) {
      const rs = await client.execute({ sql, args });
      return rs.rows.map((row) => rowToObject(rs.columns, row));
    },
    async one(sql, args = []) {
      const rows = await this.all(sql, args);
      return rows[0] || null;
    },
    async run(sql, args = []) {
      return client.execute({ sql, args });
    },
  };
}

function rowToObject(columns, row) {
  const obj = {};
  columns.forEach((col, i) => {
    obj[col] = row[i];
  });
  return obj;
}

export function parseJsonField(value) {
  if (value === null || value === undefined || value === "") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

const JSON_FIELDS = [
  "key_evidence", "main_risks", "agent_findings", "rejection_reasons",
  "patterns_found", "proposed_adjustments", "applied_adjustments",
  "rejection_margins", "metrics",
];

export function rowWithJson(row) {
  const out = { ...row };
  for (const f of JSON_FIELDS) {
    if (out[f]) out[f] = parseJsonField(out[f]);
  }
  return out;
}
