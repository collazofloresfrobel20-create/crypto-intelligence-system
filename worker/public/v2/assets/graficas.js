"use strict";
const scoreFields = [
  ["opportunity_score","Oportunidad","#39724f"],
  ["risk_score","Riesgo","#bd743d"],
  ["confidence_score","Confianza","#547993"],
  ["earliness_score","Detección temprana","#869446"]
];
const chartText = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const numberAvailable = n => typeof n === "number" && Number.isFinite(n);
const asScore = n => Math.min(100, Math.max(0,n));
const shortPercent = n => (n > 0 ? "+" : "") + n.toLocaleString("es-MX",{maximumFractionDigits:1}) + "%";
const noChart = text => '<p class="chart-empty">'+text+'</p>';

function candidateProfile(p) {
  const rows=scoreFields.map(([key,label,color])=>{
    const value=p[key];
    const calibrated=key==="confidence_score" && numberAvailable(p.confidence_score_calibrated) ? ' <small>Calibrada '+p.confidence_score_calibrated+'</small>':'';
    return '<div class="profile-row"><span>'+label+calibrated+'</span><div class="bar-track"><i style="width:'+(numberAvailable(value)?asScore(value):0)+'%;--bar:'+color+'"></i></div><b>'+(numberAvailable(value)?value:'—')+'</b></div>';
  });
  return '<div class="profile-chart"><div class="chart-title-row"><h3>Perfil del candidato</h3><small>0–100 puntos</small></div>'+rows.join("")+'</div>';
}

let selectedMapId=null;
function renderOpportunityMap(list) {
  const host=document.getElementById("opportunityMap");
  const points=list.filter(p=>numberAvailable(p.risk_score)&&numberAvailable(p.opportunity_score));
  if(!points.length){host.innerHTML=noChart("Sin candidatos activos con puntuación completa.");return;}
  const tokenOptions=points.map(p=>'<option value="'+chartText(p.id)+'">'+chartText(p.symbol)+'</option>').join("");
  host.innerHTML='<div class="plot-label">Oportunidad ↑</div><div class="map-layout"><div class="y-ticks"><span>100</span><span>50</span><span>0</span></div><div class="map-surface" role="group" aria-label="Riesgo horizontal y oportunidad vertical, de 0 a 100">'+points.map(p=>{
    const tone=verdictColor[VERDICT_ORDER.indexOf(p.verdict)]||"#67796c";
    return '<button type="button" class="scatter-point" data-token="'+chartText(p.id)+'" style="left:'+asScore(p.risk_score)+'%;bottom:'+asScore(p.opportunity_score)+'%;--point:'+tone+'" aria-label="'+chartText(p.symbol)+', riesgo '+p.risk_score+', oportunidad '+p.opportunity_score+'"><i></i><span>'+chartText(p.symbol)+'</span></button>';
  }).join("")+'</div><div class="x-ticks"><span>0</span><span>50</span><span>100</span></div></div><div class="plot-label x-label">Riesgo →</div><div class="map-readout"><label class="token-choose"><span class="sr-only">Token del mapa</span><select>'+tokenOptions+'</select></label><span class="map-values" aria-live="polite"></span><button class="analysis-go" aria-label="Abrir ficha del token seleccionado">Ver ficha <span aria-hidden="true">↗</span></button></div>';
  const selector=host.querySelector("select");
  const buttons=[...host.querySelectorAll(".scatter-point")];
  function select(id){
    const p=points.find(item=>String(item.id)===String(id))||points[0];
    selectedMapId=p.id;selector.value=p.id;
    buttons.forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.token===String(p.id))));
    host.querySelector(".map-values").textContent="Riesgo "+p.risk_score+" · Oportunidad "+p.opportunity_score;
  }
  buttons.forEach(b=>{b.onclick=()=>select(b.dataset.token);});
  selector.onchange=()=>select(selector.value);
  host.querySelector(".analysis-go").onclick=()=>{
    verdictFilter=null;statusFilter="active";renderStatusFilters();renderVerdictFilters();renderFiltered();
    goToView("oportunidades"); const el=document.getElementById("card-"+selectedMapId);if(el)focusCandidate(el);
  };
  select(selectedMapId);
}

let compareKey="opportunity_score";
function renderScoreComparison(list) {
  const host=document.getElementById("scoreComparison");
  host.innerHTML='<div class="chart-title-row"><div><h3>Comparador de candidatos</h3><small>Escala de 0 a 100 puntos</small></div><label><span class="sr-only">Puntuación que se compara</span><select>'+scoreFields.map(([k,title])=>'<option value="'+k+'">'+title+'</option>').join("")+'</select></label></div><div class="compare-bars"></div>';
  const selector=host.querySelector("select");
  const listBox=host.querySelector(".compare-bars");
  function draw(){
    const [key,,color]=scoreFields.find(([k])=>k===compareKey);
    selector.value=key;
    const top=list.filter(p=>numberAvailable(p[key])).sort((a,b)=>b[key]-a[key]).slice(0,8);
    listBox.innerHTML=top.length?top.map(p=>'<button class="comparison-row" data-token="'+chartText(p.id)+'" aria-label="Ver ficha de '+chartText(p.symbol)+', '+p[key]+' puntos"><b>'+chartText(p.symbol)+'</b><span class="bar-track"><i style="width:'+asScore(p[key])+'%;--bar:'+color+'"></i></span><strong>'+p[key]+'</strong></button>').join(""):noChart("Sin candidatos con puntuación para este filtro.");
    listBox.querySelectorAll("button").forEach(btn=>btn.onclick=()=>{goToView("oportunidades");const el=document.getElementById("card-"+btn.dataset.token);if(el)focusCandidate(el);});
  }
  selector.onchange=()=>{compareKey=selector.value;draw();};draw();
}
function observations(p) {
  const end=p.horizon_days||7;
  const observed=[{day:0,value:0}];
  if(end>1)observed.push({day:1,value:p.return_at_day1_pct});
  if(end>3)observed.push({day:3,value:p.return_at_day3_pct});
  observed.push({day:end,value:p.return_pct});
  return observed;
}
function candidateTimeline(p) {
  return '<div class="sample-timeline">'+observations(p).map(s=>'<div><small>Día '+s.day+'</small><b>'+(numberAvailable(s.value)?shortPercent(s.value):'Sin dato')+'</b></div>').join("")+'</div>';
}
const lineColors=["#2e6b49","#956134","#626c98","#397f84"];
function renderOutcomeChart(all) {
  const host=document.getElementById("outcomeChart");
  const closed=all.filter(p=>p.status==="evaluated"&&numberAvailable(p.return_pct)).sort((a,b)=>new Date(b.created_at)-new Date(a.created_at)).slice(0,4);
  host.innerHTML='<div class="chart-title-row"><div><h3>Últimos análisis con cierre</h3><small>Hasta cuatro casos recientes con retorno registrado entre los últimos 200 análisis de todas las redes. Cada curva parte del precio al analizar el token</small></div></div>';
  if(!closed.length){host.insertAdjacentHTML("beforeend",noChart("Aún no hay candidatos con un cierre registrado."));return;}
  const ids=new Set(closed.map(p=>p.id));
  const controls=document.createElement("div");controls.className="series-switch";controls.setAttribute("role","group");controls.setAttribute("aria-label","Series visibles");
  controls.innerHTML=closed.map((p,i)=>'<button type="button" style="--series:'+lineColors[i]+'" aria-pressed="true"><i></i>'+chartText(p.symbol)+'</button>').join("");
  const graph=document.createElement("div");host.append(controls,graph);
  const allSamples=closed.flatMap(p=>observations(p)).filter(s=>numberAvailable(s.value));
  const maxDay=Math.max(...allSamples.map(s=>s.day));
  const hi=Math.max(5,Math.ceil(Math.max(...allSamples.map(s=>s.value))/5)*5);
  const lo=Math.min(-5,Math.floor(Math.min(...allSamples.map(s=>s.value))/5)*5);
  const y=v=>100-(v-lo)/(hi-lo)*100;
  const x=d=>d/maxDay*100;
  const ticks=[...new Set(allSamples.map(s=>s.day))].sort((a,b)=>a-b);
  const fmt=n=>Number(n.toFixed(3));
  function draw(){
    const visible=closed.filter(p=>ids.has(p.id));
    const paths=visible.map(p=>{
      const color=lineColors[closed.indexOf(p)];
      let previous=null;
      return observations(p).map(s=>{
        if(!numberAvailable(s.value)){previous=null;return "";}
        const xx=fmt(x(s.day)), yy=fmt(y(s.value));
        const segment=previous?'<line x1="'+previous.x+'" y1="'+previous.y+'" x2="'+xx+'" y2="'+yy+'" stroke="'+color+'" stroke-width="2.5" vector-effect="non-scaling-stroke"/>':"";
        previous={x:xx,y:yy};return segment;
      }).join("");
    }).join("");
    const dots=visible.flatMap(p=>observations(p).filter(o=>numberAvailable(o.value)).map(o=>'<i class="series-dot" style="left:'+fmt(x(o.day))+'%;top:'+fmt(y(o.value))+'%;--series:'+lineColors[closed.indexOf(p)]+'" title="'+chartText(p.symbol)+' · día '+o.day+' · '+shortPercent(o.value)+'"></i>')).join("");
    const table=visible.map(p=>'<tr><th scope="row">'+chartText(p.symbol)+'</th>'+ticks.map(d=>{const s=observations(p).find(o=>o.day===d);return '<td>'+(s&&numberAvailable(s.value)?shortPercent(s.value):'—')+'</td>';}).join("")+'</tr>').join("");
    graph.innerHTML='<div class="line-chart" aria-hidden="true"><div class="line-y">'+[hi,0,lo].map(v=>'<span style="top:'+fmt(y(v))+'%">'+shortPercent(v)+'</span>').join("")+'</div><div class="line-surface">'+[hi,0,lo].map(v=>'<i class="line-guide" style="top:'+fmt(y(v))+'%"></i>').join("")+'<svg viewBox="0 0 100 100" preserveAspectRatio="none">'+paths+'</svg>'+dots+'</div><div class="line-x">'+ticks.map(d=>'<span style="left:'+fmt(x(d))+'%">Día '+d+'</span>').join("")+'</div></div><details class="chart-data"><summary>Ver valores por día</summary><div class="table-wrap"><table><thead><tr><th>Token</th>'+ticks.map(d=>'<th>Día '+d+'</th>').join("")+'</tr></thead><tbody>'+table+'</tbody></table></div></details>';
  }
  [...controls.children].forEach((btn,i)=>{btn.onclick=()=>{
    const id=closed[i].id;if(ids.has(id)&&ids.size===1)return;
    if(ids.has(id))ids.delete(id);else ids.add(id);
    [...controls.children].forEach((b,j)=>{b.setAttribute("aria-pressed",String(ids.has(closed[j].id)));b.disabled=ids.size===1&&ids.has(closed[j].id);});draw();
  };btn.disabled=closed.length===1;});
  draw();
}
