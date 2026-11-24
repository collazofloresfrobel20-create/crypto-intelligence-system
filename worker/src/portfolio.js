// Cartera de papel. La pertenencia a una cohorte se decide exclusivamente con el
// veredicto y la identidad conocidos en la fecha del análisis.
const HOUR = 3600000;
const DAY = 24 * HOUR;
const FEE = 0.003;
const RULES = [
  {key: "hold", label: "Mantener hasta el horizonte"},
  {key: "peak", label: "Máximo observado · retrospectivo"},
  {key: "stop15", label: "Stop −15%", stop: 0.15},
  {key: "stop20", label: "Stop −20%", stop: 0.20},
  {key: "tp30_sl15", label: "Objetivo +30% · stop −15%", target: 0.30, stop: 0.15},
  {key: "tp40_sl20", label: "Objetivo +40% · stop −20%", target: 0.40, stop: 0.20}
];
const CHAIN_SQL = "CASE WHEN lower(trim(chain_name)) IN ('bsc','bnb','bnb chain','bnb smart chain','56') THEN 'bsc' WHEN lower(trim(chain_name)) IN ('sol','solana') THEN 'solana' END";
const WEEK_SQL = "date(created_at, '-6 days', 'weekday 1')";
const iso = (time) => new Date(time).toISOString();
const money = (value) => Math.round((value + Number.EPSILON) * 100) / 100;
const positive = (v) => v != null && Number.isFinite(Number(v)) && Number(v) > 0;
const unavailable = (status, reason, source) => ({
  status, reason, source, exit_price: null, exit_at: null, value_usd: null, pnl_usd: null
});

export function portfolioOptions(url) {
  const q = url.searchParams;
  const days = Number(q.get("days") ?? 7);
  const stake = Number(q.get("stake") ?? 100);
  const chain = q.get("chain") ?? "all";
  const cohort = q.get("cohort") ?? "strong";
  const week = q.get("week") || null;
  if (![7,14,30].includes(days) || !Number.isFinite(stake) || stake < .01 || stake > 100000 ||
      Math.abs(Math.round(stake*100)-stake*100) > 0.00001 ||
      !["all","bsc","solana"].includes(chain) || !["strong","watchlist","all"].includes(cohort)) {
    throw new RangeError("Parámetros de cartera inválidos.");
  }
  if (week) {
    const date = new Date(week + "T00:00:00Z");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(week) || !Number.isFinite(+date) ||
        date.toISOString().slice(0,10) !== week || date.getUTCDay() !== 1) {
      throw new RangeError("Elige el lunes UTC de la semana.");
    }
  }
  return {days,stake,chain,cohort,week};
}

function realized(stake, entry, price, time, reason, source) {
  const value = stake * (1-FEE) * (price/entry) * (1-FEE);
  return {status:"settled", value_usd:money(value), pnl_usd:money(value-stake),
    exit_price:price, exit_at:time == null ? null : iso(time), reason, source};
}

// A gap through a stop fills at the observed opening price. Touching two barriers
// intrabar provides no first-hit ordering. A sampled quote can fill below the stop.
function trigger(bar, entry, rule, stake, source) {
  const stop = entry * (1-rule.stop);
  const target = rule.target ? entry * (1+rule.target) : Infinity;
  const [start, open, high, low, close, end] = bar;
  if (open <= stop) return realized(stake,entry,open,start,"Stop al primer precio observado",source);
  if (open >= target) return realized(stake,entry,target,start,"Objetivo límite modelado",source);
  if (low <= stop && high >= target) return unavailable("ambiguous","Ambas barreras dentro de la misma vela",source);
  if (low <= stop || high >= target) {
    const r = realized(stake,entry,low <= stop ? stop : target,null,
      low <= stop ? "Toque del stop en la vela" : "Toque del objetivo en la vela",source);
    return {...r,interval_start:iso(start),interval_end:iso(end)};
  }
  return null;
}

export function simulatePosition(pred, rawBars, quotes, options, now) {
  const entryAt = Date.parse(pred.created_at);
  const end = entryAt + options.days*DAY;
  const entry = positive(pred.price_at_prediction) ? Number(pred.price_at_prediction) : null;
  // Closed candles only. A candle already open at entry contributes its closing tick
  // only; its high/low may predate the signal. No post-horizon high can enter the peak.
  const candles = rawBars.filter(b => b.length === 6 && b[5] > entryAt && b[5] <= now)
    .map(b => b[0] < entryAt ? [b[5],b[4],b[4],b[4],b[4],b[5]] : b);
  const source = candles.length ? "Binance Alpha · velas 1 h" : "DEX · muestras por contrato";
  const tolerance = candles.length ? 1.5*HOUR : 18*HOUR;
  const ordered = candles.length ? candles : quotes.filter(q => positive(q.price))
    .map(q => [Date.parse(q.observed_at), Number(q.price)])
    .map(([t,p]) => [t,p,p,p,p,t]);
  const points = ordered.filter(b => b[5]>entryAt && b[5]<=now && b[5]<=end+tolerance).sort((a,b)=>a[5]-b[5]);
  const before = points.filter(b => b[5]<=end);
  const closing = points.find(b => b[5]>=end);
  const partialEntry = rawBars.find(b => b[0]<entryAt && b[5]>entryAt && b[5]<=Math.min(now,end));
  const partialEnd = closing && closing[0]<end && closing[5]>end ? closing : null;
  const sourceStatus = now < end+tolerance ? "pending" : "missing";
  const levels = entry == null ? {stop15:null,stop20:null,target30:null,target40:null}
    : {stop15:entry*.85,stop20:entry*.8,target30:entry*1.3,target40:entry*1.4};
  const strategies = {};
  for (const rule of RULES) {
    if (entry == null) {
      strategies[rule.key] = unavailable("missing","Sin precio inicial válido",source);
      continue;
    }
    if (rule.key === "hold") {
      strategies.hold = closing ? realized(options.stake,entry,closing[4],closing[5],"Primer cierre desde el vencimiento",source)
        : unavailable(sourceStatus,"Pendiente de precio dentro de la ventana de cierre",source);
      continue;
    }
    const mayHaveTouched = b => b && rule.stop && (b[3]<=entry*(1-rule.stop) ||
      (rule.target && b[2]>=entry*(1+rule.target)));
    if (mayHaveTouched(partialEntry)) {
      strategies[rule.key] = unavailable("ambiguous","El cruce pudo ocurrir antes o después de la entrada dentro de la vela",source);
      continue;
    }
    let last = entryAt, max = entry, maxTime = entryAt, maxInterval = null, resolved = null;
    for (const bar of before) {
      if (bar[5]-last>tolerance) {
        resolved = unavailable("missing","Serie interrumpida antes de resolver la estrategia",source);
        break;
      }
      last = bar[5];
      if (bar[2] > max) {
        max = bar[2];
        maxTime = bar[0] === bar[5] ? bar[5] : null;
        maxInterval = [iso(bar[0]),iso(bar[5])];
      }
      if (rule.stop) {
        resolved = trigger(bar,entry,rule,options.stake,source);
        if (resolved) break;
      }
    }
    if (!resolved && closing && closing[5]-last<=tolerance && before.length) {
      if (rule.key === "peak") {
        resolved = realized(options.stake,entry,max,maxTime,"Mejor marca registrada antes del vencimiento",source);
        if (maxInterval) [resolved.interval_start,resolved.interval_end] = maxInterval;
      } else if (mayHaveTouched(partialEnd)) {
        resolved = unavailable("ambiguous","El cruce pudo ocurrir antes o después del vencimiento dentro de la vela",source);
      } else {
        resolved = realized(options.stake,entry,closing[4],closing[5],"Salida temporal al vencimiento",source);
      }
    }
    strategies[rule.key] = resolved || unavailable(sourceStatus, "Falta cobertura suficiente hasta el vencimiento",source);
  }
  return {id:pred.id,token_key:pred.token_key,chain:pred.chain,symbol:pred.symbol,name:pred.name,verdict:pred.verdict,
    entry_at:pred.created_at,entry_price:entry,levels,strategies,source,observations:before.length};
}

export function aggregatePortfolio(positions,stake) {
  return RULES.map(({key,label})=>{
    const rows = positions.map(p=>p.strategies[key]);
    const settled = rows.filter(r=>r.status==="settled");
    const cost = money(settled.length*stake);
    const value = money(settled.reduce((sum,r)=>sum+r.value_usd,0));
    const complete = rows.length>0 && settled.length===rows.length;
    return {key,label,count:rows.length,settled:settled.length,allocated_usd:money(rows.length*stake),
      pending:rows.filter(r=>r.status==="pending").length,missing:rows.filter(r=>r.status==="missing").length,
      ambiguous:rows.filter(r=>r.status==="ambiguous").length,
      settled_capital_usd:cost,settled_value_usd:settled.length ? value : null,
      settled_pnl_usd:settled.length ? money(value-cost) : null,
      final_value_usd:complete ? value : null,pnl_usd:complete ? money(value-cost) : null};
  });
}

async function readPaths(db, selected, from, until) {
  const bars = new Map(), quotes = new Map();
  // Keep individual libSQL responses small; one 30-day OHLC history is ~50 KB.
  for (let i=0;i<selected.length;i+=12) {
    const group = selected.slice(i,i+12);
    const args = group.map(()=>"?").join(",");
    const keys = group.map(p=>p.token_key);
    const ids = group.map(p=>p.id);
    const [hist, ticks] = await Promise.all([
      db.all("SELECT prediction_id,bars FROM portfolio_paths WHERE prediction_id IN ("+args+")",ids),
      db.all("SELECT token_key,observed_at,price FROM portfolio_quotes WHERE token_key IN ("+args+") AND observed_at BETWEEN ? AND ? " +
        "UNION ALL SELECT s.token_key,s.observed_at,s.price FROM survivor_observations s " +
        "LEFT JOIN shadow_captures c ON c.run_id=s.run_id WHERE (c.run_id IS NULL OR c.status IN ('captured','complete')) " +
        "AND s.source='dexscreener' AND s.token_key IN ("+args+") AND s.observed_at BETWEEN ? AND ? " +
        "UNION ALL SELECT token_key,observed_at,price FROM horizon_prices WHERE token_key IN ("+args+") AND observed_at BETWEEN ? AND ?",
        [...keys,from,until,...keys,from,until,...keys,from,until])
    ]);
    for (const p of hist) bars.set(p.prediction_id,JSON.parse(p.bars));
    for (const q of ticks) {
      if (!quotes.has(q.token_key)) quotes.set(q.token_key,[]);
      quotes.get(q.token_key).push(q);
    }
  }
  return {bars,quotes};
}

export async function getPortfolio(db,url,now=Date.now()) {
  const opts = portfolioOptions(url);
  const scope = "category='analyzed' AND "+CHAIN_SQL+(opts.chain === "all" ? " IS NOT NULL" : " = ?");
  const scopeArgs = opts.chain === "all" ? [] : [opts.chain];
  const weeks = await db.all("SELECT "+WEEK_SQL+" week,COUNT(*) count FROM predictions WHERE "+scope+
    " AND julianday(created_at)<=julianday(?) GROUP BY week ORDER BY week DESC", [...scopeArgs,iso(now)]);
  const today = new Date(now);
  const week = opts.week || weeks[0]?.week || iso(now-((today.getUTCDay()+6)%7)*DAY).slice(0,10);
  const start = Date.parse(week+"T00:00:00Z");
  const verdict = {strong:"Strong Opportunity",watchlist:"Watchlist"}[opts.cohort];
  const rows = await db.all("SELECT id,symbol,name,verdict,created_at,contract_address,price_at_prediction,source," +
    "current_price,price_checked_at,price_after,evaluated_at,"+CHAIN_SQL+" chain FROM predictions WHERE "+scope+
    " AND created_at>=? AND created_at<? AND julianday(created_at)<=julianday(?)"+(verdict?" AND verdict=?":"")+
    " ORDER BY julianday(created_at),id",[...scopeArgs,week,iso(start+7*DAY).slice(0,10),iso(now),...(verdict?[verdict]:[])]);
  const identities = new Set(), selected = [];
  let excludedIdentity = 0;
  for (const row of rows) {
    const address = String(row.contract_address || "").trim();
    if (!address) {excludedIdentity++;continue;}
    const key = row.chain+":"+(row.chain==="bsc" ? address.toLowerCase() : address);
    if (identities.has(key)) continue;
    identities.add(key);
    selected.push({...row,token_key:key});
  }
  const {bars,quotes} = await readPaths(db,selected,week,iso(start+(8+opts.days)*DAY).slice(0,10));
  const positions = selected.map(pred=>{
    const points = [...(quotes.get(pred.token_key)||[])];
    if (pred.source==="dexscreener") {
      points.push({price:pred.current_price,observed_at:pred.price_checked_at},
        {price:pred.price_after,observed_at:pred.evaluated_at});
    }
    return simulatePosition(pred,bars.get(pred.id)||[],points,opts,now);
  });
  return {...opts,week,weeks,as_of:iso(now),fee_bps:30,excluded_identity:excludedIdentity,
    positions,strategies:aggregatePortfolio(positions,opts.stake)};
}
