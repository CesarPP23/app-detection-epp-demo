"""Cálculo del resumen (KPIs, series y desglose). Funciones puras: sin I/O."""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

RANGE_DAYS = {"7d": 7, "30d": 30}


def sample_kpis(events: list[dict]) -> dict:
    """Confianza media y cobertura sobre una muestra de eventos (misma definición que compute_kpis
    del dashboard anterior)."""
    n = len(events)
    if not n:
        return {"n": 0, "avg_conf": None, "coverage": None}
    avg = sum(e.get("confianza_promedio") or 0.0 for e in events) / n
    ok = sum(1 for e in events if (e.get("capture") or {}).get("status") == "ok")
    return {"n": n, "avg_conf": avg, "coverage": ok / n}


def _top(counts: Counter) -> tuple[str | None, int]:
    if not counts:
        return None, 0
    name, count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return name, count


def _from_days(stats: dict[str, dict], days: list[date]) -> tuple[int, float, Counter]:
    events, seconds, by_class = 0, 0.0, Counter()
    for d in days:
        row = stats.get(d.isoformat())
        if not row:
            continue
        events += int(row.get("events") or 0)
        seconds += float(row.get("seconds") or 0.0)
        by_class.update({k: int(v) for k, v in (row.get("by_class") or {}).items()})
    return events, seconds, by_class


def _from_events(events: list[dict]) -> tuple[int, float, Counter]:
    by_class = Counter(e["nombre_clase"] for e in events if e.get("nombre_clase"))
    return len(events), sum(e.get("duration_seconds") or 0.0 for e in events), by_class


def build_summary(
    rng: str,
    *,
    now: datetime,
    tz_name: str,
    window_events: list[dict],
    window_complete: bool,
    stats: dict[str, dict],
) -> dict:
    """24h sale de la ventana de eventos en caché (partial=True si no la cubre); 7d y 30d salen de
    daily_stats (exactos). Confianza y cobertura siempre salen de la muestra de eventos en caché."""
    tz = ZoneInfo(tz_name)
    local_now = now.astimezone(tz)
    if rng == "24h":
        cur_start = now - timedelta(hours=24)
        prev_start = now - timedelta(hours=48)
        cur = [e for e in window_events if cur_start < e["end_ts"] <= now]
        prev = [e for e in window_events if prev_start < e["end_ts"] <= cur_start]
        oldest = window_events[-1]["end_ts"] if window_events else None
        covered = window_complete or (oldest is not None and oldest <= cur_start)
        prev_known = window_complete or (oldest is not None and oldest <= prev_start)
        c_events, c_seconds, c_classes = _from_events(cur)
        p_events, p_seconds, p_classes = _from_events(prev)
        partial = not covered
        hour0 = local_now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
        buckets = []
        for i in range(24):
            start = hour0 + timedelta(hours=i)
            end = start + timedelta(hours=1)
            inside = [e for e in cur if start <= e["end_ts"].astimezone(tz) < end]
            buckets.append(
                {
                    "bucket": start.astimezone(UTC),
                    "total": len(inside),
                    "by_class": dict(Counter(e["nombre_clase"] for e in inside if e.get("nombre_clase"))),
                }
            )
        s_cur, s_prev = cur, prev
    else:
        n = RANGE_DAYS[rng]
        today = local_now.date()
        cur_days = [today - timedelta(days=n - 1 - i) for i in range(n)]
        prev_days = [cur_days[0] - timedelta(days=n - i) for i in range(n)]
        c_events, c_seconds, c_classes = _from_days(stats, cur_days)
        p_events, p_seconds, p_classes = _from_days(stats, prev_days)
        partial = False
        prev_known = any(d.isoformat() in stats for d in prev_days)
        buckets = []
        for d in cur_days:
            row = stats.get(d.isoformat()) or {}
            start = datetime(d.year, d.month, d.day, tzinfo=tz)
            buckets.append(
                {
                    "bucket": start.astimezone(UTC),
                    "total": int(row.get("events") or 0),
                    "by_class": {k: int(v) for k, v in (row.get("by_class") or {}).items()},
                }
            )
        cur_start = datetime(cur_days[0].year, cur_days[0].month, cur_days[0].day, tzinfo=tz)
        prev_start = datetime(prev_days[0].year, prev_days[0].month, prev_days[0].day, tzinfo=tz)
        s_cur = [e for e in window_events if e["end_ts"] >= cur_start]
        s_prev = [e for e in window_events if prev_start <= e["end_ts"] < cur_start]

    sc, sp = sample_kpis(s_cur), sample_kpis(s_prev)
    top_name, top_count = _top(c_classes)
    prev_top, _ = _top(p_classes)
    kpis = {
        "events": {"value": c_events, "previous": p_events if prev_known else None},
        "epp_seconds": {"value": c_seconds, "previous": p_seconds if prev_known else None},
        "avg_confidence": {"value": sc["avg_conf"], "previous": sp["avg_conf"]},
        "capture_coverage": {"value": sc["coverage"], "previous": sp["coverage"]},
        "top_class": {"nombre_clase": top_name, "count": top_count, "previous_nombre_clase": prev_top},
    }
    by_class = [
        {"nombre_clase": k, "count": v} for k, v in sorted(c_classes.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {
        "range": rng,
        "timezone": tz_name,
        "generated_at": now,
        "partial": partial,
        "sample": {"events": sc["n"], "complete": window_complete},
        "kpis": kpis,
        "series": buckets,
        "by_class": by_class,
    }
