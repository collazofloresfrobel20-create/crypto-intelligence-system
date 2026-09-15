/**
 * Puerto fiel de backend/app/backtesting.py (solo las funciones de solo-lectura: la
 * evaluación real contra precio de mercado sigue viviendo en Python/GitHub Actions, para no
 * mantener la misma lógica de negocio duplicada en dos lenguajes). Si cambias una fórmula
 * aquí, cámbiala también allá.
 */
import { rowWithJson } from "./db.js";

const round = (n, d = 2) => Math.round(n * 10 ** d) / 10 ** d;

function confidenceBucket(score) {
  if (score === null || score === undefined) return "desconocido";
  if (score >= 90) return "90-100";
  if (score >= 75) return "75-89";
  if (score >= 60) return "60-74";
  if (score >= 40) return "40-59";
  return "<40";
}

export async function getPerformanceStats(db) {
  const evaluatedRaw = await db.all(
    "SELECT * FROM predictions WHERE status = 'evaluated' AND category = 'analyzed'"
  );
  const evaluated = evaluatedRaw.map(rowWithJson);
  const totalRow = await db.one("SELECT COUNT(*) c FROM predictions WHERE category = 'analyzed'");
  const totalAnalyzed = totalRow.c;

  const n = evaluated.length;
  if (n === 0) {
    return {
      total_predictions: totalAnalyzed,
      evaluated_predictions: 0,
      message: "Aún no hay predicciones evaluadas (esperando que venza el horizonte de 7 días).",
    };
  }

  const reached20 = evaluated.filter((p) => (p.max_return_pct || 0) >= 20).length;
  const reached30 = evaluated.filter((p) => (p.max_return_pct || 0) >= 30).length;
  const avgMaxReturn = evaluated.reduce((s, p) => s + (p.max_return_pct || 0), 0) / n;
  const avgDrawdown = evaluated.reduce((s, p) => s + (p.max_drawdown_pct || 0), 0) / n;

  const buckets = {};
  for (const p of evaluated) {
    const b = confidenceBucket(p.confidence_score);
    buckets[b] = buckets[b] || { n: 0, yes: 0, partial: 0, no: 0 };
    buckets[b].n += 1;
    const result = p.thesis_result || "no";
    buckets[b][result] = (buckets[b][result] || 0) + 1;
  }

  const accuracyByConfidence = {};
  for (const [b, v] of Object.entries(buckets)) {
    accuracyByConfidence[b] = {
      n: v.n,
      success_rate_pct: v.n ? round((100 * (v.yes + 0.5 * v.partial)) / v.n, 1) : 0,
    };
  }

  return {
    total_predictions: totalAnalyzed,
    evaluated_predictions: n,
    pct_reached_plus20: round((100 * reached20) / n, 1),
    pct_reached_plus30: round((100 * reached30) / n, 1),
    avg_max_return_pct: round(avgMaxReturn, 2),
    avg_max_drawdown_pct: round(avgDrawdown, 2),
    accuracy_by_confidence_bucket: accuracyByConfidence,
  };
}

export async function getFilterEfficacyStats(db) {
  const analyzed = (
    await db.all("SELECT * FROM predictions WHERE status='evaluated' AND category='analyzed'")
  ).map(rowWithJson);
  const discarded = (
    await db.all("SELECT * FROM predictions WHERE status='evaluated' AND category='discarded'")
  ).map(rowWithJson);

  const summarize = (rows) => {
    const n = rows.length;
    if (n === 0) return { n: 0, pct_reached_plus20: null, avg_max_return_pct: null };
    const reached = rows.filter((r) => (r.max_return_pct || 0) >= 20).length;
    const avg = rows.reduce((s, r) => s + (r.max_return_pct || 0), 0) / n;
    return { n, pct_reached_plus20: round((100 * reached) / n, 1), avg_max_return_pct: round(avg, 2) };
  };

  const missedReasons = {};
  for (const r of discarded) {
    if ((r.max_return_pct || 0) >= 20) {
      for (const reason of r.rejection_reasons || []) {
        missedReasons[reason] = (missedReasons[reason] || 0) + 1;
      }
    }
  }
  const topMissed = Object.fromEntries(
    Object.entries(missedReasons)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
  );

  return {
    analyzed: summarize(analyzed),
    discarded: summarize(discarded),
    missed_opportunities_reasons: topMissed,
  };
}

const HYPOTHETICAL_STAKE_USD = 100.0;
const MIN_SAMPLE_FOR_CONFIDENCE = 20;

export async function getHistoryOverview(db) {
  const totalUpdates = (await db.one("SELECT COUNT(*) c FROM pipeline_runs")).c;
  const dateRange = await db.one(
    "SELECT MIN(created_at) first_at, MAX(created_at) last_at FROM predictions WHERE category='analyzed'"
  );
  const totalAnalyzed = (
    await db.one("SELECT COUNT(*) c FROM predictions WHERE category='analyzed'")
  ).c;
  const evaluated = (
    await db.all("SELECT * FROM predictions WHERE category='analyzed' AND status='evaluated'")
  ).map(rowWithJson);

  const n = evaluated.length;
  if (n === 0) {
    return {
      total_updates_run: totalUpdates,
      total_analyzed: totalAnalyzed,
      total_evaluated: 0,
      first_analyzed_at: dateRange.first_at,
      last_analyzed_at: dateRange.last_at,
      message: "Aún no hay predicciones evaluadas (esperan a que venza su horizonte de 7 días).",
    };
  }

  const hits = evaluated.filter((p) => p.thesis_result === "yes").length;
  const partials = evaluated.filter((p) => p.thesis_result === "partial").length;
  const overallHitRatePct = round((100 * (hits + 0.5 * partials)) / n, 1);
  const avgTimeToMaxHours = evaluated.reduce((s, p) => s + (p.time_to_max_hours || 0), 0) / n;
  // avgTimeToMaxHours mezcla exitos y fracasos (el "maximo" de algo que nunca llego a +20% no
  // es tiempo-a-la-meta, es solo su pico real). Esta otra solo cuenta los casos que SI
  // llegaron -- es la unica cifra honesta de "cuanto tarda cuando funciona".
  const hitRows = evaluated.filter((p) => p.thesis_result === "yes");
  const avgTimeToTargetHours = hitRows.length
    ? round(hitRows.reduce((s, p) => s + (p.time_to_max_hours || 0), 0) / hitRows.length, 1)
    : null;

  const byVerdict = {};
  for (const p of evaluated) {
    const v = p.verdict || "N/A";
    byVerdict[v] = byVerdict[v] || [];
    byVerdict[v].push(p);
  }

  const verdictSummary = (rows) => {
    const m = rows.length;
    const h = rows.filter((r) => r.thesis_result === "yes").length;
    const pa = rows.filter((r) => r.thesis_result === "partial").length;
    return {
      n: m,
      success_rate_pct: round((100 * (h + 0.5 * pa)) / m, 1),
      avg_max_return_pct: round(rows.reduce((s, r) => s + (r.max_return_pct || 0), 0) / m, 2),
      avg_return_at_horizon_pct: round(rows.reduce((s, r) => s + (r.return_pct || 0), 0) / m, 2),
    };
  };

  const accuracyByVerdict = {};
  for (const [v, rows] of Object.entries(byVerdict)) accuracyByVerdict[v] = verdictSummary(rows);

  const best = evaluated.reduce((a, b) => ((a.max_return_pct ?? -999) >= (b.max_return_pct ?? -999) ? a : b));
  const worst = evaluated.reduce((a, b) => ((a.max_return_pct ?? 999) <= (b.max_return_pct ?? 999) ? a : b));

  const stake = HYPOTHETICAL_STAKE_USD;
  const totalStaked = stake * n;
  const holdValue = evaluated.reduce((s, p) => s + stake * (1 + (p.return_pct || 0) / 100), 0);
  const bestCaseValue = evaluated.reduce((s, p) => s + stake * (1 + (p.max_return_pct || 0) / 100), 0);

  return {
    total_updates_run: totalUpdates,
    total_analyzed: totalAnalyzed,
    total_evaluated: n,
    first_analyzed_at: dateRange.first_at,
    last_analyzed_at: dateRange.last_at,
    overall_hit_rate_pct: overallHitRatePct,
    avg_time_to_max_hours: round(avgTimeToMaxHours, 1),
    avg_time_to_target_hours: avgTimeToTargetHours,
    hit_count_for_timing: hitRows.length,
    low_sample_warning: n < MIN_SAMPLE_FOR_CONFIDENCE,
    min_sample_for_confidence: MIN_SAMPLE_FOR_CONFIDENCE,
    accuracy_by_verdict: accuracyByVerdict,
    best_call: { symbol: best.symbol, max_return_pct: best.max_return_pct },
    worst_call: { symbol: worst.symbol, max_return_pct: worst.max_return_pct },
    hypothetical_simulation: {
      stake_per_pick_usd: stake,
      total_staked_usd: round(totalStaked, 2),
      hold_to_horizon_value_usd: round(holdValue, 2),
      hold_to_horizon_return_pct: round((100 * (holdValue - totalStaked)) / totalStaked, 2),
      best_case_value_usd: round(bestCaseValue, 2),
      best_case_return_pct: round((100 * (bestCaseValue - totalStaked)) / totalStaked, 2),
      disclaimer:
        "Simulación educativa con capital hipotético igual en cada predicción analizada. " +
        "No es una recomendación de inversión ni garantía de resultados futuros.",
    },
  };
}
