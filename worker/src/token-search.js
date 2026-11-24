const BSC = /^0x[0-9a-f]{40}$/i;
const SOL = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

function addressChain(raw) {
  return BSC.test(raw) ? "bsc" : SOL.test(raw) ? "solana" : "";
}

function assetKey(chain, address) {
  if (chain !== addressChain(address || "")) return null;
  return chain ? chain + ":" + (chain === "bsc" ? address.toLowerCase() : address) : null;
}

async function markets(q, chain, direct, fetcher) {
  const path = direct ? "/tokens/v1/" + direct + "/" + encodeURIComponent(q)
    : "/latest/dex/search?q=" + encodeURIComponent(q);
  const response = await fetcher("https://api.dexscreener.com" + path, {signal:AbortSignal.timeout(5500)});
  if (!response.ok) throw Error("mercado");
  const body = await response.json();
  const pairs = direct ? body : body.pairs;
  if (pairs !== null && !Array.isArray(pairs)) throw Error("pares");
  const matches = new Map();
  const needle = q.toLowerCase();
  for (const pair of pairs || []) {
    if (chain && pair.chainId !== chain) continue;
    const token = pair.baseToken;
    const key = assetKey(pair.chainId, token?.address);
    if (!key || !Number.isFinite(Number(pair.priceUsd)) || Number(pair.priceUsd) <= 0) continue;
    if (direct ? key !== assetKey(direct,q) :
      ![token.symbol,token.name].some(v => typeof v === "string" && v.toLowerCase().includes(needle))) continue;
    const liquidity = Math.max(0, Number(pair.liquidity?.usd) || 0);
    if (!matches.has(key) || liquidity > matches.get(key).liquidity)
      matches.set(key,{key, name:token.name,symbol:token.symbol,chain:pair.chainId,liquidity});
  }
  return [...matches.values()];
}

const criteria = `WITH criteria(q,chain,external,exact) AS (VALUES(?,?,?,?))`;
function matches(name, symbol) {
  return `(criteria.chain='' OR identity.chain=criteria.chain) AND (
      identity.token_key IN (SELECT value FROM json_each(criteria.external))
      OR (criteria.exact!='' AND identity.token_key=criteria.exact)
      OR (criteria.exact='' AND (instr(lower(coalesce(${name},'')),lower(criteria.q))>0
           OR instr(lower(coalesce(${symbol},'')),lower(criteria.q))>0)))`;
}
const chains = `CASE lower(trim(chain_name))
    WHEN 'bnb' THEN 'bsc' WHEN 'bnb chain' THEN 'bsc' WHEN 'bnb smart chain' THEN 'bsc'
    WHEN 'binance smart chain' THEN 'bsc' WHEN '56' THEN 'bsc' WHEN 'sol' THEN 'solana'
    ELSE lower(trim(chain_name)) END`;
const predSql = `${criteria}, base AS (
    SELECT id, ${chains} chain, trim(contract_address) address,symbol,name,category,status,
           verdict,created_at,horizon_days,return_pct,rejection_reasons
    FROM predictions WHERE category IN ('analyzed','discarded')
  ), identity AS (
    SELECT *, CASE WHEN length(address)>0 THEN chain||':'||CASE WHEN chain='bsc' THEN lower(address) ELSE address END
                   ELSE 'legacy:'||id END token_key FROM base WHERE chain IN ('bsc','solana')
  ), found AS (
    SELECT identity.*, row_number() OVER (PARTITION BY token_key,category,(status='evaluated') ORDER BY created_at DESC,id DESC) ordinal
    FROM identity,criteria WHERE ${matches("name","symbol")}
  ) SELECT * FROM found WHERE ordinal=1 ORDER BY symbol=(SELECT q FROM criteria) COLLATE NOCASE DESC,created_at DESC LIMIT 80`;
const archiveSql = `${criteria}, identity AS (
  SELECT a.*,substr(token_key,1,instr(token_key,':')-1) chain FROM search_archive a)
  SELECT identity.* FROM identity,criteria WHERE ${matches("name","symbol")}
  ORDER BY symbol=(SELECT q FROM criteria) COLLATE NOCASE DESC,observed_at DESC LIMIT 80`;
const dossierSql = `${criteria}, identity AS (
  SELECT s.symbol,s.token_key,s.observed_at,s.run_id,s.source,substr(s.token_key,1,instr(s.token_key,':')-1) chain,
         d.read_at,coalesce(json_extract(d.payload,'$.documents[0].data.name'),s.symbol) name
  FROM research_dossiers d JOIN survivor_observations s ON s.run_id=d.run_id AND s.token_key=d.token_key
  ), found AS (
  SELECT identity.*,row_number() OVER(PARTITION BY token_key ORDER BY read_at DESC,run_id DESC) ordinal
  FROM identity,criteria WHERE ${matches("name","symbol")})
  SELECT * FROM found WHERE ordinal=1 ORDER BY symbol=(SELECT q FROM criteria) COLLATE NOCASE DESC,read_at DESC LIMIT 40`;
const observationSql = `${criteria}, identity AS (
  SELECT symbol,token_key,observed_at,eligible,shortlisted,shadow_arm,source,
         substr(token_key,1,instr(token_key,':')-1) chain
  FROM survivor_observations WHERE token_key LIKE 'bsc:%' OR token_key LIKE 'solana:%'
  ), found AS (
  SELECT identity.*,row_number() OVER(PARTITION BY token_key ORDER BY observed_at DESC) ordinal
  FROM identity,criteria WHERE ${matches("symbol","symbol")})
  SELECT * FROM found WHERE ordinal=1 ORDER BY symbol=(SELECT q FROM criteria) COLLATE NOCASE DESC,observed_at DESC LIMIT 80`;

function asList(json) {
  try { const values=JSON.parse(json); return Array.isArray(values) ? values.filter(v=>typeof v==="string") : []; }
  catch {return [];}
}

export async function tokenSearch(db, url, fetcher=fetch) {
  let q = (url.searchParams.get("q") || "").trim(), chain = url.searchParams.get("chain") || "";
  const prefix = q.match(/^(bsc|solana):(.+)$/i);
  if (prefix) {
    if (chain && chain !== prefix[1].toLowerCase()) return Response.json({detail:"El contrato y el filtro indican redes distintas."},{status:400});
    chain = prefix[1].toLowerCase(); q = prefix[2];
  }
  if (!["","bsc","solana"].includes(chain) || q.length>120)
    return Response.json({detail:"Consulta inválida."},{status:400});
  const direct=addressChain(q);
  if ((direct && chain && direct!==chain) || (prefix && direct!==chain))
    return Response.json({detail:"Revisa el contrato y la red seleccionada."},{status:400});
  const exact=direct ? assetKey(direct,q) : "";
  let remote=[], marketStatus="omitido";
  if (q) {
    try {remote=await markets(q,chain,direct,fetcher); marketStatus="disponible";}
    catch {marketStatus="no_disponible";}
  }
  const params=[q,chain,JSON.stringify(remote.map(row=>row.key)),exact];
  let [predictions,archive,dossiers,observations]=await Promise.all([
    q ? db.all(predSql,params) : [], q ? db.all(archiveSql,params) : [], db.all(dossierSql,params),
    q ? db.all(observationSql,params) : []
  ]);
  const truncated=predictions.length===80 || archive.length===80 || dossiers.length===40 || observations.length===80;
  if (q && !exact) {
    // Un cambio de nombre también debe encontrar los análisis previos del mismo contrato.
    const keys=[...new Set([...remote.map(r=>r.key),...predictions.map(r=>r.token_key),
      ...archive.map(r=>r.token_key),...dossiers.map(r=>r.token_key),...observations.map(r=>r.token_key)])];
    if (keys.length) {
      const related=[q,chain,JSON.stringify(keys),"por_identidad"];
      [predictions,archive,dossiers,observations]=await Promise.all([db.all(predSql,related),db.all(archiveSql,related),
        db.all(dossierSql,related),db.all(observationSql,related)]);
    }
  }
  const assets=new Map();
  function add(key, chain, symbol, name) {
    if (!assets.has(key)) assets.set(key,{key,chain,symbol,name});
    const asset=assets.get(key);
    asset.name ||= name; asset.symbol ||= symbol;
    return asset;
  }
  for (const row of archive) {
    const item=add(row.token_key,row.chain,row.symbol,row.name);
    const entry={at:row.observed_at,eligible:!!row.eligible,reasons:asList(row.reasons)};
    if (!item.first || row.first_at<item.first.at) item.first={at:row.first_at,price:row.first_price};
    if (!item.latest || row.observed_at>item.latest.at) item.latest={at:row.observed_at,price:row.last_price};
    if (!item.archive || entry.at>item.archive.at) item.archive=entry;
    if (!entry.eligible) item.rejection=entry;
  }
  for (const row of predictions) {
    const item=add(row.token_key,row.chain,row.symbol,row.name);
    const result={id:row.id,at:row.created_at,status:row.status,verdict:row.verdict,
      days:row.horizon_days,pct:row.return_pct,reasons:asList(row.rejection_reasons)};
    if (!item[row.category] || result.at>item[row.category].at) item[row.category]=result;
    if (row.status==="evaluated" && row.return_pct!=null && (!item.closed || result.at>item.closed.at))
      item.closed={...result,category:row.category};
  }
  for (const row of dossiers) add(row.token_key,row.chain,row.symbol,row.name).dossier={run:row.run_id,at:row.read_at};
  for (const row of observations) add(row.token_key,row.chain,row.symbol,null).observation={
    at:row.observed_at,arm:row.shadow_arm,eligible:!!row.eligible,source:row.source};
  for (const row of remote) add(row.key,row.chain,row.symbol,row.name).market={liquidity:row.liquidity};
  if (exact && !assets.has(exact)) add(exact,direct,null,null);
  function priority(row) {
    return (row.key===exact ? 64 : 0) + (q && row.symbol?.toLowerCase()===q.toLowerCase() ? 32 : 0)
      + (q && row.name?.toLowerCase()===q.toLowerCase() ? 16 : 0)
      + (row.analyzed ? 8 : 0) + (row.dossier ? 4 : 0) + (row.discarded || row.archive ? 2 : 0);
  }
  const items=[...assets.values()].sort((a,b)=>priority(b)-priority(a)
    || (q ? (b.market?.liquidity||0)-(a.market?.liquidity||0) : b.dossier.at.localeCompare(a.dossier.at))
    || a.key.localeCompare(b.key));
  return Response.json({items:items.slice(0,q?32:8), truncated:truncated || items.length>(q?32:8) || predictions.length===80 ||
    archive.length===80 || dossiers.length===40,market_status:marketStatus});
}
