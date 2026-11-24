const el = id => document.getElementById(id);
function append(parent, tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  parent.append(node);
  return node;
}
let detailSequence = 0;
const fmt = new Intl.NumberFormat("es-MX",{maximumSignificantDigits:5});
const usd = v => v == null ? "Sin dato" : fmt.format(v) + " USD";
async function get(path, params) {
  const res = await fetch(path + "?" + new URLSearchParams(params), {signal:AbortSignal.timeout(18000)});
  if (res.status === 401) { location.assign("/login"); throw Error(); }
  if (!res.ok) throw Error((await res.json().catch(()=>({}))).detail || "Consulta no disponible.");
  return res.json();
}
async function detail(row, panel, supplied = null) {
  const seq = ++detailSequence;
  panel.replaceChildren();
  append(panel,"h3","Expediente de investigación");

  append(panel,"p","Observado el " + row.observed_at);
  const shadowRun = row.run_id.startsWith("shadow-");
  let why = row.shortlisted ? "Preseleccionado para investigación por el ciclo." :
            row.eligible ? "Pasó los filtros y quedó como alternativa de comparación." : "Excluido de la preselección de esta corrida.";
  if (shadowRun && row.shadow_arm === "random") why = "Investigado por sorteo entre elegibles de su cadena.";
  if (shadowRun && row.shadow_arm === "ranked") why = "Investigado por su posición en la heurística de volumen y movimiento de precio.";
  append(panel,"p",row.source === "consulta_manual" ? "Investigado a solicitud del usuario." : why);


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
    if(!d.blind_review || d.blind_review.estado==="evaluacion_incompleta"){
      const button=append(panel,"button","Contrastar con dos jueces");
      const feedback=append(panel,"p","");
      button.onclick=async()=>{
        button.disabled=true;
        try{
          const r=await fetch("/api/v2/judges",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({run:row.run_id,key:row.token_key})});
          if(r.status===401){location.assign("/login");return;}
          if(!r.ok)throw Error((await r.json()).detail || "No se pudo iniciar");
          let job=await r.json();
          const reviewId=job.id;
          const deadline=Date.now()+12*60*1000;
          while(["queued","running"].includes(job.status) && seq===detailSequence && Date.now()<deadline){
            feedback.textContent="Evaluación independiente en curso…";
            await new Promise(resolve=>setTimeout(resolve,4000));
            job=await get("/api/v2/judges",{id:reviewId});
          }
          if(seq!==detailSequence)return;
          if(job.status==="done")await detail(row,panel);
          else feedback.textContent=job.status==="error"?"Los jueces no completaron la evaluación.":"La evaluación continúa. Abre la ficha más tarde.";
        }catch(error){feedback.textContent=error.message || "No se pudo completar la revisión.";}
        finally{button.disabled=false;}
      };
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

const root=el("token-finder");
const network={bsc:"BNB Smart Chain",solana:"Solana"};
const verdicts={"Strong Opportunity":"Oportunidad fuerte",Watchlist:"En observación","High Risk":"Riesgo alto",
  "Insufficient Evidence":"Evidencia insuficiente",Reject:"Descartado"};
function dateLabel(value) {
  const date=new Date(value);
  return Number.isNaN(date.getTime()) ? "Fecha desconocida" : date.toLocaleDateString("es-MX",{day:"numeric",month:"short",year:"numeric"});
}
const pct=v=>(v>0?"+":"")+Number(v).toLocaleString("es-MX",{maximumFractionDigits:2})+"%";
root.innerHTML=`<header class="finder-head"><h1>Buscar token</h1><p>Consulta qué encontró CIS o inicia una investigación del contrato que elijas.</p></header>
  <form class="finder-form" role="search"><label>Nombre, símbolo o contrato<input name="q" type="search" autocomplete="off" enterkeyhint="search" maxlength="120" placeholder="Ej. PancakeSwap o CAKE"></label>
    <label>Red<select name="chain"><option value="">Ambas redes</option><option value="bsc">BNB Smart Chain</option><option value="solana">Solana</option></select></label>
    <button class="finder-submit">Buscar</button></form>
  <h2 class="finder-list-head">Expedientes recientes</h2><p class="finder-status" role="status"></p><div class="finder-results"></div>`;
const form=root.querySelector("form"), heading=root.querySelector(".finder-list-head"), status=root.querySelector(".finder-status");
const results=root.querySelector(".finder-results");
const dialog=document.createElement("dialog");
dialog.className="finder-dialog"; dialog.setAttribute("aria-labelledby","finder-title");
dialog.innerHTML=`<header><span>Ficha del token</span><button type="button" class="finder-back" autofocus>Volver a resultados</button></header><div class="finder-body"></div>`;
document.body.append(dialog);
const body=dialog.querySelector(".finder-body");
dialog.querySelector(".finder-back").onclick=()=>dialog.close();
dialog.onclick=e=>{if(e.target===dialog)dialog.close();};
let viewSequence=0, searchSequence=0, loaded=false;
dialog.addEventListener("close",()=>{viewSequence++;detailSequence++;});

function state(item) {
  if (item.analyzed) return "Análisis operativo";
  if (item.dossier) return "Con expediente";
  if (item.discarded || item.archive?.eligible===false) return "Descartado por filtros";
  if (item.archive || item.observation) return "Observado por CIS";
  return item.market ? "Mercado localizado" : "Contrato por verificar";
}
function addButton(parent,text,handler) {
  const btn=append(parent,"button",text,"finder-action");btn.type="button";btn.onclick=()=>handler(btn);return btn;
}
function section(parent,title) {
  const node=append(parent,"section","","finder-section");append(node,"h3",title);return node;
}
function addLines(parent,lines) {
  if (Array.isArray(lines)) for (const line of lines) if(typeof line==="string")append(parent,"p",line);
}
async function showDossier(target,item,run,seq) {
  target.textContent="Cargando expediente…";
  const d=await get("/api/v2/dossier",{run,key:item.key});
  if (seq!==viewSequence) return;
  if (!d.observation) throw Error("Falta la observación que identifica el expediente.");
  await detail({...d.observation,has_dossier:true},target,d);
}
async function investigate(item,seq,target,button) {
  button.disabled=true;
  try {
    const response=await fetch("/api/v2/investigate",{method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({key:item.key}),signal:AbortSignal.timeout(20000)});
    if (response.status===401) {location.assign("/login");return;}
    let job=await response.json();
    if (!response.ok) throw Error(job.detail || "No se pudo iniciar la investigación.");
    const until=Date.now()+15*60000;
    while (["running","queued"].includes(job.status)) {
      if (seq!==viewSequence) return;
      target.textContent=job.status==="queued" ? "Investigación en cola. Puedes volver a buscar este contrato para consultar el avance." : "CIS está leyendo las fuentes de este contrato.";
      if(Date.now()>=until) return;
      await new Promise(resolve=>setTimeout(resolve,4500));
      if (seq!==viewSequence) return;
      job=await get("/api/v2/request",{id:job.id});
    }
    if(seq!==viewSequence)return;
    if(job.status!=="done") throw Error(job.error || "La investigación quedó incompleta.");
    item.dossier={run:job.result_run};
    await showDossier(target,item,job.result_run,seq);
    button.hidden=true;
  } catch(e) {if(seq===viewSequence)target.textContent=e.message || "No se pudo completar la investigación.";}
  finally {button.disabled=false;}
}

async function openToken(item) {
  const seq=++viewSequence;detailSequence++;body.replaceChildren();
  const title=append(body,"h2",item.symbol || item.name || "Contrato");
  title.id="finder-title";
  append(body,"p",[item.name!==item.symbol?item.name:null,network[item.chain],state(item)].filter(Boolean).join(" · "));
  const hasKey=!item.key.startsWith("legacy:");
  append(body,hasKey?"code":"p",hasKey?item.key.split(":")[1]:"Este registro histórico carece de un contrato verificable.");
  if(!dialog.open)dialog.showModal();
  dialog.scrollTop=0;
  if (item.closed) {
    const area=section(body,"Resultado registrado");
    append(area,"p",pct(item.closed.pct)+" al cierre · "+(item.closed.days || "—")+" días · "+dateLabel(item.closed.at));
    append(area,"small",item.closed.category==="discarded"?"Seguimiento de un descarte":"Seguimiento del análisis operativo");
  }
  if (item.first?.price>0 && item.latest?.price>0 && item.first.at<item.latest.at) {
    const area=section(body,"Desde que apareció en la captura");
    append(area,"p",pct(100*(item.latest.price/item.first.price-1))+" entre dos precios observados, antes de costos.");
    const when=ts=>new Date(ts).toLocaleString("es-MX",{dateStyle:"short",timeStyle:"short"});
    append(area,"small",usd(item.first.price)+" · "+when(item.first.at)+" → "+usd(item.latest.price)+" · "+when(item.latest.at));
  }
  if (item.discarded || item.rejection) {
    const area=section(body,"Por qué se descartó");
    if(item.discarded) {
      append(area,"strong","Motor operativo · "+dateLabel(item.discarded.at));
      addLines(area,item.discarded.reasons.length?item.discarded.reasons:["El registro conserva el descarte sin motivo detallado."]);
    }
    if(item.rejection){
      append(area,"strong","Captura en paralelo · "+dateLabel(item.rejection.at));addLines(area,item.rejection.reasons);
    }
  }
  if (item.archive) append(body,"p","Última captura en paralelo · "+dateLabel(item.archive.at)+" · "+
    (item.archive.eligible?"Pasó los filtros de esa corrida.":"Quedó fuera de los filtros de esa corrida."));
  else if (item.observation && !item.dossier) append(body,"p","Última observación de CIS · "+dateLabel(item.observation.at)+" · "+
    (item.observation.arm==="ranked"?"Seleccionado por ranking.":item.observation.arm==="random"?"Seleccionado por sorteo.":
      item.observation.eligible?"Elegible en esa corrida.":"Registrado en esa corrida."));
  const analysis=item.analyzed ? section(body,"Análisis operativo") : null;
  if (analysis) {
    append(analysis,"strong",verdicts[item.analyzed.verdict]||item.analyzed.verdict||"Sin veredicto");
    append(analysis,"p",dateLabel(item.analyzed.at)+(item.analyzed.status==="evaluated"?" · Evaluado":" · Seguimiento abierto"));
  }
  if (hasKey) {
    const area=section(body,item.dossier?"Investigación por contrato":"Investigar este contrato");
    if (!item.dossier) append(area,"p","CIS comprobará el mercado y reunirá las fuentes disponibles para este token.");
    const content=append(area,"div","","finder-dossier");content.setAttribute("aria-live","polite");
    if (item.dossier) {
      showDossier(content,item,item.dossier.run,seq).catch(e=>{if(seq===viewSequence)content.textContent=e.message;});
    } else {
      const btn=addButton(area,"Iniciar investigación",button=>investigate(item,seq,content,button));
      area.insertBefore(btn,content);
    }
  }
  if (analysis) {
    try {
      const p=await get("/api/predictions/"+item.analyzed.id,{});
      if(seq!==viewSequence)return;
      for (const [label,field] of [["Proyecto","project_explainer"],["Tesis favorable","bull_case"],["Escenario adverso","bear_case"]]) {
        if (p[field]) {append(analysis,"h4",label);append(analysis,"p",p[field]);}
      }
      addLines(analysis,p.key_evidence);addLines(analysis,p.main_risks);
    } catch {if(seq===viewSequence)append(analysis,"p","No se pudo leer el análisis detallado.");}
  }
}

async function search() {
  const seq=++searchSequence, q=form.elements.q.value.trim();
  loaded=true;results.replaceChildren();
  heading.textContent=q?"Resultados":"Expedientes recientes";
  status.textContent=q?"Buscando en CIS y en el mercado…":"Cargando los últimos expedientes…";
  try {
    const data=await get("/api/v2/search",{q,chain:form.elements.chain.value});
    if(seq!==searchSequence)return;
    status.textContent=data.items.length ? (q ? data.items.length+" coincidencia"+(data.items.length===1?"":"s")+
      (data.truncated?" mostradas. Precisa el nombre o pega la dirección.":". Comprueba la red y el contrato.") : "Última investigación disponible por contrato.") :
      q ? "Sin coincidencias. Prueba el nombre completo o pega la dirección del contrato." : "Todavía no hay expedientes en esta red.";
    if(data.market_status==="no_disponible")status.textContent+=" La fuente de mercado está temporalmente indisponible.";
    for (const item of data.items) {
      const btn=append(results,"button","","finder-row");btn.type="button";
      const main=append(btn,"span","","finder-row-title");append(main,"strong",item.symbol || "Contrato");
      if(item.name && item.name!==item.symbol)append(main,"small",item.name);
      const meta=append(btn,"span","","finder-row-meta");append(meta,"span",network[item.chain]);append(meta,"small",state(item));
      append(btn,"code",item.key.startsWith("legacy:")?"Registro sin contrato":item.key.split(":")[1]);
      append(btn,"span","Ver ficha ›","finder-open");
      btn.onclick=()=>openToken(item);
    }
  } catch(e) {if(seq===searchSequence)status.textContent=e.message || "No se pudo consultar el buscador.";}
}
form.onsubmit=e=>{e.preventDefault();search();};
form.elements.chain.onchange=search;
window.cisFindToken=(q,chain="")=>{
  if(dialog.open)dialog.close();
  form.elements.q.value=q||"";form.elements.chain.value=Object.hasOwn(network,chain)?chain:"";
  loaded=true;
  if(location.hash!=="#expedientes")location.hash="expedientes";
  search();
};
for(const home of document.querySelectorAll("form[data-token-search]"))
  home.onsubmit=e=>{e.preventDefault();window.cisFindToken(home.elements.q.value,home.elements.chain?.value || "");};
window.addEventListener("cis:view",e=>{
  if(e.detail==="expedientes"&&!loaded)search();
  if(e.detail!=="expedientes"&&dialog.open)dialog.close();
});
if(location.hash==="#expedientes")search();
