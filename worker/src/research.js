import { dispatchUpdateWorkflow } from "./github.js";
export async function researchApi(db, url) {
  const query = url.searchParams;
  if (url.pathname.endsWith("/dossier")) {
    const row = await db.one("SELECT payload FROM research_dossiers WHERE run_id=? AND token_key=?", [query.get("run"),query.get("key")]);
    if (!row) return Response.json({detail:"Expediente inexistente"},{status:404});
    const d = JSON.parse(row.payload);
    const review = await db.one("SELECT reviewed_at,payload FROM dossier_reviews WHERE run_id=? AND token_key=? ORDER BY reviewed_at DESC LIMIT 1",[query.get("run"),query.get("key")]);
    const observation=await db.one("SELECT * FROM survivor_observations WHERE run_id=? AND token_key=?",[query.get("run"),query.get("key")]);
    const current = review ? JSON.parse(review.payload) : {};
    return Response.json({observation,reviewed_at:review?.reviewed_at,blind_review:current.blind_review,project_card:current.project_card || d.project_card,thesis_review:d.thesis_review,
      protocol:d.protocol,documents:(d.documents || []).map(({url,http_status,read_at})=>({url,http_status,read_at})),
      news:{leads:(d.news?.leads || []).map(({title,publisher,status})=>({title,publisher,status}))}});
  }
  const chain = query.get("chain") || "", q = (query.get("q") || "").trim();
  const page = Number(query.get("page") || 0);
  if (!["","bsc","solana"].includes(chain) || q.length>120 || !Number.isSafeInteger(page) || page<0 || page>100000) return Response.json({detail:"Filtro inválido"},{status:400});
  const rows = await db.all(
    "SELECT s.*, EXISTS(SELECT 1 FROM research_dossiers d WHERE d.run_id=s.run_id AND d.token_key=s.token_key) AS has_dossier FROM survivor_observations s WHERE token_key LIKE ? AND (instr(lower(coalesce(symbol,'')),lower(?))>0 OR instr(token_key,?)>0) ORDER BY observed_at DESC,run_id,token_key LIMIT 26 OFFSET ?",
    [chain ? chain+":%" : "%",q,q,page*25]);
  return Response.json({rows:rows.slice(0,25),more:rows.length>25});
}

export async function requestResearch(db, request, env) {
  if (!request.headers.get("content-type")?.includes("application/json")) return Response.json({detail:"Se requiere JSON"},{status:415});
  const body=await request.json().catch(()=>null);
  let key=typeof body?.key === "string" ? body.key.trim() : "";
  if (/^bsc:0x[0-9a-fA-F]{40}$/.test(key)) key=key.toLowerCase();
  else if (!/^solana:[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(key)) return Response.json({detail:"Contrato inválido"},{status:400});
  const now=new Date(), since = ms => new Date(now.getTime()-ms).toISOString();
  const cached=await db.one("SELECT run_id FROM research_dossiers WHERE token_key=? AND julianday(read_at)>=julianday(?) ORDER BY read_at DESC LIMIT 1",[key,since(7*86400000)]);
  if (cached) return Response.json({status:"done",result_run:cached.run_id,token_key:key});
  const existing=await db.one("SELECT * FROM research_requests WHERE token_key=? AND status IN ('queued','running') AND julianday(created_at)>=julianday(?) ORDER BY created_at DESC LIMIT 1",[key,since(1200000)]);
  if (existing) return Response.json(existing);
  const id=crypto.randomUUID();
  await db.run("INSERT INTO research_requests (id,token_key,created_at,status) VALUES(?,?,?,'queued')",[id,key,now.toISOString()]);
  try { await dispatchUpdateWorkflow(env,id); }
  catch { await db.run("UPDATE research_requests SET status='error',error=? WHERE id=?",["No se pudo iniciar el workflow",id]);return Response.json({detail:"No se pudo iniciar la investigación"},{status:503}); }
  return Response.json({id,status:"queued",token_key:key});
}

export async function requestJudges(db, request, env) {
  if(!request.headers.get("content-type")?.includes("application/json")) return Response.json({}, {status:415});
  const body=await request.json().catch(()=>null);
  if(typeof body?.run!=="string" || typeof body?.key!=="string") return Response.json({}, {status:400});
  const args=[body.run,body.key];
  if(!await db.one("SELECT 1 FROM research_dossiers WHERE run_id=? AND token_key=?",args)) return Response.json({detail:"Expediente inexistente"},{status:404});
  const id=crypto.randomUUID(), now=new Date();
  const row=await db.one("SELECT id,status,requested_at FROM judge_requests WHERE run_id=? AND token_key=?",args);
  if(row && (row.status==="done" || now-Date.parse(row.requested_at)<15*60000)) return Response.json(row);
  const cached=await db.one("SELECT json_extract(payload,'$.blind_review.estado') AS state FROM dossier_reviews WHERE run_id=? AND token_key=? ORDER BY reviewed_at DESC LIMIT 1",args);
  if(cached?.state && cached.state!=="evaluacion_incompleta") return Response.json({status:"done"});
  await db.run("INSERT INTO judge_requests(id,run_id,token_key,requested_at,status) VALUES (?,?,?,?,'queued') ON CONFLICT(run_id,token_key) DO UPDATE SET id=excluded.id,requested_at=excluded.requested_at,status='queued' WHERE judge_requests.status!='done' AND julianday(judge_requests.requested_at)<julianday(excluded.requested_at)-15.0/1440",[id,...args,now.toISOString()]);
  const job=await db.one("SELECT id,status FROM judge_requests WHERE run_id=? AND token_key=?",args);
  if(job.id===id) {
    try {await dispatchUpdateWorkflow(env,id,true);}
    catch {await db.run("UPDATE judge_requests SET status='error' WHERE id=?",[id]);return Response.json({detail:"No se pudo iniciar la revisión"},{status:503});}
  }
  return Response.json(job);
}
