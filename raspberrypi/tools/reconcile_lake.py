"""Reconciliación diaria del data lake (Fase 5.4): detecta eventos o capturas perdidos entre capas.

Para un día local (SITE_TIMEZONE) compara:
  1. daily_stats.events          (Firestore, dbraspberry)
  2. documentos de detecciones_confirmadas con end_ts en ese día
  3. objetos raw/events/.../dt=<día>/*.json     (sidecars)
  4. objetos raw/images/.../dt=<día>/*.jpg      (imágenes originales)
  5. filas de epp_lake.events para ese día      (BigQuery; informativo: se pone al día con el MERGE diario)

Criterio de coherencia: 1 = 2 = 3, y 4 = eventos con capture.status == "ok". Los event_id que faltan en
alguna capa se imprimen. La capa 5 solo genera avisos (el MERGE corre una vez al día).

Solo necesita permisos de LECTURA (se puede ejecutar desde cualquier PC con ADC):
    python raspberrypi/tools/reconcile_lake.py --date 2026-10-10 [--device raspberry-pi-01] [--skip-bq]
    python raspberrypi/tools/reconcile_lake.py --dry-run     # datos de prueba, sin tocar GCP

Código de salida: 0 coherente, 1 hay diferencias en las capas 1-4.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

PROJECT = "epp-detection-v1"
EVENT_ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(?:json|jpg)$")


class Sources(Protocol):
    """Origen de datos por capa. Los clientes reales y los falsos de los tests implementan lo mismo."""

    def daily_stats_events(self, device_id: str, day: date) -> int | None: ...
    def firestore_events(self, device_id: str, day: date) -> dict[str, str]:
        """{event_id: capture_status} de los documentos del día."""
        ...
    def lake_event_ids(self, device_id: str, day: date) -> set[str]: ...
    def lake_image_ids(self, device_id: str, day: date) -> set[str]: ...
    def bigquery_event_ids(self, device_id: str, day: date) -> set[str]: ...


@dataclass
class Report:
    device_id: str
    day: date
    counts: dict[str, int | None] = field(default_factory=dict)
    missing: dict[str, list[str]] = field(default_factory=dict)  # problemas de las capas 1-4
    warnings: dict[str, list[str]] = field(default_factory=dict)  # capa 5 (informativa)

    @property
    def ok(self) -> bool:
        return not self.missing

    def render(self) -> str:
        lines = [f"Reconciliación {self.device_id} · {self.day.isoformat()}"]
        for name, n in self.counts.items():
            lines.append(f"  {name:<34} {'—' if n is None else n}")
        for title, group in (("DIFERENCIAS", self.missing), ("AVISOS", self.warnings)):
            for what, ids in group.items():
                if isinstance(ids, str):
                    lines.append(f"[{title}] {what}")
                    continue
                lines.append(f"[{title}] {what}: {len(ids)}")
                lines.extend(f"    {i}" for i in sorted(ids))
        lines.append("RESULTADO: coherente" if self.ok else "RESULTADO: hay diferencias")
        return "\n".join(lines)


def reconcile(src: Sources, device_id: str, day: date, check_bigquery: bool = True) -> Report:
    docs = src.firestore_events(device_id, day)
    doc_ids = set(docs)
    stats = src.daily_stats_events(device_id, day)
    sidecars = src.lake_event_ids(device_id, day)
    images = src.lake_image_ids(device_id, day)
    expected_images = {eid for eid, status in docs.items() if status == "ok"}

    rep = Report(device_id, day)
    rep.counts = {
        "1 daily_stats.events": stats,
        "2 documentos de Firestore": len(doc_ids),
        "3 sidecars JSON en el lake": len(sidecars),
        "4 imágenes en el lake": len(images),
        "  (eventos con capture.status=ok)": len(expected_images),
    }

    if stats is not None and stats != len(doc_ids):
        rep.missing["daily_stats.events != documentos (diferencia de contadores)"] = f"{stats} vs {len(doc_ids)}"  # type: ignore[assignment]
    if stats is None and doc_ids:
        rep.missing["no existe el documento daily_stats del día"] = "hay eventos pero falta el resumen"  # type: ignore[assignment]
    _diff(rep.missing, "en Firestore pero sin sidecar en el lake", doc_ids - sidecars)
    _diff(rep.missing, "sidecar en el lake pero sin documento en Firestore", sidecars - doc_ids)
    _diff(rep.missing, "capture.status=ok pero sin imagen en el lake", expected_images - images)
    _diff(rep.missing, "imagen en el lake sin capture.status=ok en Firestore", images - expected_images)

    if check_bigquery:
        bq = src.bigquery_event_ids(device_id, day)
        rep.counts["5 filas en BigQuery (epp_lake.events)"] = len(bq)
        _diff(rep.warnings, "aún no están en BigQuery (se cargan con el MERGE diario)", sidecars - bq)
        _diff(rep.warnings, "en BigQuery sin sidecar en el lake", bq - sidecars)
    return rep


def _diff(target: dict, title: str, ids: set[str]) -> None:
    if ids:
        target[title] = sorted(ids)


# ---------------------------------------------------------------------------------------------
# Implementación real (solo lectura) y datos de prueba
# ---------------------------------------------------------------------------------------------


def local_day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


class GcpSources:
    """Lee de Firestore, GCS y BigQuery con ADC. Los clientes se pueden inyectar (tests)."""

    def __init__(self, events_db: Any, bucket: Any, bq: Any | None, tz_name: str = "America/Lima", project: str = PROJECT):
        self.events_db, self.bucket, self.bq, self.tz_name, self.project = events_db, bucket, bq, tz_name, project

    @classmethod
    def from_env(cls, project: str, lake_bucket: str, tz_name: str, with_bq: bool) -> "GcpSources":
        from google.cloud import bigquery, firestore, storage

        return cls(
            firestore.Client(project=project, database="dbraspberry"),
            storage.Client(project=project).bucket(lake_bucket),
            bigquery.Client(project=project, location="us-central1") if with_bq else None,
            tz_name,
            project,
        )

    def daily_stats_events(self, device_id: str, day: date) -> int | None:
        snap = self.events_db.collection("daily_stats").document(f"{device_id}_{day.isoformat()}").get()
        return int(snap.to_dict().get("events", 0)) if snap.exists else None

    def firestore_events(self, device_id: str, day: date) -> dict[str, str]:
        start, end = local_day_bounds(day, self.tz_name)
        query = (
            self.events_db.collection("detecciones_confirmadas")
            .where("device_id", "==", device_id)
            .where("end_ts", ">=", start)
            .where("end_ts", "<", end)
        )
        out: dict[str, str] = {}
        for doc in query.stream():
            data = doc.to_dict() or {}
            out[doc.id] = (data.get("capture") or {}).get("status", "missing")
        return out

    def _ids(self, prefix: str) -> set[str]:
        ids = set()
        for blob in self.bucket.list_blobs(prefix=prefix):
            m = EVENT_ID_RE.search(blob.name)
            if m:
                ids.add(m.group(1))
        return ids

    def lake_event_ids(self, device_id: str, day: date) -> set[str]:
        return self._ids(f"raw/events/device={device_id}/dt={day.isoformat()}/")

    def lake_image_ids(self, device_id: str, day: date) -> set[str]:
        return self._ids(f"raw/images/device={device_id}/dt={day.isoformat()}/")

    def bigquery_event_ids(self, device_id: str, day: date) -> set[str]:
        if self.bq is None:
            return set()
        from google.cloud import bigquery

        job = self.bq.query(
            f"SELECT event_id FROM `{self.project}.epp_lake.events` "
            "WHERE device_id = @d AND DATE(end_ts, @tz) = @day",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("d", "STRING", device_id),
                    bigquery.ScalarQueryParameter("tz", "STRING", self.tz_name),
                    bigquery.ScalarQueryParameter("day", "DATE", day),
                ]
            ),
        )
        return {row["event_id"] for row in job.result()}


class SampleSources:
    """Datos de prueba para --dry-run: coherentes en las capas 1-4 y con un evento aún sin cargar a BigQuery."""

    IDS = [
        "0192f6c4-8a1e-7c3b-9f2d-4b8e1a2c3d4e",
        "0192f6c4-9b2f-7d4c-8a3e-5c9f2b3d4e5f",
        "0192f6c5-0c30-7e5d-9b4f-6d0a3c4e5f60",
    ]

    def daily_stats_events(self, device_id: str, day: date) -> int | None:
        return len(self.IDS)

    def firestore_events(self, device_id: str, day: date) -> dict[str, str]:
        return {i: "ok" for i in self.IDS}

    def lake_event_ids(self, device_id: str, day: date) -> set[str]:
        return set(self.IDS)

    def lake_image_ids(self, device_id: str, day: date) -> set[str]:
        return set(self.IDS)

    def bigquery_event_ids(self, device_id: str, day: date) -> set[str]:
        return set(self.IDS[:2])


def main(argv: list[str] | None = None, sources: Sources | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", help="día local YYYY-MM-DD (por defecto, ayer en SITE_TIMEZONE)")
    p.add_argument("--device", default="raspberry-pi-01")
    p.add_argument("--project", default=PROJECT)
    p.add_argument("--bucket", default=None, help="por defecto <proyecto>-lake")
    p.add_argument("--timezone", default="America/Lima")
    p.add_argument("--skip-bq", action="store_true", help="no consulta BigQuery (capa 5)")
    p.add_argument("--dry-run", action="store_true", help="usa datos de prueba; no toca GCP")
    args = p.parse_args(argv)

    if args.dry_run:
        sources = SampleSources()
    if sources is None:
        sources = GcpSources.from_env(args.project, args.bucket or f"{args.project}-lake", args.timezone, not args.skip_bq)
    day = date.fromisoformat(args.date) if args.date else (datetime.now(ZoneInfo(args.timezone)).date() - timedelta(days=1))

    report = reconcile(sources, args.device, day, check_bigquery=not args.skip_bq)
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
