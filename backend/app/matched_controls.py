"""Comparación emparejada en sombra sobre decisiones y cotizaciones fechadas."""
import json
import math
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from statistics import median

METHOD = "liquidez-v1"
HORIZONS = (7, 30, 90, 180)
MAX_RATIO = 2.0
START = datetime(2026, 11, 26, tzinfo=timezone.utc)


def positive(raw):
    try:
        n = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) and n > 0 else None


def match_chain(selected, controls):
    """Asignación ordenada 1:1 que maximiza pares y minimiza la distancia logarítmica total."""
    order = lambda x: (x["liquidity"], x["key"])
    selected, controls = sorted(selected, key=order), sorted(controls, key=order)
    # Cada celda contiene (-cantidad, distancia total, pares); la última componente desempata.
    previous = [(0, 0., ())] * (len(controls)+1)
    for pick in selected:
        current = [(0, 0., ())]
        for j, other in enumerate(controls, 1):
            best = min(previous[j], current[j-1])
            ratio = max(pick["liquidity"], other["liquidity"]) / min(pick["liquidity"], other["liquidity"])
            if ratio <= MAX_RATIO:
                count, distance, pairs = previous[j-1]
                best = min(best, (count-1, distance+math.log(ratio), pairs+((pick["key"],other["key"]),)))
            current.append(best)
        previous = current
    return dict(previous[-1][2])


def freeze(data):
    assets = {}
    for key, record in data["records"].items():
        if not record["eligible"]:
            continue
        token = record["token"]
        assets[key] = dict(key=key, symbol=token.get("symbol"), price=positive(token.get("price")),
                           liquidity=positive(token.get("liquidity")))
    arms = data["arms"]
    pairs = []
    for chain in ("bsc", "solana"):
        group = [a for a in assets.values() if a["key"].startswith(chain+":")]
        picks = [a for a in group if a["key"] in arms]
        controls = [a for a in group if a["key"] not in arms and a["liquidity"]]
        assignments = match_chain([p for p in picks if p["liquidity"]], controls)
        for pick in sorted(picks, key=lambda a:a["key"]):
            control = assets.get(assignments.get(pick["key"]))
            pairs.append(dict(chain=chain, arm=arms[pick["key"]], pick=pick, control=control))
    return pairs


def day(stamp):
    return datetime.fromisoformat(stamp).astimezone(timezone.utc)


def synchronize(db, now):
    """Congela entradas una vez y toma la primera captura conjunta de cada ventana."""
    captures = db.execute("SELECT c.run_id,c.observed_at,c.version,m.run_id AS frozen FROM shadow_captures c "
                          "LEFT JOIN matched_runs m ON m.run_id=c.run_id AND m.method=? "
                          "WHERE c.status IN ('captured','complete') AND c.observed_at<=? ORDER BY c.observed_at,c.run_id",
                          (METHOD,now.isoformat())).fetchall()
    stamps = [day(r["observed_at"]) for r in captures]
    cache = {}
    def archive(run_id):
        if run_id not in cache:
            raw = db.execute("SELECT payload FROM shadow_captures WHERE run_id=?", (run_id,)).fetchone()
            cache[run_id] = json.loads(raw["payload"])
        return cache[run_id]
    for row in captures:
        if row["frozen"] is None:
            data = archive(row["run_id"])
            db.execute("INSERT OR IGNORE INTO matched_runs VALUES (?,?,?,?,?,?)",
                       (row["run_id"],METHOD,row["observed_at"],row["version"],data.get("event","unknown"),
                        json.dumps(freeze(data),separators=(",",":"))))
    runs = db.execute("SELECT * FROM matched_runs WHERE method=? AND observed_at<=? ORDER BY observed_at,run_id",
                      (METHOD,now.isoformat())).fetchall()
    closures = {(r["run_id"],r["pick_key"],r["days"]):dict(r) for r in db.execute(
        "SELECT * FROM matched_closes WHERE method=? AND closed_at<=?",(METHOD,now.isoformat())).fetchall()}
    checked = {(r["run_id"],r["days"]):r["capture_count"] for r in db.execute(
        "SELECT run_id,days,capture_count FROM matched_windows WHERE method=?", (METHOD,)).fetchall()}
    parsed = []
    for row in runs:
        pairs = json.loads(row["pairs"])
        parsed.append((dict(row),pairs))
        for horizon in HORIZONS:
            target = day(row["observed_at"]) + timedelta(days=horizon)
            if target > now:
                continue
            pending = {p["pick"]["key"]:p["control"]["key"] for p in pairs if p["control"] and
                       (row["run_id"],p["pick"]["key"],horizon) not in closures}
            i = bisect_left(stamps,target)
            stop = bisect_right(stamps, target+timedelta(days=1))
            count = stop-i
            expired = now >= target+timedelta(days=1)
            if expired and checked.get((row["run_id"],horizon)) == count:
                continue
            while pending and i < stop:
                closing = captures[i]
                records = archive(closing["run_id"])["records"]
                for pick, other in list(pending.items()):
                    a = records.get(pick,{}).get("token",{})
                    b = records.get(other,{}).get("token",{})
                    prices = positive(a.get("price")), positive(b.get("price"))
                    if None in prices:
                        continue
                    values = (row["run_id"],METHOD,pick,horizon,closing["run_id"],closing["observed_at"],
                              *prices,positive(a.get("liquidity")),positive(b.get("liquidity")))
                    db.execute("INSERT OR IGNORE INTO matched_closes VALUES (?,?,?,?,?,?,?,?,?,?)",values)
                    closures[row["run_id"],pick,horizon] = dict(pick_price=prices[0],control_price=prices[1],
                        pick_depth=values[-2],control_depth=values[-1])
                    del pending[pick]
                i += 1
            if expired:
                db.execute("INSERT INTO matched_windows VALUES(?,?,?,?) ON CONFLICT(run_id,method,days) "
                           "DO UPDATE SET capture_count=excluded.capture_count",(row["run_id"],METHOD,horizon,count))
    return parsed,closures


def report(db, now, net_return):
    runs, closes = synchronize(db, now)
    totals = {}
    examples = []
    latest = latest_scheduled(runs)
    for row, pairs in runs:
        if row["event"] != "schedule":
            continue
        monday = day(row["observed_at"]).date()
        week = (monday-timedelta(days=monday.weekday())).isoformat()
        origin = "prospectivo" if day(row["observed_at"]) >= START else "reconstruido"
        for pair in pairs:
            pick, control = pair["pick"], pair["control"]
            if row["run_id"] == latest:
                examples.append(dict(cadena=pair["chain"],grupo=pair["arm"],seleccion=pick["key"],alternativa=control["key"] if control else None,
                                     simbolo=pick["symbol"],simbolo_alternativa=control["symbol"] if control else None,
                                     liquidez=pick["liquidity"],liquidez_alternativa=control["liquidity"] if control else None))
            for h in HORIZONS:
                key = (week,origin,row["version"],pair["chain"],pair["arm"],h)
                if key not in totals:
                    totals[key] = dict(selecciones=0,sin_par=0,pendientes=0,sin_precio=0,sin_costos=0,diferencias=[])
                bucket = totals[key]
                bucket["selecciones"] += 1
                if not control:
                    bucket["sin_par"] += 1
                    continue
                if now < day(row["observed_at"])+timedelta(days=h+1):
                    bucket["pendientes"] += 1
                    continue
                close = closes.get((row["run_id"],pick["key"],h))
                if not close or not pick["price"] or not control["price"]:
                    bucket["sin_precio"] += 1
                    continue
                a = net_return(close["pick_price"]/pick["price"],pick["liquidity"],close["pick_depth"],100,.003)
                b = net_return(close["control_price"]/control["price"],control["liquidity"],close["control_depth"],100,.003)
                if a is None or b is None:
                    bucket["sin_costos"] += 1
                else:
                    bucket["diferencias"].append(a-b)
    rows = []
    for (week,origin,version,chain,arm,h), bucket in sorted(totals.items()):
        diffs = bucket.pop("diferencias")
        rows.append(dict(semana=week,origen=origin,version=version,cadena=chain,grupo=arm,dias=h,medidos=len(diffs),
                         mediana_diferencia_pp=median(diffs) if diffs else None,**bucket))
    return dict(metodo=METHOD,filas=rows,ejemplos=examples,ultima_programada=latest)


def latest_scheduled(runs):
    return next((r["run_id"] for r,_ in reversed(runs) if r["event"]=="schedule"),None)
