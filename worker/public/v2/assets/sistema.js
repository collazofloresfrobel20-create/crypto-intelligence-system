const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function escaped(value) {
  if (typeof value==="string") return esc(value);
  if (Array.isArray(value)) return value.map(escaped);
  if (value && typeof value==="object") return Object.fromEntries(Object.entries(value).map(([k,v])=>[esc(k),escaped(v)]));
  return value;
}
async function api(path,opts) {
  const response=await fetch(path,opts);
  if(response.status===401){location.assign("/login");throw Error("Sesión vencida");}
  if(!response.ok) throw Error("Consulta incompleta ("+response.status+")");
  return escaped(await response.json());
}
$("btnLogout").onclick=async event=>{
  event.preventDefault();
  await api("/api/auth/logout",{method:"POST"});
  location.assign("/login");
};
$("runCycle").onclick=async event=>{
  const b=event.currentTarget;b.disabled=true;
  try {await api("/api/update",{method:"POST"});$("runStatus").textContent="Ciclo solicitado. Consulta su avance al recargar los datos.";}
  catch(err){$("runStatus").textContent=err.message;b.disabled=false;}
};


const VERDICT_CLASS = {
  "Strong Opportunity": "v-strong",
  "Watchlist": "v-watch",
  "High Risk / Speculative": "v-highrisk",
  "Reject": "v-reject",
  "Insufficient Evidence": "v-insufficient",
};

// ---------- Gráficos SVG (sin librerías externas) ----------

// SVG chico a proposito (una version anterior mas grande hacia fallar la captura de
// pantalla en algunos entornos) -- las descripciones largas van en HTML aparte, no en el SVG.
const PIPELINE_STAGES = [
  ["Descubrimiento", "Universo de tokens en Binance Alpha"],
  ["Filtros duros", "Liquidez · volumen · market cap · holders · antigüedad"],
  ["7 analistas en paralelo", "Fundamental · Tokenomics · On-chain · Market · Narrativa · Seguridad · Social"],
  ["Bull vs Bear", "Tesis alcista construida y luego atacada con la misma evidencia"],
  ["Mediador", "¿Qué argumento sobrevive al debate?"],
  ["Juez", "5 scores independientes + veredicto"],
  ["+7 días después", "Se revisa el precio real y registra el resultado para evaluación"],
];

function renderPipelineDiagram() {
  // HTML/CSS puro (no SVG): en este entorno un SVG en esta posicion hacia que la captura de
  // pantalla completa saliera en blanco, incluso ya achicado y sin <marker>. Con divs no pasa.
  const n = PIPELINE_STAGES.length;
  const steps = PIPELINE_STAGES.map(([title, sub], i) => `
    <div class="pipeline-step">
      <div class="pipeline-step-box">${title}</div>
      ${i < n - 1 ? '<div class="pipeline-step-arrow">&darr;</div>' : ""}
      <div class="pipeline-step-sub">${sub}</div>
    </div>
  `).join("");

  return `<div class="pipeline-steps">${steps}</div>`;
}

function fmtPrice(v) {
  if (v == null || isNaN(v)) return "-";
  if (v >= 1) return "$" + v.toFixed(3);
  if (v >= 0.01) return "$" + v.toFixed(5);
  return "$" + v.toPrecision(3);
}

function fmtPct(v) {
  if (v == null || isNaN(v)) return "-";
  return (v > 0 ? "+" : "") + v + "%";
}

// Envuelve fmtPct en color funcional (positivo/negativo) -- para valores REALES u
// observados en vivo, nunca para scores 0-100 (esos no tienen signo).
function fmtPctColored(v) {
  const text = fmtPct(v);
  if (v == null || isNaN(v) || v === 0) return text;
  return `<span class="${v > 0 ? "pct-positive" : "pct-negative"}">${text}</span>`;
}

// Nota: aquí solo se muestran datos observados (precio al análisis, precio actual/real, %
// desde el análisis) -- nunca niveles de entrada/salida calculados. El sistema es de
// investigación, no da instrucciones de cuándo comprar/vender (ver disclaimer del header).
function projectionBox(p) {
  const createdAt = new Date(p.created_at);
  const horizonDays = p.horizon_days || 7;
  const deadline = new Date(createdAt.getTime() + horizonDays * 86400000);

  if (p.status === "evaluated") {
    const hit = p.thesis_result === "yes" ? "Sí llegó" : (p.thesis_result === "partial" ? "Se acercó" : "No llegó");
    return `
      <div class="projection-box">
        <div class="projection-item"><div class="val">${fmtPrice(p.price_at_prediction)} &rarr; ${fmtPrice(p.price_after)}</div><div class="lbl">Precio: análisis &rarr; cierre día ${horizonDays}</div></div>
        <div class="projection-item"><div class="val">${fmtPctColored(p.max_return_pct)}</div><div class="lbl">Máximo alcanzado (real)</div></div>
        ${p.return_at_day1_pct != null ? `<div class="projection-item"><div class="val">${fmtPctColored(p.return_at_day1_pct)}</div><div class="lbl">Retorno al día 1 (real)</div></div>` : ""}
        ${p.return_at_day3_pct != null ? `<div class="projection-item"><div class="val">${fmtPctColored(p.return_at_day3_pct)}</div><div class="lbl">Retorno al día 3 (real)</div></div>` : ""}
        <div class="projection-item"><div class="val">${fmtPctColored(p.return_pct)}</div><div class="lbl">Al cierre del día ${horizonDays} (real)</div></div>
        <div class="projection-item"><div class="val">${hit}</div><div class="lbl">¿Llegó a +20&ndash;30%?</div></div>
        ${p.volatility_pct != null ? `<div class="projection-item"><div class="val">${p.volatility_pct}%</div><div class="lbl">Volatilidad realizada</div></div>` : ""}
      </div>
    `;
  }

  if (p.status === "unevaluable") {
    return `
      <div class="projection-box">
        <div class="projection-item"><div class="val">${fmtPrice(p.price_at_prediction)}</div><div class="lbl">Precio al análisis</div></div>
        <div class="projection-item"><div class="val">Sin datos</div><div class="lbl">No se pudo conseguir precio para evaluarla (típicamente ya no listado en Binance Alpha)</div></div>
      </div>
    `;
  }

  const now = new Date();
  const daysLeft = Math.max(0, Math.ceil((deadline - now) / 86400000));
  const dateStr = deadline.toLocaleDateString("es-MX", { day: "numeric", month: "short" });
  const hasMark = p.current_price != null && p.current_return_pct != null;
  return `
    <div class="projection-box">
      <div class="projection-item"><div class="val">${fmtPrice(p.price_at_prediction)}${hasMark ? " &rarr; " + fmtPrice(p.current_price) : ""}</div><div class="lbl">Precio: análisis${hasMark ? " &rarr; ahora mismo" : " (aún sin revisar de nuevo)"}</div></div>
      ${hasMark ? `<div class="projection-item"><div class="val">${fmtPctColored(p.current_return_pct)}</div><div class="lbl">Desde el análisis (en vivo, no es el resultado final)</div></div>` : ""}
      <div class="projection-item"><div class="val">+20% a +30%</div><div class="lbl">Objetivo de búsqueda (no es promesa)</div></div>
      <div class="projection-item"><div class="val">${daysLeft > 0 ? daysLeft + "d" : "hoy"}</div><div class="lbl">Faltan para el día ${horizonDays} (${dateStr})</div></div>
    </div>
  `;
}

function statusPill(p) {
  if (p.status === "evaluated") {
    const label = p.thesis_result === "yes" ? "Sí llegó a +20&ndash;30%" : (p.thesis_result === "partial" ? "Se acercó, no llegó" : "No llegó a +20%");
    const cls = p.thesis_result === "yes" ? "pos" : p.thesis_result === "no" ? "neg" : "";
    return `<span class="pill-inline ${cls}">${label}</span>`;
  }
  if (p.status === "unevaluable") {
    return `<span class="pill-inline">Sin datos de precio</span>`;
  }
  if (p.current_return_pct != null) {
    const cls = p.current_return_pct > 0 ? "pos" : p.current_return_pct < 0 ? "neg" : "";
    return `<span class="pill-inline ${cls}">En vivo: ${fmtPct(p.current_return_pct)}</span>`;
  }
  return "";
}

function goplusSignalsLine(p) {
  // Fase 4: señales estructuradas de GoPlus (antes solo texto crudo para los analistas LLM).
  // Solo se muestran las que vinieron con dato -- GoPlus tiene cobertura imperfecta y varían
  // entre EVM y Solana (ver goplus.extract_security_features).
  const parts = [];
  if (p.lp_holders_locked_pct != null) parts.push(`liquidez/supply bloqueado ${p.lp_holders_locked_pct}%`);
  if (p.creator_percent != null) parts.push(`creador controla ${p.creator_percent}%`);
  if (p.owner_percent != null) parts.push(`dueño controla ${p.owner_percent}%`);
  if (p.is_mintable != null) parts.push(p.is_mintable ? "es mintable" : "no es mintable");
  if (p.is_honeypot != null) parts.push(p.is_honeypot ? "posible honeypot" : "no detectado como honeypot");
  if (!parts.length) return "";
  return `<p class="muted" style="margin:2px 0 0;">Señales de GoPlus: ${parts.join(" · ")}.</p>`;
}

function renderPredictions(preds) {
  const el = $("predictions");
  if (!preds.length) { el.innerHTML = '<div class="empty-state">Sin resultados todavía.</div>'; return; }
  el.innerHTML = preds.map(p => {
    const vclass = VERDICT_CLASS[p.verdict] || "v-insufficient";
    const evidence = Array.isArray(p.key_evidence) ? p.key_evidence : [];
    const risks = Array.isArray(p.main_risks) ? p.main_risks : [];
    return `
    <article class="card" tabindex="-1" id="card-${p.id}">
      <div class="card-head">
        <div><span class="sym">${p.symbol || "?"}</span><span class="name">${p.name || ""}</span><span class="pill-inline">${p.source === "dexscreener" ? "DexScreener" : "Binance Alpha"}</span></div>
        <div class="card-head-right">
          <span class="badge ${vclass}">${p.verdict || "N/A"}</span>
          ${statusPill(p)}
        </div>
      </div>
      ${p.project_explainer ? `<p class="project-explainer">${p.project_explainer}</p>` : ""}
      ${p.secondary_judge_verdict ? `<p class="muted" style="margin:2px 0 0;">Segunda opinión (Groq): <strong>${p.secondary_judge_verdict}</strong> ${p.judge_agreement ? "(coincide con Gemini)" : "(no coincide con Gemini)"}</p>` : ""}
      ${candidateProfile(p)}
      ${p.premortem_risk != null ? `<p class="muted" style="margin:2px 0 0;">Pre-mortem adversarial (Groq, en observación, no influye en el veredicto): riesgo de caída <strong>${p.premortem_risk}</strong>/100${Array.isArray(p.premortem_failure_modes) && p.premortem_failure_modes.length ? ". Causas señaladas: " + p.premortem_failure_modes.join("; ") : ""}.</p>` : ""}
      ${p.top10_holder_concentration_pct != null ? `<p class="muted" style="margin:2px 0 0;">Los top holders que reporta GoPlus controlan <strong>${p.top10_holder_concentration_pct}%</strong> del supply.</p>` : ""}
      ${goplusSignalsLine(p)}
      ${p.verdict_v2 ? `<p class="muted" style="margin:2px 0 0;">Veredicto por reglas explícitas (en observación, no reemplaza al del Juez): <strong>${p.verdict_v2}</strong>.</p>` : ""}
      ${dataGapsLine(p)}
      ${movementContextBlock(p)}
      ${projectionBox(p)}
      <div class="debate-grid">
        <div>
          <div class="section-title">Bull case</div>
          <div class="text-block">${p.bull_case || "-"}</div>
        </div>
        <div>
          <div class="section-title">Bear case</div>
          <div class="text-block">${p.bear_case || "-"}</div>
        </div>
      </div>
      ${p.mediator_contradictions_count ? `<p class="muted" style="margin:6px 0 0;">El Mediador encontró <strong>${p.mediator_contradictions_count}</strong> ${p.mediator_contradictions_count === 1 ? "contradicción" : "contradicciones"} entre Bull y Bear.</p>` : ""}
      ${evidence.length || risks.length ? `<div class="debate-grid">
        <div>${evidence.length ? `<div class="section-title">Evidencia clave</div><ul class="compact">${evidence.map(e=>`<li>${e}</li>`).join("")}</ul>` : ""}</div>
        <div>${risks.length ? `<div class="section-title">Riesgos principales</div><ul class="compact">${risks.map(r=>`<li>${r}</li>`).join("")}</ul>` : ""}</div>
      </div>` : ""}
      ${p.system_note ? `<div class="section-title">Nota del sistema</div><div class="text-block">${p.system_note}</div>` : ""}
      ${p.status === 'evaluated' ? `
        <div class="section-title">Resultado real en el horizonte de ${p.horizon_days} días</div>
        <div class="timeline-wrap">${candidateTimeline(p)}</div>
        <div class="timeline-caption">Retorno máximo: ${p.max_return_pct}% &middot; Caída máxima: ${p.max_drawdown_pct != null ? p.max_drawdown_pct + "%" : "sin dato"} &middot; Retorno al día ${p.horizon_days}: ${p.return_pct}% &middot; Tesis: ${p.thesis_result}</div>
      ` : `<div class="section-title">Estado</div><div class="text-block faint">Pendiente de evaluar (horizonte ${p.horizon_days} días)</div>`}
    </article>`;
  }).join("");
}

const SOURCE_LABEL = { market_klines: "klines de precio de Binance", security_goplus: "reporte de seguridad de GoPlus" };
function dataGapsLine(p) {
  // Plan v2, C1: aviso de texto cuando faltó una fuente critica en este analisis. Texto plano.
  const dq = p.data_quality;
  if (!dq || !Array.isArray(dq.critical_missing) || !dq.critical_missing.length) return "";
  const names = dq.critical_missing.map(k => SOURCE_LABEL[k] || k).join(" y ");
  const cap = p.confidence_score_capped != null && p.confidence_score_capped !== p.confidence_score
    ? ` Con esa fuente faltante el confidence queda limitado a ${p.confidence_score_capped}.` : "";
  return `<p class="muted" style="margin:6px 0 0;">Datos no disponibles en este análisis: <strong>${names}</strong>.${cap}</p>`;
}

function movementContextBlock(p) {
  // Plan v2, B3: contexto de movimiento calculado por código (sin LLM). Descriptivo, sin niveles de
  // precio ni objetivos; texto plano, sin color.
  const mc = p.movement_context;
  if (!mc || !Array.isArray(mc.text)) return "";
  return `<div class="section-title" style="margin-top:14px;">Contexto de movimiento (7 días previos)</div>
    ${mc.text.map(t => `<p class="muted" style="margin:2px 0;max-width:80ch;">${t}</p>`).join("")}
    <p class="muted" style="margin:4px 0 0;font-size:11px;max-width:80ch;">${mc.disclaimer || ""}</p>`;
}

const DISCARDED_STATUS_LABEL = { pending: "Pendiente", evaluated: "Evaluado", unevaluable: "Sin datos" };

function wasNearMiss(margins) {
  // Fase 0a: "casi pasó los filtros" -- todos los filtros que falló los falló por menos del
  // 20% (ratio_of_threshold >= 0.8). Texto plano, sin color nuevo (el color se reserva para
  // retornos con signo).
  if (!Array.isArray(margins) || !margins.length) return false;
  return margins.every(m => typeof m.ratio_of_threshold === "number" && m.ratio_of_threshold >= 0.8);
}

function renderDiscarded(rows, total) {
  const el = $("discardedTable");
  if (!rows.length) { el.innerHTML = '<div class="empty-state">Sin datos todavía.</div>'; return; }
  const totalNote = total > rows.length
    ? `<p class="muted" style="margin-bottom:10px;">Mostrando los ${rows.length} más recientes de <strong>${total.toLocaleString("es-MX")}</strong> descartados en total (la tabla no carga todos por rendimiento; las estadísticas de Auto-corrección sí usan el total real).</p>`
    : "";
  el.innerHTML = totalNote + `<div class="table-wrap"><table>
    <thead><tr><th>Símbolo</th><th>Razón de rechazo</th><th>Estado</th><th>Retorno máx. real</th><th>¿Se perdió la oportunidad?</th></tr></thead>
    <tbody>
      ${rows.map(r => {
        const reasons = Array.isArray(r.rejection_reasons) ? r.rejection_reasons : [];
        const margins = Array.isArray(r.rejection_margins) ? r.rejection_margins : [];
        const missed = r.status === 'evaluated' && (r.max_return_pct ?? 0) >= 20;
        return `<tr>
          <td><strong>${r.symbol}</strong></td>
          <td>${reasons.map(x => `<span class="pill-inline">${x}</span>`).join("")}${wasNearMiss(margins) ? '<span class="pill-inline">Casi pasó los filtros</span>' : ""}</td>
          <td>${DISCARDED_STATUS_LABEL[r.status] || r.status}</td>
          <td>${r.status === 'evaluated' ? r.max_return_pct + '%' : '-'}</td>
          <td>${r.status === 'evaluated' ? (missed ? 'Sí' : 'No') : '-'}</td>
        </tr>`;
      }).join("")}
    </tbody>
  </table></div>`;
}

const VERDICT_ORDER = ["Strong Opportunity", "Watchlist", "High Risk / Speculative", "Insufficient Evidence", "Reject"];
const STATUS_OPTIONS = [
  ["active", "Activo ahora"],
  ["evaluated", "Cerrado"],
  ["unevaluable", "Sin datos"],
];

let allPredictions = [];
let verdictFilter = null;  // null = todos
let statusFilter = "active";   // por defecto solo lo vigente -- "Todos" mezcla 13 días de historial cerrado

function isActive(p) {
  if (p.status !== "pending") return false;
  const deadline = new Date(p.created_at).getTime() + (p.horizon_days || 7) * 86400000;
  return deadline >= Date.now();
}

function matchesStatus(p, sf) {
  if (!sf) return true;
  if (sf === "active") return isActive(p);
  return p.status === sf;
}

function applyFilters(list) {
  return list.filter(p => (!verdictFilter || p.verdict === verdictFilter) && matchesStatus(p, statusFilter));
}

function renderFiltered() {
  const filtered = applyFilters(allPredictions);
  renderRanking(filtered);
  renderScoreComparison(filtered);
  renderPredictions(filtered);
}

function pillBtn(label, active, dataAttr) {
  return `<button class="btn secondary small" aria-pressed="${active}" data-v="${dataAttr}" style="${active ? 'background:var(--black);color:#fff;border-color:var(--black);' : ''}">${label}</button>`;
}

function renderStatusFilters() {
  const base = allPredictions.filter(p => !verdictFilter || p.verdict === verdictFilter);
  const counts = { active: 0, evaluated: 0, unevaluable: 0 };
  for (const p of base) {
    if (isActive(p)) counts.active++;
    else if (p.status === "evaluated") counts.evaluated++;
    else if (p.status === "unevaluable") counts.unevaluable++;
  }
  const pills = [pillBtn(`Todos (${base.length})`, statusFilter === null, "")]
    .concat(STATUS_OPTIONS.map(([key, label]) => pillBtn(`${label} (${counts[key]})`, statusFilter === key, key)));
  $("statusFilters").innerHTML = pills.join("");
  $("statusFilters").querySelectorAll("button").forEach(btn => {
    btn.onclick = () => {
      statusFilter = btn.dataset.v || null;
      renderStatusFilters();
      renderVerdictFilters();
      renderFiltered();
    };
  });
}

function renderVerdictFilters() {
  const base = allPredictions.filter(p => matchesStatus(p, statusFilter));
  const counts = {};
  for (const p of base) counts[p.verdict] = (counts[p.verdict] || 0) + 1;
  const verdicts = VERDICT_ORDER.filter(v => counts[v]);
  const pills = [pillBtn(`Todos (${base.length})`, verdictFilter === null, "")]
    .concat(verdicts.map(v => pillBtn(`${v} (${counts[v]})`, verdictFilter === v, v)));
  $("verdictFilters").innerHTML = pills.join("");
  $("verdictFilters").querySelectorAll("button").forEach(btn => {
    btn.onclick = () => {
      verdictFilter = btn.dataset.v || null;
      renderStatusFilters();
      renderVerdictFilters();
      renderFiltered();
    };
  });
}

const RANK_SORT_OPTIONS = [
  ["opportunity_score", "Opportunity"],
  ["risk_score", "Risk"],
  ["confidence_score", "Confidence"],
  ["earliness_score", "Earliness"],
];
let rankSort = { key: "opportunity_score", dir: "desc" };

function renderRanking(list) {
  const box = $("rankingTable");
  if (!list.length) { box.innerHTML = '<div class="empty-state">Nada que rankear con este filtro.</div>'; return; }
  const sorted = [...list].sort((a, b) => {
    const av = a[rankSort.key] ?? -1, bv = b[rankSort.key] ?? -1;
    return rankSort.dir === "desc" ? bv - av : av - bv;
  });
  const headerCell = (key, label) => {
    const isActive = rankSort.key === key;
    const arrow = isActive ? (rankSort.dir === "desc" ? " ↓" : " ↑") : "";
    return `<th data-sort="${key}" aria-sort="${isActive ? (rankSort.dir === "desc" ? "descending" : "ascending") : "none"}"><button class="sort-btn">${label}${arrow}</button></th>`;
  };
  box.innerHTML = `<div class="table-wrap" tabindex="0" aria-label="Ranking de candidatos"><table class="ranking-table">
    <thead><tr>
      <th>#</th><th>Símbolo</th><th>Fuente</th><th>Veredicto</th>
      ${RANK_SORT_OPTIONS.map(([k, l]) => headerCell(k, l)).join("")}
      <th>Estado</th><th>Resultado (real o en vivo)</th>
    </tr></thead>
    <tbody>
      ${sorted.map((p, i) => `
        <tr data-goto="card-${p.id}">
          <td class="faint">${i + 1}</td>
          <td><button class="token-link" aria-label="Ver análisis de ${p.symbol}">${p.symbol || "?"}</button></td>
          <td class="muted">${p.source === "dexscreener" ? "DexScreener" : "Binance Alpha"}</td>
          <td><span class="badge ${VERDICT_CLASS[p.verdict] || "v-insufficient"}">${p.verdict || "N/A"}</span></td>
          <td>${p.opportunity_score ?? "-"}</td>
          <td>${p.risk_score ?? "-"}</td>
          <td>${p.confidence_score ?? "-"}</td>
          <td>${p.earliness_score ?? "-"}</td>
          <td>${p.status === "evaluated" ? (p.thesis_result === "yes" ? "Sí llegó" : p.thesis_result === "partial" ? "Se acercó" : "No llegó") : p.status === "unevaluable" ? "Sin datos" : "Activo"}</td>
          <td>${p.status === "evaluated" ? fmtPctColored(p.return_pct) : p.current_return_pct != null ? fmtPctColored(p.current_return_pct) : "-"}</td>
        </tr>`).join("")}
    </tbody>
  </table></div>`;
  box.querySelectorAll("th[data-sort]").forEach(th => {
    th.onclick = () => {
      const key = th.dataset.sort;
      rankSort = rankSort.key === key ? { key, dir: rankSort.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" };
      renderRanking(list);
    };
  });
  box.querySelectorAll("tr[data-goto]").forEach(tr => {
    tr.style.cursor = "pointer";
    tr.onclick = () => {
      goToView("oportunidades");
      const target = document.getElementById(tr.dataset.goto);
      if (target) focusCandidate(target);
    };
  });
}

async function refreshPredictions() {
  const preds = await api("/api/predictions?category=analyzed&compact=1&limit=200");
  allPredictions = preds;
  renderOutcomeChart(preds);
  renderStatusFilters();
  renderVerdictFilters();
  renderFiltered();
  return preds;
}

const DISCARDED_TABLE_LIMIT = 300;

async function refreshDiscarded() {
  const [rows, countResp] = await Promise.all([
    api(`/api/predictions?category=discarded&compact=1&limit=${DISCARDED_TABLE_LIMIT}`),
    api("/api/predictions/count?category=discarded").catch(() => null),
  ]);
  renderDiscarded(rows, countResp ? countResp.count : rows.length);
  return { rows, total: countResp ? countResp.count : rows.length };
}

function numItem(num, lbl) {
  return `<div class="num-item"><div class="num">${num}</div><div class="lbl">${lbl}</div></div>`;
}

function relativeTime(iso) {
  if (!iso) return "-";
  const diffMs = Date.now() - new Date(iso).getTime();
  const min = Math.round(diffMs / 60000);
  if (min < 1) return "hace instantes";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 48) return `hace ${h}h`;
  return `hace ${Math.round(h / 24)}d`;
}


async function refreshSummary() {
 const [analyzed,discarded,runs] = await Promise.all([refreshPredictions(),api("/api/predictions/count?category=discarded"),api("/api/runs")]);
 drawOverview(analyzed,discarded.count,runs[0]);
 renderOpportunityMap(analyzed.filter(isActive));
}
$("btnUpdate").onclick = async () => {
 const button=$("btnUpdate");
 button.disabled=true;
 try {
   await Promise.all([refreshDiscarded(),refreshSummary()]);
   $("runStatus").textContent="Datos actualizados.";
 } catch(error){$("runStatus").textContent=error.message;} finally {button.disabled=false;}
};

function renderStats(s) {
  if (!s || !s.evaluated_predictions) {
    $("statsBox").innerHTML = `<p class="muted">${s && s.message ? s.message : "Aún no hay predicciones evaluadas."}</p>`;
    return;
  }
  $("statsBox").innerHTML = `
    <p class="muted">${s.evaluated_predictions} de ${s.total_predictions} predicciones evaluadas hasta ahora, agrupadas por qué tan seguro estaba el Juez:</p>
    <ul class="compact">${Object.entries(s.accuracy_by_confidence_bucket).map(([b,v]) => `<li>Confidence ${b}: ${v.success_rate_pct}% de éxito (n=${v.n})</li>`).join("")}</ul>
  `;
}

function renderEdge(snap) {
  const box = $("edgeBox");
  const e = snap && snap.payload;
  if (!e || !e.labels) {
    box.innerHTML = '<div class="empty-state">Se calcula al final de cada actualización.</div>';
    return;
  }
  const pc = x => x == null ? "-" : (x * 100).toFixed(1) + "%";
  const pp = x => x == null ? "-" : (x > 0 ? "+" : "") + (x * 100).toFixed(1) + " pp";
  const labelRows = Object.values(e.labels).map(l => `
    <tr><td>${l.title}</td><td>${pc(l.analyzed_rate)}</td><td>${pc(l.control_rate)}</td>
    <td>${pp(l.matched_edge)} <span class="muted">(${pp(l.matched_ci95[0])} a ${pp(l.matched_ci95[1])})</span></td>
    <td>${l.distinguishable_from_zero ? "Sí" : "No"}</td></tr>`).join("");
  const weekRows = Object.entries(e.weekly || {}).map(([w, v]) => `
    <tr><td>${w}</td><td>${pc(v.control_rate)} <span class="muted">(n=${v.control_n})</span></td>
    <td>${v.analyzed_n ? pc(v.analyzed_rate) + ' <span class="muted">(n=' + v.analyzed_n + ')</span>' : "-"}</td></tr>`).join("");
  const regimeRows = Object.entries(e.by_regime || {}).map(([t, v]) => `
    <tr><td>BTC en ${t}</td><td>${pc(v.control_rate)} <span class="muted">(n=${v.control_n})</span></td>
    <td>${v.analyzed_n ? pc(v.analyzed_rate) + ' <span class="muted">(n=' + v.analyzed_n + ')</span>' : "-"}</td></tr>`).join("");
  const bl = (e.baselines_auc || {}).touch20 || {};
  const baseRows = Object.entries(bl).map(([n, v]) => `
    <tr><td>${n}</td><td>${v.auc ?? "-"} <span class="muted">(${v.ci95[0] ?? "-"} a ${v.ci95[1] ?? "-"})</span></td></tr>`).join("");
  const pw = e.power;
  const sc = e.score_context || {};
  const rateCell = o => o && o.rate != null
    ? `~${Math.round(o.rate * 100)}${o.ci95 && o.ci95[0] != null ? ` <span class="muted">(${Math.round(o.ci95[0] * 100)} a ${Math.round(o.ci95[1] * 100)})</span>` : ""}`
    : "-";
  const freqRows = grp => Object.entries(grp || {}).map(([k, v]) => `
    <tr><td>${k}</td><td>${v.n_rows} <span class="muted">(${v.n_tokens} tokens)</span>${v.insufficient ? ' <span class="pill-inline">muestra insuficiente</span>' : ""}</td>
    <td>${rateCell(v.touch20)}</td><td>${rateCell(v.sustained10)}</td><td>${rateCell(v.drop20)}</td></tr>`).join("");
  const freqTable = (title, grp) => Object.keys(grp || {}).length ? `
    <div class="section-title" style="margin-top:22px;">${title}</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Grupo</th><th>Observaciones</th><th>Tocaron +20% (de cada 100)</th><th>Cerraron en +10% o más</th><th>Cayeron 20% o más</th></tr></thead>
      <tbody>${freqRows(grp)}</tbody>
    </table></div>` : "";
  const calRaw = ((sc.calibration || {}).touch20 || {})["confidence_score (crudo)"] || {};
  const brier = calRaw.brier;
  const relRows = (calRaw.reliability || []).map(b => `
    <tr><td>${b.bin}</td><td>${b.n}</td><td>${(b.mean_predicted * 100).toFixed(0)}%</td><td>${(b.observed_rate * 100).toFixed(0)}%</td></tr>`).join("");
  const calHtml = brier ? `
    <div class="section-title" style="margin-top:22px;">Calibración del confidence_score (leído como probabilidad de tocar +20%)</div>
    <p class="muted" style="max-width:70ch;">Brier ${brier.brier} frente a ${brier.brier_reference_constant} de simplemente predecir siempre la tasa base
      (habilidad ${brier.skill}; menor a 0 significa que, leído como probabilidad, rinde peor que no tener modelo). n=${brier.n}.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Confidence (tramo)</th><th>N</th><th>Probabilidad que implica</th><th>Frecuencia real</th></tr></thead>
      <tbody>${relRows}</tbody>
    </table></div>` : "";
  const pm = sc.premortem;
  const pmHtml = pm ? `<p class="muted" style="margin-top:6px;max-width:70ch;">Pre-mortem de Groq vs. resultado: ${pm.note ? pm.note : `AUC ${pm.auc_vs_drop20} contra caída de 20% o más y ${pm.auc_vs_touch20} contra tocar +20%, n=${pm.n} (${pm.n_tokens} tokens)`}.</p>` : "";
  const dc = sc.data_completeness;
  const dcHtml = dc ? `<p class="muted" style="margin-top:10px;max-width:70ch;">Completitud de datos vs. resultado: ${dc.note ? dc.note : `AUC ${dc.auc_vs_touch20} (tocó +20%) y ${dc.auc_vs_sustained10} (cerró en +10% o más), n=${dc.n}`}.</p>` : "";
  box.innerHTML = `
    <div class="numbers-row">
      ${numItem(e.matched.analyzed_tokens, "Tokens analizados comparables")}
      ${numItem(e.matched.runs, "Ciclos con al menos " + e.min_controls_per_run + " controles")}
      ${numItem(e.control.rows, "Filas de control evaluadas")}
    </div>
    <div class="section-title" style="margin-top:22px;">Analizados frente al control del mismo ciclo</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Forma de medir</th><th>Analizados</th><th>Control</th><th>Diferencia emparejada (IC 95%)</th><th>Distinguible de cero</th></tr></thead>
      <tbody>${labelRows}</tbody>
    </table></div>
    <p class="muted" style="margin-top:6px;max-width:70ch;">Los intervalos se calculan remuestreando tokens completos, no filas: varias filas de un mismo token no son observaciones independientes. Un intervalo que incluye 0 significa que todavía no se puede afirmar diferencia, ni a favor ni en contra.</p>
    ${pw ? `<p class="muted" style="max-width:70ch;">Para detectar una diferencia de 5 puntos harían falta unas ${pw.independent_predictions_needed["5pp"]} predicciones independientes; para 10 puntos, unas ${pw.independent_predictions_needed["10pp"]}. Hoy hay ${pw.tokens_available_now} tokens distintos.</p>` : ''}
    <div class="section-title" style="margin-top:22px;">El mercado cambia el resultado (etiqueta: tocó +20%)</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Semana</th><th>Control</th><th>Analizados</th></tr></thead>
      <tbody>${weekRows}</tbody>
    </table></div>
    ${regimeRows ? `<div class="section-title" style="margin-top:22px;">Por régimen de Bitcoin al momento de la predicción</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Régimen</th><th>Control</th><th>Analizados</th></tr></thead>
      <tbody>${regimeRows}</tbody>
    </table></div>` : ''}
    ${baseRows ? `<div class="section-title" style="margin-top:22px;">¿El Juez supera a una señal gratuita? (AUC, 0.5 = azar)</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Señal</th><th>AUC (IC 95%)</th></tr></thead>
      <tbody>${baseRows}</tbody>
    </table></div>` : ''}
    ${freqTable("Qué pasó históricamente según el nivel de confianza del Juez", sc.by_confidence)}
    ${freqTable("Qué pasó históricamente según el veredicto", sc.by_verdict)}
    ${sc.by_verdict_v2 && Object.keys(sc.by_verdict_v2).length ? freqTable("Qué pasó históricamente según el veredicto por reglas explícitas (en observación)", sc.by_verdict_v2) : ""}
    ${sc.verdict_v2_agreement && sc.verdict_v2_agreement.n ? `<p class="muted" style="margin-top:6px;">Coincide con el veredicto del Juez en el ${Math.round(sc.verdict_v2_agreement.agree_rate * 100)}% de los casos (n=${sc.verdict_v2_agreement.n}). Para reemplazar al Juez se exigen al menos 60 días en observación y 150 tokens distintos.</p>` : ""}
    ${calHtml}
    ${dcHtml}
    ${pmHtml}
    <p class="muted" style="margin-top:8px;">Calculado ${new Date(e.computed_at).toLocaleString()}. Acciones tokenizadas excluidas.</p>
  `;
}

async function loadEdge() {
  try { renderEdge(await api("/api/analytics/edge_report")); }
  catch (_) { renderEdge(null); }
}

function renderEfficacy(e) {
  const box = $("efficacyBox");
  if (!e || (!e.analyzed.n && !e.discarded.n)) {
    box.innerHTML = '<div class="empty-state">Aún no hay suficientes evaluaciones.</div>';
    return;
  }
  const missed = Object.entries(e.missed_opportunities_reasons || {});
  box.innerHTML = `
    <div class="numbers-row">
      ${numItem((e.analyzed.pct_reached_plus20 ?? '-') + '%', `Analizados que llegaron a +20% (n=${e.analyzed.n})`)}
      ${numItem((e.discarded.pct_reached_plus20 ?? '-') + '%', `Descartados que igual llegaron a +20% (n=${e.discarded.n})`)}
    </div>
    ${missed.length ? `<div class="section-title" style="margin-top:18px;">Razones de rechazo que dejaron pasar oportunidades reales</div>
      <ul class="compact">${missed.map(([r,c]) => `<li>${r} &mdash; ${c} caso(s)</li>`).join("")}</ul>` : ''}
  `;
}

function renderHistory(h) {
  const box = $("historyBox");
  if (!h || !h.total_evaluated) {
    box.innerHTML = `<div class="empty-state">${h && h.message ? h.message : "Sin datos todavía."}</div>`;
    return;
  }

  const verdictRows = Object.entries(h.accuracy_by_verdict).map(([v, s]) => `
    <tr>
      <td>${v}</td><td>${s.n}</td><td>${s.success_rate_pct}%</td>
      <td>${fmtPctColored(s.avg_max_return_pct)}</td><td>${fmtPctColored(s.avg_return_at_horizon_pct)}</td>
    </tr>`).join("");

  box.innerHTML = `
    ${h.low_sample_warning ? `<p class="disclaimer" style="margin-bottom:22px;">Muestra pequeña (${h.total_evaluated} evaluadas, mínimo recomendado ${h.min_sample_for_confidence}): trata estas cifras con cautela todavía, no como una precisión confirmada.</p>` : ''}
    <div class="numbers-row">
      ${numItem(h.total_updates_run, "Actualizaciones corridas")}
      ${numItem(h.total_analyzed, "Análisis registrados")}
      ${numItem(h.overall_hit_rate_pct + "%", "Cumplimiento histórico de la meta a 7 días")}
      ${numItem(h.avg_time_to_target_hours != null ? Math.round(h.avg_time_to_target_hours / 24 * 10) / 10 + "d" : "-", `Tiempo promedio a la meta cuando SÍ llegó (n=${h.hit_count_for_timing})`)}
    </div>
    <p class="muted" style="margin-top:2px;">Referencia: contando también los que no llegaron, el pico (llegue o no a la meta) tomó en promedio ${h.avg_time_to_max_hours}h.</p>

    <div class="section-title" style="margin-top:26px;">Precisión por tipo de veredicto</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Veredicto</th><th>N</th><th>% Éxito</th><th>Retorno máx. prom.</th><th>Retorno a los 7 días prom.</th></tr></thead>
        <tbody>${verdictRows}</tbody>
      </table>
    </div>

  `;
}

async function loadDiagnosis() {
  try {
    const diag = await api("/api/diagnoses/latest");
    const patterns = Array.isArray(diag.patterns_found) ? diag.patterns_found : [];
    const adj = Array.isArray(diag.proposed_adjustments) ? diag.proposed_adjustments : [];
    $("diagnosisBox").innerHTML = `
      <p class="muted">Basado en ${diag.based_on_evaluated} evaluaciones &middot; ${new Date(diag.created_at).toLocaleString()} &middot; estado: ${diag.status}</p>
      <div class="text-block">${diag.summary || ""}</div>
      ${patterns.length ? `<div class="section-title">Patrones detectados</div><ul class="compact">${patterns.map(p=>`<li>${p}</li>`).join("")}</ul>` : ""}
      ${adj.length ? `
        <div class="section-title">Ajustes propuestos</div>
        <ul class="compact">${adj.map(a => `<li><strong>${a.parameter}</strong>: ${a.current_value} &rarr; ${a.proposed_value}. ${a.reason}</li>`).join("")}</ul>
        ${diag.status === 'proposed' ? `<div class="row" style="margin-top:12px;"><button class="btn small" id="btnApplyDiag">Aplicar ajustes propuestos</button></div>` : ''}
        ${diag.status === 'applied' ? `
          <div class="row" style="margin-top:12px;"><button class="btn small" id="btnRollbackDiag">Deshacer</button></div>
          <p class="muted" style="margin-top:6px;">Deshacer restaura el valor que el sistema tenía registrado al momento de este diagnóstico -- si otro ajuste tocó el mismo parámetro después, podría no ser el valor más reciente.</p>
        ` : ''}
        ${diag.status === 'rolled_back' ? `<p class="muted" style="margin-top:8px;">Este ajuste fue revertido.</p>` : ''}
      ` : ""}
    `;
    const applyBtn = document.getElementById("btnApplyDiag");
    if (applyBtn) {
      applyBtn.onclick = async () => {
        await api(`/api/diagnoses/${diag.id}/apply`, {
          method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({})
        });
        loadDiagnosis();
        loadConfig();
      };
    }
    const rollbackBtn = document.getElementById("btnRollbackDiag");
    if (rollbackBtn) {
      rollbackBtn.onclick = async () => {
        const r = await api(`/api/diagnoses/${diag.id}/rollback`, { method: "POST" });
        if (r.failed && r.failed.length) {
          alert("Se revirtieron " + r.rolled_back.length + " parámetro(s). No se pudieron revertir: "
            + r.failed.map(f => `${f.parameter} (${f.reason})`).join(", "));
        }
        loadDiagnosis();
        loadConfig();
      };
    }
  } catch (e) {
    $("diagnosisBox").innerHTML = '<div class="empty-state">Aún no hay diagnósticos.</div>';
  }
}

$("btnStats").onclick = async () => {
  renderStats(await api("/api/backtest/stats"));
  renderEfficacy(await api("/api/backtest/filter-efficacy"));
};

async function loadConfig() {
  const cfg = await api("/api/config");
  $("cfgMinLiquidity").value = cfg.MIN_LIQUIDITY_USD;
  $("cfgMinVolume").value = cfg.MIN_VOLUME_24H_USD;
  $("cfgMaxMcap").value = cfg.MAX_MARKET_CAP_USD;
  $("cfgMinHolders").value = cfg.MIN_HOLDERS;
  $("cfgMaxAge").value = cfg.MAX_LISTING_AGE_DAYS;
  $("cfgMaxCandidates").value = cfg.MAX_CANDIDATES_PER_RUN;
  $("cfgMinConfidence").value = cfg.MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY;
  $("cfgMlMode").value = cfg.ML_SCORING_MODE || "shadow";
  $("cfgPriceMode").value = cfg.PRICE_MODEL_MODE || "shadow";
  $("cfgCapMode").value = cfg.CONFIDENCE_CAP_MODE || "shadow";
  $("cfgEnsembleMode").value = cfg.ENSEMBLE_JUDGE_MODE || "shadow";
  const verdictValues = cfg.VERDICT_VALUES || ["Strong Opportunity", "Watchlist", "High Risk / Speculative", "Reject", "Insufficient Evidence"];
  const notifyVerdicts = new Set(cfg.TELEGRAM_NOTIFY_VERDICTS || ["Strong Opportunity"]);
  $("cfgTelegramVerdicts").innerHTML = verdictValues.map((v, i) => `
    <label class="cfg" style="flex-direction:row;align-items:center;gap:8px;">
      <input type="checkbox" class="cfgTelegramVerdict" value="${v}" id="cfgTelegramVerdict${i}" ${notifyVerdicts.has(v) ? "checked" : ""}>
      ${v}
    </label>
  `).join("");
  $("modelInfo").innerHTML = `
    <tr><td>Modelo analistas</td><td>${cfg.GEMINI_MODEL_FAST}</td></tr>
    <tr><td>Modelo debate/juez</td><td>${cfg.GEMINI_MODEL_SMART}</td></tr>
    <tr><td>Búsqueda web (grounding)</td><td>${cfg.GEMINI_ENABLE_SEARCH_GROUNDING ? "activada" : "desactivada (requiere facturación en Google Cloud)"}</td></tr>
    <tr><td>Actualización automática</td><td>cron de GitHub Actions cada 12h (ver .github/workflows/update-cycle.yml)</td></tr>
    <tr><td>API key de Gemini / Telegram</td><td>configuradas como secretos en GitHub Actions, no visibles desde aquí</td></tr>
    <tr><td>Bloqueo de login</td><td>${cfg.LOGIN_MAX_ATTEMPTS} intentos fallidos &rarr; ${cfg.LOGIN_LOCKOUT_MINUTES} min de bloqueo</td></tr>
  `;
  loadMlBenchmark();
  loadPriceModels();
}

function mlMetricRow(label, ml, heuristic) {
  return `<tr><td>${label}</td><td>${ml}</td><td>${heuristic}</td></tr>`;
}

async function loadPriceModels() {
  const box = $("priceModelsBox");
  const labels = {
    touch20: "Tocó +20% en algún momento (etiqueta histórica)",
    sustained10: "Cerró el horizonte en +10% o más (etiqueta primaria)",
    drop20: "Cayó 20% o más en algún momento (riesgo)",
  };
  const rows = [];
  let pre = null, trained = null, nSamples = null;
  for (const k of ["touch20", "sustained10", "drop20"]) {
    let m;
    try { m = await api("/api/ml-models/price_" + k); } catch (e) { continue; }
    const g = (m.metrics || {}).grouped_validation || {};
    const o = (m.metrics || {}).out_of_time || {};
    trained = m.trained_at; nSamples = m.n_samples;
    if (k === "sustained10") pre = (m.metrics || {}).preregistered_activation;
    const f = x => x == null ? "-" : x;
    rows.push(`<tr><td>${labels[k]}</td><td>${f(g.n_tokens)}</td><td>${f(g.auc_model)}</td>
      <td>${f(g.auc_baseline_vol_mcap)}</td><td>${f(g.dauc_floor_mean)} <span class="muted">(${f((g.dauc_floor_ci95 || [])[0])} a ${f((g.dauc_floor_ci95 || [])[1])})</span></td>
      <td>${f(o.dauc_floor)}</td></tr>`);
  }
  if (!rows.length) {
    box.innerHTML = '<div class="empty-state">Aún no hay modelos de precio entrenados (falta el backfill de features o más ciclos).</div>';
    return;
  }
  const checks = pre ? Object.entries(pre.checks).map(([c, ok]) => `<li>${c}: <strong>${ok ? "cumple" : "no cumple"}</strong></li>`).join("") : "";
  box.innerHTML = `
    <p class="muted">Último entrenamiento: ${new Date(trained).toLocaleString()} &middot; ${nSamples} filas.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Modelo</th><th>Tokens</th><th>AUC modelo</th><th>AUC solo vol/mcap</th><th>Mejora sobre max(base, 0.5) (IC 95%)</th><th>Fuera de tiempo</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table></div>
    <p class="muted" style="margin-top:6px;max-width:70ch;">AUC 0.5 = azar. Validación en particiones agrupadas por token (ningún token está a la vez en entrenamiento y prueba).</p>
    ${pre ? `<div class="section-title" style="margin-top:18px;">Criterio de activación prerregistrado (etiqueta primaria): ${pre.meets_criteria ? "se cumple" : "NO se cumple todavía"}</div>
    <ul class="compact">${checks}</ul>` : ""}
  `;
}

async function loadMlBenchmark() {
  const box = $("mlBenchmarkBox");
  let model;
  try {
    model = await api("/api/ml-models/selector");
  } catch (e) {
    box.innerHTML = '<div class="empty-state">Aún no hay ningún modelo entrenado (hace falta más historial evaluado).</div>';
    return;
  }
  const m = model.metrics || {};
  const bench = m.benchmark_vs_heuristic;
  let benchHtml = '<p class="muted">Todavía no hay benchmark contra la heurística (conjunto de prueba insuficiente en el último reentrenamiento).</p>';
  if (bench && bench.ml && bench.heuristic) {
    benchHtml = `
      <div class="table-wrap">
        <table>
          <thead><tr><th>Métrica (sobre el mismo conjunto de prueba, fuera de muestra)</th><th>Clasificador ML</th><th>Ranking heurístico actual</th></tr></thead>
          <tbody>
            ${mlMetricRow("PR-AUC", bench.ml.pr_auc, bench.heuristic.pr_auc)}
            ${mlMetricRow(`Precisión (top ${bench.ml.candidates_investigated})`, bench.ml.precision_top_n, bench.heuristic.precision_top_n)}
            ${mlMetricRow("Recall", bench.ml.recall_top_n, bench.heuristic.recall_top_n)}
            ${mlMetricRow("Oportunidades reales encontradas", bench.ml.strong_opportunities_found, bench.heuristic.strong_opportunities_found)}
            ${mlMetricRow("Falsos negativos (se habrían perdido)", bench.ml.false_negatives, bench.heuristic.false_negatives)}
          </tbody>
        </table>
      </div>
      <p class="muted" style="margin-top:8px;">Comparado sobre ${bench.n_eval} casos evaluados que el modelo NO usó para entrenar (${bench.positives_in_eval} de ellos sí fueron éxito real).</p>
    `;
  }
  box.innerHTML = `
    <p class="muted">Último modelo entrenado: ${new Date(model.trained_at).toLocaleString()} &middot; ${model.n_samples} casos evaluados usados en total (entrenamiento + validación).</p>
    ${m.warning ? `<p class="disclaimer">${m.warning}</p>` : ""}
    ${benchHtml}
  `;
}

$("btnSaveConfig").onclick = async () => {
  const body = {
    MIN_LIQUIDITY_USD: parseFloat($("cfgMinLiquidity").value),
    MIN_VOLUME_24H_USD: parseFloat($("cfgMinVolume").value),
    MAX_MARKET_CAP_USD: parseFloat($("cfgMaxMcap").value),
    MIN_HOLDERS: parseInt($("cfgMinHolders").value),
    MAX_LISTING_AGE_DAYS: parseInt($("cfgMaxAge").value),
    MAX_CANDIDATES_PER_RUN: parseInt($("cfgMaxCandidates").value),
    MIN_CONFIDENCE_FOR_STRONG_OPPORTUNITY: parseInt($("cfgMinConfidence").value),
    ML_SCORING_MODE: $("cfgMlMode").value,
    PRICE_MODEL_MODE: $("cfgPriceMode").value,
    CONFIDENCE_CAP_MODE: $("cfgCapMode").value,
    ENSEMBLE_JUDGE_MODE: $("cfgEnsembleMode").value,
    TELEGRAM_NOTIFY_VERDICTS: Array.from(document.querySelectorAll(".cfgTelegramVerdict:checked")).map(el => el.value),
  };
  try {
    const saved=await api("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const omitted=Object.keys(body).filter(k=>!(k in (saved.updated || {})));
    $("configStatus").textContent=omitted.length ? "Revisa los campos que CIS no aceptó: "+omitted.join(", ") : "Configuración guardada en CIS.";
    await loadConfig();
  } catch(error){$("configStatus").textContent=error.message; return;}
  setTimeout(() => $("configStatus").textContent = "", 4000);
};

const visited=new Map();
function once(key, action) {
  if(!visited.has(key)) visited.set(key, Promise.resolve().then(action).catch(error=>{
    visited.delete(key);
    const area=document.querySelector("#view-"+(location.hash.slice(1)||"resumen"));
    if(area){let note=area.querySelector(".load-error");if(!note){note=document.createElement("p");note.className="load-error";note.setAttribute("role","status");area.prepend(note);}note.textContent="No se pudo completar la consulta. Vuelve a abrir la sección para reintentar.";}
    console.warn("Consulta de sección incompleta",key);
  }));
}
function loadView(key) {
  if(["resumen","ranking","oportunidades","historial"].includes(key)) once("summary",refreshSummary);
  if(key==="descartados") once(key,refreshDiscarded);
  if(key==="historial") once(key,async()=>{await Promise.all([loadCohorts(),api("/api/history/overview").then(renderHistory)]);});
  if(key==="autocorreccion") once(key,async()=>{await Promise.all([api("/api/backtest/stats").then(renderStats),api("/api/backtest/filter-efficacy").then(renderEfficacy),loadEdge(),loadDiagnosis()]);});
  if(key==="configuracion") once(key,loadConfig);
  if(key==="metodologia") $("pipelineDiagram").innerHTML=renderPipelineDiagram();
}
window.addEventListener("cis:view",e=>loadView(e.detail));
loadView(location.hash.slice(1)||"resumen");
async function loadCohorts(){
  const box=$("shadowCohorts");
  try {
    const snap=await api("/api/analytics/shadow_cohorts");
    function draw(){
      const horizon=$("cohortDays").value;
      const rows=[...(snap.payload[horizon] || []), ...(snap.payload.universo_elegible?.[horizon] || [])];
      const coverage=snap.payload.cobertura;
      const study=snap.payload.estudio_volumen;
      const states={en_observacion:"en observación",evidencia_insuficiente:"evidencia insuficiente",solo_descriptiva:"solo descriptiva",candidata_a_revision:"candidata a revisión humana"};
      const studyLine=study ? '<p class="desc">Volumen frente a liquidez y pérdida al cierre de 7 días · '+(states[study.estado] || "pendiente")+'. '+Number(study.medidos)+' contratos con cierre y '+Number(study.sin_cierre)+' sin cierre. Evaluación final el 31 de enero de 2027 UTC.'+(Number.isFinite(study.rr) ? ' Riesgo relativo '+study.rr.toFixed(2)+'×.' : '')+(Array.isArray(study.ic95_rr) ? ' IC 95% '+study.ic95_rr.map(n=>Number(n).toFixed(2)).join(' a ')+'.' : '')+(Number.isFinite(study.auc) ? ' AUC '+study.auc.toFixed(2)+'.' : '')+'</p>' : '';
      const status=studyLine+(coverage ? '<p class="desc">Cobertura operativa · '+Number(coverage.dias_consecutivos)+' de 14 días UTC completos con dos capturas programadas, fuentes disponibles y cupos cubiertos en ambas cadenas.</p>' : '');
      const labels={ranking:"Ranking",sorteo:"Sorteo entre los restantes",alternativa:"Alternativas",todos_los_elegibles:"Total elegible (referencia)"};
      if(!rows.length){box.innerHTML=status+'<p>Aún no hay contratos elegibles.</p>';return;}
      box.innerHTML=status+'<p class="desc">Primera observación elegible por contrato. Retorno neto estimado para 100 USD, comisión de 0.3% por lado e impacto del pool. Referencia BTC/USDT.</p><div class="table-wrap"><table><thead><tr><th>Cadena</th><th>Grupo</th><th>Versión</th><th>Contratos</th><th>Pendientes</th><th>Sin precio</th><th>Neto mediano</th><th>Exceso vs. BTC</th></tr></thead><tbody>'+rows.map(r=>'<tr><td>'+r.cadena+'</td><td>'+(labels[r.grupo] || r.grupo)+'</td><td>'+r.version.slice(0,8)+(r.version.includes('-public-') ? '…'+r.version.slice(-5) : '')+'</td><td>'+r.contratos+'</td><td>'+r.inmaduros+'</td><td>'+r.sin_precio+'</td><td>'+(r.mediana_neta_amm_pct==null?'Sin cierre':r.mediana_neta_amm_pct.toFixed(2)+'% · n='+r.con_costos)+'</td><td>'+(r.exceso_mediano_vs_btc_pp==null?'Sin cierre':r.exceso_mediano_vs_btc_pp.toFixed(2)+' pp · n='+r.con_benchmark_btc)+'</td></tr>').join('')+'</tbody></table></div>';
    }
    $("cohortDays").onchange=draw;draw();
  } catch { box.textContent="Seguimiento no disponible para esta base."; }
}
