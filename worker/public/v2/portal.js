const el = id => document.getElementById(id);
function append(parent, tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  parent.append(node);
  return node;
}
let page = 0, sequence = 0, detailSequence = 0, timer;
const fmt = new Intl.NumberFormat("es-MX",{maximumSignificantDigits:5});
const usd = v => v == null ? "Sin dato" : fmt.format(v) + " USD";
async function get(path, params) {
  const res = await fetch(path + "?" + new URLSearchParams(params));
  if (res.status === 401) { location.assign("/login"); throw Error(); }
  if (!res.ok) throw Error();
  return res.json();
}
async function detail(row, supplied = null) {
  const seq = ++detailSequence, panel = el("detail");
  panel.replaceChildren();
  append(panel,"h2",row.symbol || row.token_key);
  append(panel,"code",row.token_key);
  append(panel,"p","Observado el " + row.observed_at);
  const shadowRun = row.run_id.startsWith("shadow-");
  let why = row.shortlisted ? "Preseleccionado para investigación por el ciclo." :
            row.eligible ? "Pasó los filtros y quedó como alternativa de comparación." : "Excluido de la preselección de esta corrida.";
  if (shadowRun && row.shadow_arm === "random") why = "Investigado por sorteo entre elegibles de su cadena.";
  if (shadowRun && row.shadow_arm === "ranked") why = "Investigado por su posición en la heurística de volumen y movimiento de precio.";
  append(panel,"p",row.source === "consulta_manual" ? "Investigado a solicitud del usuario." : why);

  panel.focus();
  append(panel,"h3","Mercado observado");
  append(panel,"p","Precio " + usd(row.price) + " · Liquidez " + usd(row.liquidity) + " · Volumen 24 h " + usd(row.volume_24h));
  if (!row.has_dossier) { append(panel,"p","El contrato quedó registrado en el universo de comparación. Esta observación carece de expediente."); return; }
  try {
    const d = supplied || await get("/api/v2/dossier",{run:row.run_id,key:row.token_key});
    if (seq !== detailSequence) return;
    const card = d.project_card;
    append(panel,"h3","Proyecto a meses");
    append(panel,"p",card ? (card.verdict === "Reject" ? "Veto documentado" : card.score == null ? "Sin calificar" : card.score + "/100") + " · Cobertura " + card.coverage + "%" : "Expediente anterior a la rúbrica.");
    if (card) append(panel,"p","Veredicto experimental · " + card.verdict + " · Rango por datos faltantes " + card.range.join("–"));
    if (d.reviewed_at) append(panel,"p","Última revisión " + d.reviewed_at + " · " + (d.blind_review?.estado || "Revisión documental"));
    if (card) for (const [key,r] of Object.entries(card.dimensions)) append(panel,"p",key + " · " + (r.level ?? "Sin dato") + " — " + r.reason,"metric");
    for (const v of card?.vetoes || []) append(panel,"p",v);
    append(panel,"h3","Fuentes y eventos");
    for (const s of d.documents || []) append(panel,"p",s.url + " · HTTP " + (s.http_status ?? "sin respuesta") + " · " + s.read_at);
    for (const m of d.protocol?.metrics || []) append(panel,"p",m.slug + " · TVL " + usd(m.value) + " · " + m.source);
    for (const m of d.protocol?.metrics || []) {
      const peers = m.peers_by_same_category_and_chain || [];
      if (peers.length) append(panel,"p","Comparables de " + m.slug + " · " + peers.map(p => p.slug + " (" + usd(p.tvl_usd) + ")").join(" · "));
    }
    for (const [name,j] of Object.entries(d.blind_review?.judges || {})) {
      append(panel,"h3","Revisión de " + name);
      append(panel,"p",j.error ? "Evaluación incompleta" : [j.tesis,j.rival,j.invalidacion].join(" · "));
      for(const [scenario,condition] of Object.entries(j.escenarios || {})) append(panel,"p",scenario + " · " + condition);
    }
    for (const n of d.news?.leads || []) append(panel,"p",n.title + " · " + n.publisher + " · " + n.status);
    if (!(d.news?.leads || []).length) append(panel,"p","Sin noticias vinculadas en los feeds consultados.");
    append(panel,"h3","Escenarios por comprobar");
    for (const s of d.thesis_review?.preguntas_de_escenario || []) append(panel,"p",s.caso + " · " + s.dias + " días. " + s.observar + ". " + s.invalida);
  } catch { if (seq === detailSequence) append(panel,"p","No se pudo cargar el expediente. Vuelve a seleccionar el contrato."); }
}
async function load() {
  const seq = ++sequence; ++detailSequence;
  el("detail").replaceChildren(); el("results").replaceChildren();
  el("status").textContent = "Cargando observaciones…"; el("prev").disabled = el("next").disabled = true;
  try {
    const data = await get("/api/v2/observations",{chain:el("chain").value,q:el("query").value.trim(),page});
    if (seq !== sequence) return;
    el("status").textContent = data.rows.length ? "Observaciones más recientes · Página " + (page+1) : "Sin observaciones para esta búsqueda.";
    for (const row of data.rows) {
      const b = append(el("results"),"button","","row");
      append(b,"strong",row.symbol || "Sin símbolo"); append(b,"span",row.has_dossier ? "Ver expediente" : "Ver observación");
      append(b,"small",row.token_key); b.onclick = () => detail(row);
    }
    el("next").disabled = !data.more;
  } catch { if (seq === sequence) el("status").textContent = "No se pudo leer el registro de investigación. Comprueba la conexión y el estado del colector."; }
  if (seq === sequence) el("prev").disabled = page === 0;
}
el("chain").onchange = () => {page=0;load();};
el("query").oninput = () => {clearTimeout(timer);page=0;timer=setTimeout(load,200);};
el("prev").onclick = () => {page--;load();};el("next").onclick = () => {page++;load();};load();

el("investigate").onsubmit = async event => {
  event.preventDefault();
  const button=event.currentTarget.querySelector("button");
  const status=el("request-status");
  const chain=el("request-chain").value, address=el("address").value.trim();
  if (!(chain==="bsc" ? /^0x[0-9a-fA-F]{40}$/ : /^[1-9A-HJ-NP-Za-km-z]{32,44}$/).test(address)) {
    status.textContent="Revisa la dirección del contrato y la cadena.";return;
  }
  button.disabled=true;
  try {
    const res=await fetch("/api/v2/investigate",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({key:chain+":"+address})});
    if(res.status===401){location.assign("/login");return;}
    let job=await res.json();
    if(!res.ok) throw Error(job.detail || "Error al iniciar");
    const deadline=Date.now()+15*60*1000;
    while (job.status==="queued" || job.status==="running") {
      status.textContent=job.status==="queued"?"Solicitud en cola…":"Leyendo fuentes y comprobando el contrato…";
      if(Date.now()>deadline){status.textContent="La consulta sigue en curso. Puedes volver a consultar el contrato.";return;}
      await new Promise(resolve=>setTimeout(resolve,3000));
      job=await get("/api/v2/request",{id:job.id});
    }
    if(job.status!=="done") throw Error(job.error || "Consulta incompleta");
    const d=await get("/api/v2/dossier",{run:job.result_run,key:job.token_key});
    if(!d.observation) throw Error("Falta la observación del expediente");
    status.textContent="Expediente disponible.";await detail({...d.observation,has_dossier:1},d);
  } catch(error){status.textContent="Consulta incompleta. "+(error.message || "Comprueba la conexión.");}
  finally{button.disabled=false;}
};
