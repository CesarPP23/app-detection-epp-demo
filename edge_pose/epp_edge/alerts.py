"""Alertas inmediatas al cerrar un episodio con incumplimiento.

  - Telegram: mensaje + foto de evidencia (requiere bot_token y chat_id en config).
  - GPIO (Raspberry Pi): enciende un LED/buzzer N segundos (gpiozero; opcional).
  - Webhook genérico: POST JSON a una URL (integración con otros sistemas).

Todo se ejecuta en un hilo aparte para no bloquear la máquina de estados.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("epp.alerts")


class Alerter:
    def __init__(self, cfg: dict, base_dir: Path):
        a = cfg.get("alerts", {}) or {}
        self.enabled = bool(a.get("enabled", False))
        self.only_noncompliant = bool(a.get("only_noncompliant", True))
        tg = a.get("telegram", {}) or {}
        self.tg_token = self._read(base_dir, tg.get("bot_token_file")) or tg.get("bot_token", "")
        self.tg_chat = str(tg.get("chat_id", ""))
        self.webhook = a.get("webhook_url", "")
        self.gpio_pin = a.get("gpio_pin")
        self.gpio_seconds = float(a.get("gpio_seconds", 10))
        self._led = None
        if self.enabled and self.gpio_pin is not None:
            try:
                from gpiozero import LED  # solo existe en Raspberry Pi OS
                self._led = LED(int(self.gpio_pin))
            except Exception as e:
                log.warning("GPIO no disponible (%s); alerta luminosa desactivada", e.__class__.__name__)

    @staticmethod
    def _read(base_dir: Path, rel: Optional[str]) -> str:
        if not rel:
            return ""
        try:
            return (base_dir / rel).resolve().read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    # ------------------------------------------------------------------ api
    def on_episode(self, ep: dict) -> None:
        if not self.enabled:
            return
        if self.only_noncompliant and ep.get("cumple"):
            return
        threading.Thread(target=self._send, args=(ep,), name="alert", daemon=True).start()

    def _send(self, ep: dict) -> None:
        faltantes = ep.get("faltantes") or []
        mal = ep.get("mal_colocados") or []
        if isinstance(faltantes, str):
            faltantes = json.loads(faltantes or "[]")
        if isinstance(mal, str):
            mal = json.loads(mal or "[]")
        estado = "CUMPLE" if ep.get("cumple") else "INCUMPLIMIENTO"
        text = (f"[EPP] {estado} en {ep.get('zona', '')} ({ep.get('device_id', '')})\n"
                f"Inicio: {ep.get('ts_inicio', '')[:19].replace('T', ' ')} UTC · Duración: {ep.get('duracion_s', 0)} s · "
                f"Personas: {ep.get('n_personas') or ep.get('personas_max', 1)}\n"
                f"Sin EPP: {', '.join(faltantes) or '—'}\nMal colocado: {', '.join(mal) or '—'}")
        snap = ep.get("snapshot")
        if self.tg_token and self.tg_chat:
            self._telegram(text, snap)
        if self.webhook:
            self._post_webhook(ep)
        if self._led is not None:
            try:
                self._led.blink(on_time=0.5, off_time=0.5, n=int(self.gpio_seconds), background=True)
            except Exception as e:
                log.debug("gpio: %s", e)

    def _telegram(self, text: str, snapshot: Optional[str]) -> None:
        import requests
        base = f"https://api.telegram.org/bot{self.tg_token}"
        try:
            if snapshot and Path(snapshot).exists():
                with open(snapshot, "rb") as f:
                    r = requests.post(f"{base}/sendPhoto", data={"chat_id": self.tg_chat, "caption": text},
                                      files={"photo": f}, timeout=15)
            else:
                r = requests.post(f"{base}/sendMessage", data={"chat_id": self.tg_chat, "text": text}, timeout=15)
            if r.status_code != 200:
                log.warning("Telegram HTTP %s: %s", r.status_code, r.text[:120])
            else:
                log.info("Alerta Telegram enviada")
        except Exception as e:
            log.warning("Telegram no disponible: %s", e.__class__.__name__)

    def _post_webhook(self, ep: dict) -> None:
        import requests
        try:
            requests.post(self.webhook, json={k: v for k, v in ep.items() if k not in ("snapshot", "clip")}, timeout=10)
        except Exception as e:
            log.warning("Webhook no disponible: %s", e.__class__.__name__)
