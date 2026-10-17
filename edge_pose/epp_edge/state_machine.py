"""Máquina de estados de bajo consumo del nodo de borde.

    IDLE ──movimiento──▶ PERSON_CHECK ──persona confirmada──▶ ACTIVE
      ▲                      │ sin persona / sin movimiento         │ sin persona >= absence_timeout
      └──────────────────────┘◀──────────────────────────────────────┘ (cierra episodio)

  IDLE          : solo MOG2 (dentro de la ROI) a `motion.sample_fps`. Ninguna red neuronal corre.
  PERSON_CHECK  : YOLOv8n-pose a 320 px cada `check_interval_s` mientras haya movimiento.
  ACTIVE        : modelo EPP a 640 px + pose a 320 px hasta `epp.max_fps`; asocia los EPP a cada
                  persona por sus puntos clave, valida la colocación (placement.py) y acumula
                  estadísticas por persona (track id) para el episodio.

La máquina no conoce cámara ni nube: recibe frames por `step()` y emite callbacks.
"""
from __future__ import annotations

import collections
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from .crops import crop_region, merge_dets, refine_person, shift_dets
from .detectors import Detection, EPPDetector, PoseDetector, blur_heads, draw
from .motion import MotionDetector
from .placement import EPP_LABELS, Person, PersonStats, evaluate_frame
from .storage import Storage, utc_now_iso

CLASS_IDS = {"Gorra": 0, "Guante": 1, "Mascarilla": 2}   # ids del contrato de evento (iguales a los del modelo)

log = logging.getLogger("epp.fsm")


class State(str, Enum):
    IDLE = "IDLE"
    PERSON_CHECK = "PERSON_CHECK"
    ACTIVE = "ACTIVE"


@dataclass
class Episode:
    id: str
    ts_inicio: float
    frames: int = 0
    persons: dict = field(default_factory=dict)            # track_id -> PersonStats
    personas_max: int = 1
    best_score: float = -1.0
    best_frame: Optional[np.ndarray] = None
    best_raw: Optional[np.ndarray] = None                  # frame sin anotar del mejor momento (nube 2.0.0)
    best_dets: list = field(default_factory=list)          # cajas normalizadas del mejor frame
    best_ts: float = 0.0
    conf_sum: dict = field(default_factory=dict)            # etiqueta -> suma de confianzas
    conf_n: dict = field(default_factory=dict)              # etiqueta -> número de detecciones
    conf_max: float = 0.0
    epp_ms_total: float = 0.0
    last_sample_ts: float = 0.0
    last_person_ts: float = 0.0
    clip: collections.deque = field(default_factory=lambda: collections.deque(maxlen=1))


class EPPStateMachine:
    def __init__(self, cfg: dict, storage: Storage, base_dir: Path,
                 on_frame: Optional[Callable[[np.ndarray, str], None]] = None,
                 on_episode_closed: Optional[Callable[[dict], None]] = None):
        self.cfg = cfg
        self.storage = storage
        self.base_dir = base_dir
        self.on_frame = on_frame
        self.on_episode_closed = on_episode_closed

        cam = cfg["camera"]
        self.roi = None
        roi_cfg = cam.get("roi")
        if roi_cfg:
            self.roi = [(int(x * cam["width"]), int(y * cam["height"])) for x, y in roi_cfg]

        m = cfg["motion"]
        self.motion = MotionDetector(m["min_area_ratio"], m["history"], m["var_threshold"], roi=self.roi,
                                     frame_size=(cam["width"], cam["height"]))
        self.motion_period = 1.0 / m["sample_fps"]
        self.motion_cooldown = m["cooldown_s"]

        p = cfg["person"]
        self.pose = PoseDetector(base_dir / p["model"], p["imgsz"], p["conf"], p.get("tracker", "bytetrack.yaml"))
        self.person_interval = p["check_interval_s"]
        self.person_confirm = p["confirm_frames"]
        self.absence_timeout = p["absence_timeout_s"]
        self.pose_every = max(1, int(p.get("pose_every", 1)))

        e = cfg["epp"]
        self.conf_high = float(e["conf"])
        self.epp = EPPDetector(base_dir / e["model"], e["imgsz"], float(e.get("conf_low", e["conf"])), e["iou"])
        self.required = list(e["required"])
        self.compliance_ratio = e["compliance_ratio"]
        self.min_eval_frames = int(e.get("min_eval_frames", 5))
        self.min_person_frames = int(e.get("min_person_frames", 5))
        self.epp_period = 1.0 / e["max_fps"]
        self.sample_every = e["sample_every_s"]
        cr = e.get("crop", {}) or {}
        self.crop_mode = str(cr.get("mode", "alternate")).lower()      # off | pose_only | alternate | always
        self.crop_enabled = self.crop_mode != "off"
        self.crop_merge_age = float(cr.get("merge_age_s", 0.4))
        self._prev_dets: list = []
        self._prev_dets_ts = 0.0
        self._prev_dets_mode = ""
        self.crop_expand = float(cr.get("expand", 1.35))
        self.crop_min = int(cr.get("min_size", 320))
        self.crop_max_persons = int(cr.get("max_persons", 2))
        self.crop_when_smaller_than = float(cr.get("when_person_height_below", 0.75))  # fracción de la altura del frame

        self.blur = cfg["privacy"]["blur_faces"]
        self.device_id = cfg["device"]["id"]
        self.zona = cfg["device"]["zona"]
        self.snapshots_dir = (base_dir / cfg["storage"]["snapshots_dir"]).resolve()
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.jpeg_q = cfg["storage"]["snapshot_jpeg_quality"]
        ev = cfg.get("evidence", {})
        self.clip_seconds = float(ev.get("clip_seconds", 0))
        self.clip_fps = float(ev.get("clip_fps", 4))
        self.clip_only_noncompliant = bool(ev.get("only_noncompliant", True))

        self.state = State.IDLE
        self.episode: Optional[Episode] = None
        self._last_motion_ts = 0.0
        self._last_motion_check = 0.0
        self._last_person_check = 0.0
        self._person_hits = 0
        self._last_epp_ts = 0.0
        self._last_persons: list[Person] = []
        self._last_dets: list[Detection] = []
        self._last_clip_ts = 0.0
        self._last_idle_emit = 0.0
        self._fps_ema = 0.0
        self._cycle = 0
        self.stats = collections.Counter()
        self.pose.warmup(); self.epp.warmup()
        log.info("FSM lista. Estado inicial IDLE (ROI=%s)", "sí" if self.roi else "no")

    # ------------------------------------------------------------------ util
    def _set_state(self, new: State) -> None:
        if new != self.state:
            log.info("Estado %s -> %s", self.state.value, new.value)
            self.stats[f"transiciones_{self.state.value}_{new.value}"] += 1
            self.state = new

    def _annotate(self, frame, dets, persons) -> np.ndarray:
        return draw(frame, dets, persons, status=self.state.value, fps=self._fps_ema, blur_faces=self.blur, roi=self.roi)

    def _emit(self, frame: np.ndarray, dets: list[Detection], persons: list[Person]) -> Optional[np.ndarray]:
        img = None
        if self.on_frame is not None:
            img = self._annotate(frame, dets, persons)
            self.on_frame(img, self.state.value)
        return img

    def _in_roi(self, p: Person) -> bool:
        if self.roi is None:
            return True
        cx, cy = p.center
        return cv2.pointPolygonTest(np.array(self.roi, dtype=np.int32), (float(cx), float(cy)), False) >= 0

    # ------------------------------------------------------------------ step
    def step(self, frame: np.ndarray, now: Optional[float] = None) -> None:
        now = now or time.time()
        if self.state == State.IDLE:
            self._step_idle(frame, now)
        elif self.state == State.PERSON_CHECK:
            self._step_person_check(frame, now)
        else:
            self._step_active(frame, now)

    def _check_motion(self, frame: np.ndarray, now: float) -> bool:
        if now - self._last_motion_check < self.motion_period:
            return now - self._last_motion_ts < self.motion_cooldown
        self._last_motion_check = now
        self.stats["frames_motion"] += 1
        if self.motion.detect(frame):
            self._last_motion_ts = now
            return True
        return now - self._last_motion_ts < self.motion_cooldown

    def _step_idle(self, frame: np.ndarray, now: float) -> None:
        if self._check_motion(frame, now):
            self._person_hits = 0
            self._last_person_check = 0.0
            self._set_state(State.PERSON_CHECK)
        elif self.on_frame is not None and now - self._last_idle_emit > 1.0:
            self._last_idle_emit = now
            self._emit(frame, [], [])

    def _step_person_check(self, frame: np.ndarray, now: float) -> None:
        if not self._check_motion(frame, now):
            self._set_state(State.IDLE)
            return
        if now - self._last_person_check < self.person_interval:
            return
        self._last_person_check = now
        self.stats["inferencias_pose"] += 1
        try:
            _found, persons = self.pose.has_person(frame)
        except Exception:  # noqa: BLE001 - frame corrupto, error del runtime NCNN, etc.
            log.exception("Fallo de inferencia de pose en PERSON_CHECK; se trata como 'sin persona'")
            persons = []
        persons = [p for p in persons if self._in_roi(p)]
        self._last_persons = persons
        self._emit(frame, [], persons)
        if persons:
            self._person_hits += 1
            if self._person_hits >= self.person_confirm:
                self._open_episode(now, len(persons))
                self._set_state(State.ACTIVE)
        else:
            self._person_hits = 0

    def _step_active(self, frame: np.ndarray, now: float) -> None:
        ep = self.episode
        assert ep is not None
        if now - self._last_epp_ts < self.epp_period:
            return
        dt = now - self._last_epp_ts if self._last_epp_ts else self.epp_period
        self._last_epp_ts = now
        self._fps_ema = 0.9 * self._fps_ema + 0.1 * (1.0 / max(dt, 1e-3)) if self._fps_ema else 1.0 / max(dt, 1e-3)
        self._cycle += 1

        try:
            self._active_cycle(frame, now, ep)
        except Exception:  # noqa: BLE001 - frame corrupto, error del runtime NCNN, etc.
            log.exception("Fallo de inferencia en el ciclo %d; se descarta este frame y se sigue",
                          self._cycle)

        if now - ep.last_person_ts >= self.absence_timeout:
            self._close_episode(now)
            self._set_state(State.IDLE)

    def _active_cycle(self, frame: np.ndarray, now: float, ep: Episode) -> None:
        """Un ciclo de pose + EPP + colocación, aislado en su propio método: si una inferencia falla
        (frame corrupto, error del runtime NCNN), `_step_active` lo registra y salta el ciclo en vez
        de tirar abajo todo el proceso (mismo criterio que `raspberrypi/epp_edge/inference.py`)."""
        # 1) personas + ids (pose sobre el frame completo, cada `pose_every` ciclos)
        if self._cycle % self.pose_every == 0 or not self._last_persons:
            self.stats["inferencias_pose"] += 1
            persons = [p for p in self.pose(frame, track=True) if self._in_roi(p)]
            self._last_persons = persons
        else:
            persons = self._last_persons

        # 2) recorte por persona: la pose se refina siempre en el recorte (la persona llena la entrada);
        #    el detector de EPP corre sobre el frame completo (mejor cofia/mascarilla) o sobre el recorte
        #    (mejor guantes a distancia) según el modo; en "alternate" se alternan y se fusionan las
        #    detecciones de dos ciclos consecutivos, con el costo de una sola inferencia por ciclo.
        h_frame = frame.shape[0]
        use_crop = (self.crop_enabled and 0 < len(persons) <= self.crop_max_persons
                    and all((p.box[3] - p.box[1]) < self.crop_when_smaller_than * h_frame for p in persons))
        crops = []
        if use_crop:
            for p in persons:
                X1, Y1, X2, Y2 = crop_region(p.box, frame.shape, self.crop_expand, self.crop_min)
                crop = frame[Y1:Y2, X1:X2]
                if crop.size == 0:
                    continue
                crops.append((p, crop, X1, Y1))
                if refine_person(p, self.pose(crop, track=False), X1, Y1):
                    self.stats["pose_refinada"] += 1
                self.stats["inferencias_pose"] += 1
        epp_on_crop = bool(crops) and (self.crop_mode == "always" or (self.crop_mode == "alternate" and self._cycle % 2 == 1))
        if epp_on_crop:
            dets_all: list[Detection] = []
            for _, crop, X1, Y1 in crops:
                dets_all += shift_dets(self.epp(crop), X1, Y1)
                ep.epp_ms_total += self.epp.last_ms
                self.stats["inferencias_epp"] += 1
            dets_now, mode = merge_dets(dets_all), "crop"
        else:
            dets_now, mode = self.epp(frame), "full"
            ep.epp_ms_total += self.epp.last_ms
            self.stats["inferencias_epp"] += 1
        if self.crop_mode == "alternate" and self._prev_dets_mode and self._prev_dets_mode != mode \
                and now - self._prev_dets_ts <= self.crop_merge_age:
            dets = merge_dets(list(dets_now) + list(self._prev_dets))
        else:
            dets = dets_now
        self._prev_dets, self._prev_dets_ts, self._prev_dets_mode = list(dets_now), now, mode
        if persons:
            ep.last_person_ts = now
            ep.personas_max = max(ep.personas_max, len(persons))
        elif dets:  # EPP visibles sin pose: evidencia débil de presencia
            ep.last_person_ts = now

        dets = evaluate_frame(persons, dets, self.conf_high)   # filtra detecciones débiles sin contexto
        ep.frames += 1
        for p in persons:
            ep.persons.setdefault(p.track_id, PersonStats(p.track_id)).add(p)
        self._last_dets = dets

        # snapshot representativo: frame con más EPP evaluados + más confianza
        n_eval = sum(1 for p in persons for k in EPP_LABELS if p.status.get(k) != "na")
        score = n_eval + len(set(d.label for d in dets)) + (sum(d.conf for d in dets) / max(len(dets), 1))
        img = self._emit(frame, dets, persons)
        for d in dets:
            ep.conf_sum[d.label] = ep.conf_sum.get(d.label, 0.0) + d.conf
            ep.conf_n[d.label] = ep.conf_n.get(d.label, 0) + 1
            ep.conf_max = max(ep.conf_max, d.conf)
        if score > ep.best_score:
            ep.best_score = score
            ep.best_frame = img if img is not None else self._annotate(frame, dets, persons)
            raw = frame.copy()
            ep.best_raw = blur_heads(raw, dets) if self.blur else raw
            h, w = frame.shape[:2]
            ep.best_dets = [{"clase_id": CLASS_IDS.get(d.label, d.cls_id), "nombre_clase": d.label,
                             "confianza": round(float(d.conf), 4),
                             "bbox_xyxy_norm": [round(d.x1 / w, 4), round(d.y1 / h, 4), round(d.x2 / w, 4), round(d.y2 / h, 4)],
                             "img_w": w, "img_h": h} for d in dets]
            ep.best_ts = now
        if self.clip_seconds > 0 and now - self._last_clip_ts >= 1.0 / self.clip_fps:
            self._last_clip_ts = now
            ep.clip.append(img if img is not None else self._annotate(frame, dets, persons))

        if now - ep.last_sample_ts >= self.sample_every:
            ep.last_sample_ts = now
            counts = collections.Counter(d.label for d in dets)
            self.storage.add_sample(ep.id, utc_now_iso(), dict(counts), len(persons),
                                    [[d.label, round(d.conf, 3), d.x1, d.y1, d.x2, d.y2] for d in dets],
                                    estados={f"P{p.track_id}": p.status for p in persons})

    # -------------------------------------------------------------- episodios
    def _open_episode(self, now: float, n_persons: int) -> None:
        ts_ms = int(now * 1000)
        ep = Episode(id=f"{self.device_id}-{ts_ms}", ts_inicio=now, personas_max=max(1, n_persons),
                     last_person_ts=now, last_sample_ts=0.0)
        if self.clip_seconds > 0:
            ep.clip = collections.deque(maxlen=int(self.clip_seconds * self.clip_fps))
        self.episode = ep
        self._last_epp_ts = 0.0
        self._cycle = 0
        self.storage.open_episode(ep.id, self.device_id, self.zona,
                                  datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds"))
        self.stats["episodios"] += 1
        log.info("Episodio abierto %s (personas=%d)", ep.id, n_persons)

    def _close_episode(self, now: float, reason: str = "gap") -> None:
        ep = self.episode
        if ep is None:
            return
        end = now - self.absence_timeout
        duration = max(0.0, end - ep.ts_inicio)

        # personas con suficientes frames; la "principal" es la de más frames (compatibilidad de columnas)
        stats = [s for s in ep.persons.values() if s.frames >= self.min_person_frames] or list(ep.persons.values())
        personas = [s.to_dict(self.required, self.compliance_ratio, self.min_eval_frames) for s in stats]
        principal = max(stats, key=lambda s: s.frames) if stats else None
        faltantes = sorted({f for d in personas for f in d["faltantes"]})
        mal = sorted({m for d in personas for m in d["mal_colocados"]})
        no_eval = sorted({n for d in personas for n in d["no_evaluables"]})
        cumple = int(bool(personas) and all(d["cumple"] for d in personas))
        ratios = {k: (principal.ratio_ok(k) if principal else None) for k in EPP_LABELS}
        confs = {k: (round(ep.conf_sum[k] / ep.conf_n[k], 3) if ep.conf_n.get(k) else None) for k in EPP_LABELS}
        n_det = sum(ep.conf_n.values())

        snapshot_path = clip_path = raw_path = None
        if ep.best_frame is not None:
            snapshot_path = self.snapshots_dir / f"{ep.id}.jpg"
            cv2.imwrite(str(snapshot_path), ep.best_frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_q])
        if ep.best_raw is not None:
            raw_path = self.snapshots_dir / f"{ep.id}_original.jpg"
            cv2.imwrite(str(raw_path), ep.best_raw, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if self.clip_seconds > 0 and len(ep.clip) >= 2 and (not cumple or not self.clip_only_noncompliant):
            clip_path = self.snapshots_dir / f"{ep.id}.mp4"
            h, w = ep.clip[0].shape[:2]
            vw = cv2.VideoWriter(str(clip_path), cv2.VideoWriter_fourcc(*"mp4v"), self.clip_fps, (w, h))
            for f in ep.clip:
                vw.write(f)
            vw.release()

        data = {
            "ts_fin": datetime.fromtimestamp(end, timezone.utc).isoformat(timespec="milliseconds"),
            "duracion_s": round(duration, 2), "frames": ep.frames, "personas_max": ep.personas_max,
            "ratio_gorra": ratios["Gorra"] and round(ratios["Gorra"], 3),
            "ratio_guante": ratios["Guante"] and round(ratios["Guante"], 3),
            "ratio_mascarilla": ratios["Mascarilla"] and round(ratios["Mascarilla"], 3),
            "conf_gorra": confs["Gorra"], "conf_guante": confs["Guante"], "conf_mascarilla": confs["Mascarilla"],
            "cumple": cumple, "faltantes": faltantes, "mal_colocados": mal, "no_evaluables": no_eval,
            "n_personas": len(personas), "personas": personas,
            "snapshot": str(snapshot_path) if snapshot_path else None,
            "clip": str(clip_path) if clip_path else None,
            "epp_fps": round(1000.0 * ep.frames / ep.epp_ms_total, 2) if ep.epp_ms_total else None,
            "snapshot_raw": str(raw_path) if raw_path else None,
            "best_dets": ep.best_dets,
            "best_ts": datetime.fromtimestamp(ep.best_ts or end, timezone.utc).isoformat(timespec="milliseconds"),
            "conf_mean": round(sum(ep.conf_sum.values()) / n_det, 4) if n_det else None,
            "conf_max": round(ep.conf_max, 4) if n_det else None,
            "closed_reason": reason,
        }
        self.storage.close_episode(ep.id, data)
        self.stats["episodios_cumplen" if cumple else "episodios_incumplen"] += 1
        log.info("Episodio cerrado %s: %.1fs, %d frames, personas=%d, cumple=%s faltantes=%s mal=%s no_eval=%s ratios=%s",
                 ep.id, duration, ep.frames, len(personas), bool(cumple), faltantes, mal, no_eval,
                 {k: (round(v, 2) if v is not None else None) for k, v in ratios.items()})
        self.episode = None
        self._fps_ema = 0.0
        self._last_persons = []
        if self.on_episode_closed:
            full = self.storage.get_episode(ep.id)
            if full:
                self.on_episode_closed(full)

    def flush(self, reason: str = "shutdown") -> None:
        if self.episode is not None:
            self._close_episode(time.time() + self.absence_timeout, reason=reason)
            self._set_state(State.IDLE)

    def status(self) -> dict:
        return {
            "state": self.state.value,
            "episode_id": self.episode.id if self.episode else None,
            "episode_frames": self.episode.frames if self.episode else 0,
            "personas": [{"id": p.track_id, **p.status} for p in self._last_persons] if self.state == State.ACTIVE else [],
            "fps_epp": round(self._fps_ema, 2),
            "ms_epp_avg": round(self.epp.avg_ms, 1),
            "ms_pose_avg": round(self.pose.avg_ms, 1),
            "motion_ratio": round(self.motion.last_ratio, 4),
            **{k: int(v) for k, v in self.stats.items()},
        }
