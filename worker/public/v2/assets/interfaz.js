"use strict";
const glyphs = {
 grid:"M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
 rank:"M4 20h16 M6 16V9 M12 16V4 M18 16v-5",scan:"M4 9V5a1 1 0 0 1 1-1h4 M15 4h4a1 1 0 0 1 1 1v4 M20 15v4a1 1 0 0 1-1 1h-4 M9 20H5a1 1 0 0 1-1-1v-4 M8 12h8 M12 8v8",
 filter:"M3 5h18l-7 8v5l-4 2v-7z",history:"M3 11a9 9 0 1 1 2.6 7.1 M3 5v6h6 M12 7v5l3 2",
 pulse:"M2 12h5l3-8 4 16 3-8h5",nodes:"M5 4h5v5H5z M14 15h5v5h-5z M7.5 9v8.5H14 M10 6.5h6.5V15",
 sliders:"M4 6h6m4 0h6 M4 12h9m4 0h3 M4 18h2m4 0h10 M10 3v6m4 0v6M7 15v6",
 menu:"M3 6h18M3 12h18M3 18h18",exit:"M10 4H5v16h5 M9 12h12m-5-5 5 5-5 5",
 arrow:"M4 12h15m-6-6 6 6-6 6",upRight:"M6 18 18 6M7 6h11v11",
 donut:"M12 3a9 9 0 1 0 9 9h-9z M15 2.8V9h6.2A8.5 8.5 0 0 0 15 2.8z",
 refresh:"M19 8a8 8 0 0 0-14-2l-2 3 M3 4v5h5 M5 16a8 8 0 0 0 14 2l2-3 M16 15h5v5",
 layers:"m12 3 9 5-9 5-9-5z M3 12l9 5 9-5 M3 16l9 5 9-5", eye:"M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6z M14.4 12a2.4 2.4 0 1 1-4.8 0 2.4 2.4 0 0 1 4.8 0z",
};
function icon(name){return '<svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="'+glyphs[name]+'"/></svg>';}
document.querySelectorAll("[data-icon]").forEach(el=>{el.innerHTML=icon(el.dataset.icon);});
const navLinks=[...document.querySelectorAll(".tab-btn")];
const mobileNav=matchMedia("(max-width:760px)");
const menu=document.querySelector("#menu");
const veil=document.querySelector(".veil");
const rail=document.querySelector(".rail");
function toggleMenu(open){
 document.body.classList.toggle("menu-open",open);
 menu.setAttribute("aria-expanded",String(open)); menu.setAttribute("aria-label",open?"Cerrar menú":"Abrir menú");
 veil.hidden=!open;
 rail.inert=mobileNav.matches&&!open;
 if(open) rail.querySelector(".active").focus();
}
function showView(key,moveFocus){
 const current=navLinks.find(link=>link.dataset.view===key)||navLinks[0];
 navLinks.forEach(link=>{
   link.classList.toggle("active",current===link);
   if(link===current)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current");
 });
 document.querySelectorAll("main>.view").forEach(view=>view.classList.toggle("active",view.id==="view-"+current.dataset.view));
 document.querySelector("#currentView").textContent=current.textContent.trim();
 document.querySelector(".topGroup").textContent=!["autocorreccion","metodologia","configuracion"].includes(current.dataset.view)?"Investigación":"Sistema";
 document.title=current.textContent.trim()+" · CIS";
 toggleMenu(false);
 window.dispatchEvent(new CustomEvent("cis:view",{detail:current.dataset.view}));
 if(moveFocus){document.querySelector("main").focus({preventScroll:true});window.scrollTo(0,0);}
}
function goToView(key){
 if(location.hash!=="#"+key){history.pushState(null,"","#"+key);}
 showView(key,true);
}
function focusCandidate(card){card.focus({preventScroll:true});card.scrollIntoView({block:"start",behavior:matchMedia("(prefers-reduced-motion:reduce)").matches?"auto":"smooth"});}
if(menu){
 menu.addEventListener("click",()=>toggleMenu(!document.body.classList.contains("menu-open")));
 veil.addEventListener("click",()=>{toggleMenu(false);menu.focus();});
 document.addEventListener("keydown",e=>{
   if(!document.body.classList.contains("menu-open"))return;
   if(e.key==="Escape"){toggleMenu(false);menu.focus();}
   if(e.key==="Tab"){
     const links=[...rail.querySelectorAll("a")]; const first=links[0], last=links[links.length-1];
     if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
     else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
   }
 });
 mobileNav.addEventListener("change",()=>toggleMenu(false));
 document.addEventListener("click",e=>{
   const anchor=e.target.closest('a[href^="#"]');
   if(!anchor||e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;
   const key=anchor.hash.slice(1);
   if(!navLinks.some(link=>link.dataset.view===key))return;
   e.preventDefault();goToView(key);
 });
 window.addEventListener("popstate",()=>showView(location.hash.slice(1),true));
 window.addEventListener("hashchange",()=>showView(location.hash.slice(1),true));
 showView(location.hash.slice(1),false);
}
const verdictName=["Oportunidad fuerte","En observación","Riesgo alto","Evidencia insuficiente","Descartado"];
const verdictColor=["#346b48","#b3ce4a","#d89c46","#91a6ad","#c96e60"];
const tokenShapes=["m5 17 8-12 6 2-8 12z M7 12h10","M4 16 12 3l8 13H4z M7 18h10","M4 13a8 8 0 1 0 16 0a8 8 0 1 0-16 0 M2 15l20-6","m5 18 6-13 2 7h6l-8 8 1-7z","M3 6h5l4 12 4-12h5"];
function tokenMark(p){
 const i=(p.id-1)%5;
 return '<span class="token-mark mark-'+i+'" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="'+tokenShapes[i]+'"/></svg></span>';
}
function drawOverview(analyzed,discardCount,run){
 const active=analyzed.filter(isActive).sort((a,b)=>b.opportunity_score-a.opportunity_score);
 const strong=active.filter(p=>p.verdict==="Strong Opportunity").length;
 document.querySelector("#selectionNote").textContent=strong ? strong+" análisis activos con veredicto de oportunidad fuerte." : "Ningún análisis activo cargado alcanzó el veredicto de oportunidad fuerte.";
 const evaluated=analyzed.filter(p=>p.status==="evaluated");
 const counts=VERDICT_ORDER.map(v=>analyzed.filter(p=>p.verdict===v).length);
 const metrics=[["scan",active.length,"Candidatos activos","ranking"],["filter",discardCount,"Descartados","descartados"],["history",evaluated.length,"Evaluados a 7 días","historial"]];
 document.querySelector("#overviewMetrics").innerHTML=metrics.map(([svg,n,label,key])=>'<a href="#'+key+'" class="metric"><div><span>'+label+'</span><strong>'+n.toLocaleString("es-MX")+'</strong></div><i>'+icon(svg)+'</i></a>').join("");
 const picks=document.querySelector("#overviewPicks");
 picks.innerHTML=active.length?'<div class="pick-head"><span>Token</span><span>Veredicto</span><span>Oport.</span></div>'+active.slice(0,4).map(p=>{
   const i=VERDICT_ORDER.indexOf(p.verdict);
   return '<button type="button" class="pick" data-id="'+p.id+'"><span class="token">'+tokenMark(p)+'<span><b>'+p.symbol+'</b><small>'+p.name+'</small></span></span><span class="badge '+VERDICT_CLASS[p.verdict]+'">'+verdictName[i]+'</span><strong class="score">'+p.opportunity_score+'</strong></button>';
 }).join(""):'<p class="empty-state">Sin candidatos activos.</p>';
 picks.querySelectorAll(".pick").forEach(btn=>btn.onclick=()=>{
   verdictFilter=null;statusFilter="active";renderStatusFilters();renderVerdictFilters();renderFiltered();
   goToView("oportunidades");const card=document.querySelector("#card-"+btn.dataset.id);if(card)focusCandidate(card);
 });
 let pos=0;
 const colors=counts.flatMap((n,i)=>{
   if(!n)return [];
   const left=pos;pos+=n*100/analyzed.length;return [verdictColor[i]+" "+left+"% "+pos+"%"];
 });
 document.querySelector("#overviewVerdicts").innerHTML='<div class="verdict-total"><div class="donut" aria-hidden="true" style="--slices:'+ (colors.length?'conic-gradient('+colors.join(",")+')':'#e4e7dc')+'"><span>'+analyzed.length+'</span></div><div><strong>'+analyzed.length+' análisis</strong><small>Análisis cargados</small></div></div><ul class="verdict-list">'+counts.map((n,i)=>'<li><span class="dot" style="--dot:'+verdictColor[i]+'"></span>'+verdictName[i]+'<b>'+n+'</b></li>').join("")+'</ul>';
 document.querySelector("#lastRunStat").textContent=run?relativeTime(run.finished_at||run.started_at)+" · ciclo operativo":"Sin ciclos registrados";
}
