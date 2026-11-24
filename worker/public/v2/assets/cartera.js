const root = document.getElementById("carteraApp");
const usd = new Intl.NumberFormat("es-MX", { style: "currency", currency: "USD", currencyDisplay: "narrowSymbol" });
const prices = new Intl.NumberFormat("es-MX", { maximumSignificantDigits: 8 });
const times = new Intl.DateTimeFormat("es-MX", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const shortDay = new Intl.DateTimeFormat("es-MX", { month: "short", day: "numeric", timeZone: "UTC" });
const scenarios = {
  hold: { label: "Mantener hasta el cierre", rule: "Mantén la posición hasta el horizonte elegido." },
  peak: { label: "Máximo observado", rule: "Vende al precio más alto registrado en el periodo. Este escenario retrospectivo requiere conocer el futuro." },
  stop15: { label: "Stop −15%", stop: "stop15", rule: "Activa la salida al observar una pérdida del 15%. Si el precio sigue por encima, usa el cierre." },
  stop20: { label: "Stop −20%", stop: "stop20", rule: "Activa la salida al observar una pérdida del 20%. Si el precio sigue por encima, usa el cierre." },
  tp30_sl15: { label: "Objetivo +30% · stop −15%", target: "target30", stop: "stop15", rule: "Sal al primer objetivo o stop observado. Si ninguno se activa, usa el cierre." },
  tp40_sl20: { label: "Objetivo +40% · stop −20%", target: "target40", stop: "stop20", rule: "Sal al primer objetivo o stop observado. Si ninguno se activa, usa el cierre." },
};
const statuses = { settled: "Medida", pending: "Pendiente", missing: "Falta precio", ambiguous: "Orden incierto" };
const chains = { bsc: "BNB Chain", solana: "Solana" };
let active = "hold";
let current = null;
let request = null;
let linked = null;
let initialized = false;

function el(tag, className = "", text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function money(n) { return Number.isFinite(n) ? usd.format(n) : "Sin dato"; }
function signed(n) { return Number.isFinite(n) ? (n > 0 ? "+" : "") + usd.format(n) : "Sin dato"; }
function price(n) { return Number.isFinite(n) && n > 0 ? "$" + prices.format(n) : "Sin dato"; }
function time(s) { const d = new Date(s); return s && Number.isFinite(d.getTime()) ? times.format(d) + " UTC" : "Sin fecha"; }
function date(s) { const d = new Date(s + "T00:00:00Z"); return Number.isFinite(d.getTime()) ? shortDay.format(d) + " " + d.getUTCFullYear() : s; }
function field(key) { return root.querySelector('[name="' + key + '"]'); }
function area(key) { return root.querySelector('[data-paper="' + key + '"]'); }
function direction(n) { return Number.isFinite(n) ? (n > 0 ? "paper-positive" : n < 0 ? "paper-negative" : "") : ""; }
function detailLine(label, value) {
  const row = el("div", "paper-detail-line");
  row.append(el("dt", "", label), el("dd", "", value));
  return row;
}
function metric(label, value, extra = "") {
  const block = el("div", "paper-metric");
  block.append(el("span", "", label), el("strong", extra, value));
  return block;
}
function initialize() {
  if (!root || initialized) return;
  initialized = true;
  root.innerHTML = `
    <header class="paper-header"><h1>Cartera simulada</h1>
      <p>Compara qué habría pasado al asignar el mismo importe a cada token seleccionado durante una semana.</p></header>
    <form class="paper-filters" aria-label="Definir la cartera">
      <label>Semana de entrada<select name="week"><option value="">Última registrada</option></select></label>
      <label>Selección<select name="cohort"><option value="strong">Oportunidad fuerte</option><option value="watchlist">En vigilancia</option><option value="all">Todos los analizados</option></select></label>
      <label>Cadena<select name="chain"><option value="all">Ambas cadenas</option><option value="bsc">BNB Chain</option><option value="solana">Solana</option></select></label>
      <label>Plazo por token<select name="days"><option value="7">7 días</option><option value="14">14 días</option><option value="30">30 días</option></select></label>
      <label>USD por token<input name="stake" type="number" inputmode="decimal" min="0.01" max="100000" step="0.01" value="100" required></label>
      <button type="submit" class="paper-apply">Calcular</button>
    </form>
    <p class="paper-status" data-paper="status" role="status"></p>
    <div class="paper-results" data-paper="results"></div>`;
  const form = root.querySelector("form");
  form.addEventListener("submit", event => { event.preventDefault(); linked = null; load(); });
  form.addEventListener("change", event => {
    if (event.target.tagName === "SELECT" && form.reportValidity()) { linked = null; load(); }
  });
}
function updateWeeks(data) {
  const select = field("week");
  select.replaceChildren(new Option("Última registrada", ""));
  const available = new Set((data.weeks || []).map(w => w.week));
  if (data.week) available.add(data.week);
  for (const week of [...available].sort().reverse()) select.add(new Option("Del " + date(week), week));
  select.value = data.week || "";
}
async function load() {
  if (!root || !root.querySelector("form").reportValidity()) return;
  request?.abort();
  const controller = new AbortController();
  request = controller;
  const params = new URLSearchParams(new FormData(root.querySelector("form")));
  if (!params.get("week")) params.delete("week");
  current = null;
  area("results").replaceChildren();
  area("results").setAttribute("aria-busy", "true");
  area("status").textContent = "Consultando las entradas y sus precios…";
  const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch("/api/v2/portfolio?" + params, { signal: controller.signal, credentials: "same-origin" });
    if (response.status === 401) { location.assign("/login"); return; }
    if (!response.ok) throw Error("request");
    const data = await response.json();
    if (request !== controller) return;
    if (!Array.isArray(data.positions) || !Array.isArray(data.strategies)) throw Error("format");
    current = data;
    updateWeeks(data);
    area("status").textContent = "";
    render();
  } catch {
    if (request !== controller) return;
    area("status").textContent = "No se pudo consultar esta cartera. Pulsa Calcular para reintentar.";
  } finally {
    clearTimeout(timeout);
    if (request === controller) area("results").removeAttribute("aria-busy");
  }
}
function positionView(position, expanded) {
  const definition = scenarios[active];
  const result = position.strategies?.[active] || {};
  const item = el("details", "paper-position");
  item.open = expanded;
  item.dataset.position = String(position.id);
  const title = el("summary", "paper-position-title");
  const identity = el("span", "paper-position-id");
  identity.append(el("strong", "", position.symbol || position.name || "Token"), el("small", "", chains[position.chain] || position.chain));
  const outcome = el("span", "paper-position-return");
  outcome.append(el("b", direction(result.pnl_usd), result.status === "settled" ? signed(result.pnl_usd) : statuses[result.status] || "Sin dato"));
  if (result.status === "settled") outcome.append(el("small", "", "Ganancia / pérdida"));
  title.append(identity, outcome, el("span", "paper-chevron", "⌄"));
  title.lastChild.setAttribute("aria-hidden", "true");
  const body = el("div", "paper-position-body");
  if (position.name) body.append(el("p", "paper-token-name", position.name));
  if (position.token_key) body.append(el("code", "paper-address", position.token_key));
  const lines = el("dl", "paper-position-lines");
  lines.append(detailLine("Veredicto de entrada", position.verdict || "Sin dato"), detailLine("Fecha de entrada", time(position.entry_at)),
    detailLine("Entrada por token", price(position.entry_price)));
  if (definition.target) lines.append(detailLine("Toma de ganancias", price(position.levels?.[definition.target])));
  if (definition.stop) lines.append(detailLine("Activación del stop", price(position.levels?.[definition.stop])));
  lines.append(detailLine("Precio de salida", price(result.exit_price)));
  if (result.exit_at) lines.append(detailLine("Fecha de salida", time(result.exit_at)));
  else if (result.interval_start && result.interval_end) {
    lines.append(detailLine("Vela de la salida", time(result.interval_start) + " – " + time(result.interval_end)));
  }
  if (result.status === "settled") lines.append(detailLine("Valor al salir", money(result.value_usd)));
  body.append(lines);
  if (result.reason) body.append(el("p", "paper-exit-note", result.reason));
  if (result.source) body.append(el("small", "paper-source", "Fuente de precios · " + result.source));
  item.append(title, body);
  return item;
}
function render(options = {}) {
  const data = current;
  if (!data) return;
  const outlet = area("results");
  const opened = new Set([...outlet.querySelectorAll(".paper-position[open]")].map(node => node.dataset.position));
  outlet.replaceChildren();
  const count = data.positions.length;
  if (Number(data.excluded_identity) > 0) outlet.append(el("p", "paper-coverage-note", data.excluded_identity + " registros sin identidad de contrato verificable quedaron fuera de esta cartera."));
  if (!count) {
    const empty = el("section", "paper-empty");
    empty.append(el("h2", "", "Esta selección quedó vacía"), el("p", "", "CIS no registró entradas que coincidan con la semana, la cadena y el veredicto elegidos."));
    if (data.cohort !== "all") {
      const widen = el("button", "paper-link", "Ver todos los analizados de esta semana");
      widen.type = "button";
      widen.onclick = () => { field("cohort").value = "all"; linked = null; load(); };
      empty.append(widen);
    }
    outlet.append(empty); return;
  }
  const strategy = data.strategies.find(s => s.key === active);
  if (!strategy || !scenarios[active]) { outlet.append(el("p", "paper-status", "Faltan los resultados de esta estrategia.")); return; }
  const heading = el("section", "paper-summary");
  heading.append(el("p", "paper-week", data.week ? "Semana del " + date(data.week) + " · " + data.days + " días por posición" : "Simulación por posición"));
  heading.append(el("h2", "", scenarios[active].label), el("p", "paper-rule", scenarios[active].rule));
  const metrics = el("div", "paper-metrics");
  const complete = Number.isFinite(strategy.final_value_usd);
  metrics.append(metric("Capital asignado", money(strategy.allocated_usd)),
    metric(complete ? "Valor final" : "Valor de las medidas", complete ? money(strategy.final_value_usd) : strategy.settled ? money(strategy.settled_value_usd) : "Por medir"),
    metric(complete ? "Ganancia / pérdida" : "Ganancia / pérdida medida", strategy.settled ? signed(strategy.settled_pnl_usd) : "Por medir", direction(strategy.settled_pnl_usd)));
  const coverage = el("p", "paper-settled", strategy.settled + " de " + count + " posiciones medidas" + (strategy.settled ? " · capital de esas posiciones " + money(strategy.settled_capital_usd) : "") + ".");
  heading.append(metrics, coverage);
  outlet.append(heading);
  const comparison = el("section", "paper-comparison");
  comparison.append(el("h2", "", "Comparar salidas"));
  const rows = el("div", "paper-choices");
  rows.setAttribute("role", "group"); rows.setAttribute("aria-label", "Seleccionar una regla de salida");
  for (const [key, definition] of Object.entries(scenarios)) {
    const sums = data.strategies.find(s => s.key === key);
    if (!sums) continue;
    const button = el("button", "paper-choice");
    button.type = "button"; button.dataset.strategy = key;
    button.setAttribute("aria-pressed", String(key === active));
    const label = el("span", "paper-choice-label");
    label.append(el("b", "", definition.label), el("small", "", key === "peak" ? "Referencia retrospectiva" : sums.settled + " de " + count + " medidas"));
    const value = el("span", "paper-choice-amount");
    value.append(el("b", direction(sums.settled_pnl_usd), sums.settled ? signed(sums.settled_pnl_usd) : "Por medir"));
    if (sums.settled) value.append(el("small", "", "P&L medido"));
    button.append(label, value);
    button.onclick = () => { active = key; render({ focus: key }); };
    rows.append(button);
  }
  comparison.append(rows);
  outlet.append(comparison);
  const detail = el("section", "paper-positions");
  detail.append(el("h2", "", "Posiciones"), el("p", "paper-muted", "Cada entrada usa " + money(data.stake) + " USD. Abre un token para ver sus precios y la salida simulada."));
  const breakdown = [[strategy.pending, "pendientes"], [strategy.missing, "sin precio suficiente"], [strategy.ambiguous, "con orden de salida incierto"]]
    .filter(([n]) => n > 0).map(([n, label]) => n + " " + label);
  if (breakdown.length) detail.append(el("p", "paper-coverage-note", "Cobertura parcial · " + breakdown.join(" · ")));
  let match = null;
  if (linked) {
    match = data.positions.find(p => String(p.id) === String(linked.id)) ||
      data.positions.find(p => linked.token_key && p.token_key === linked.token_key);
    if (!match) detail.append(el("p", "paper-coverage-note", "La entrada consultada no aparece entre las posiciones identificadas de esta selección semanal."));
  }
  let expandedMatch = null;
  for (const pos of data.positions) {
    const item = positionView(pos, opened.has(String(pos.id)) || pos === match);
    if (pos === match) expandedMatch = item;
    detail.append(item);
  }
  outlet.append(detail);
  const assumptions = el("details", "paper-method");
  assumptions.append(el("summary", "", "Supuestos de la simulación"),
    el("p", "", "Se toma la primera señal que cumple la selección por cadena y contrato en cada semana UTC. Cada posición tiene su propio plazo desde la entrada. Los escenarios usan las mismas posiciones."),
    el("p", "", "La entrada usa el precio archivado con la señal. Se simula una comisión de " + Number(data.fee_bps) / 100 + "% por lado en USD. El cálculo omite gas, impuestos del token e impacto de mercado. La salida de un stop puede quedar por debajo de su nivel de activación."),
    el("p", "", "El cierre usa la primera cotización válida desde el final del plazo, con una tolerancia de 90 minutos para velas de Binance Alpha y 18 horas para muestras DEX. El máximo es el mejor precio observado dentro del plazo. Si una vela toca objetivo y stop sin revelar el orden, la posición queda como incierta."));
  outlet.append(assumptions);
  if (options.focus) rows.querySelector('[data-strategy="' + options.focus + '"]')?.focus({ preventScroll: true });
  if (linked && expandedMatch) {
    expandedMatch.querySelector("summary").focus({ preventScroll: true });
    expandedMatch.scrollIntoView({ block: "nearest" });
  }
  linked = null;
}
function show() { initialize(); if (root && !current && !area("results").hasAttribute("aria-busy")) load(); }
window.addEventListener("cis:view", event => { if (event.detail === "cartera") show(); });
window.addEventListener("cis:paper-position", event => {
  if (!root) return;
  initialize();
  const item = event.detail;
  if (!item?.entry_at) return;
  const monday = new Date(item.entry_at);
  if (!Number.isFinite(monday.getTime())) return;
  monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() + 6) % 7);
  const week = monday.toISOString().slice(0, 10);
  if (![...field("week").options].some(opt => opt.value === week)) field("week").add(new Option("Del " + date(week), week));
  field("week").value = week;
  field("chain").value = chains[item.chain] ? item.chain : "all";
  field("cohort").value = item.verdict === "Strong Opportunity" ? "strong" : item.verdict === "Watchlist" ? "watchlist" : "all";
  active = "hold"; linked = item; load();
});
if (location.hash === "#cartera") show();
