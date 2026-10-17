# Cambios de Cesar sobre la integración (revisión del 2026-10-16)

**Para quién es:** Marcelo. Resume qué revisé de `edge_pose/` antes de fusionar la rama
`integracion/nodo-pose-episodios` a `remediacion/auditoria-2026-10`, y el único cambio de código
que aplicué, con el estado anterior (AS-IS) y el nuevo (TO-BE).

## Qué revisé

Leí completos `epp_edge/cloud_v2.py` y `epp_edge/state_machine.py` (los dos archivos nuevos/más
grandes de la integración), más `storage.py`, `main.py` y los 8 tests de `test_cloud_v2.py`. **Sin
hallazgos de seguridad ni de corrección que bloqueen fusionar.** `cloud_v2.py` reutiliza bien los
patrones ya auditados de `raspberrypi/outbox.py` (outbox idempotente, UUIDv7, create-only con
`if_generation_match=0`, batch atómico de Firestore con `Conflict` absorbido) y el truco de
representar el episodio como `episodio_conforme`/`episodio_no_conforme` para que el panel lo
muestre sin tocar el backend es elegante y está probado (`test_contrato_compatible_con_la_api`).

El único cambio que aplicó código fue el de abajo. Otros hallazgos menores que **no** toqué (quedan
para quien quiera atenderlos después, ninguno bloquea usar el nodo):

- `_step_active`: si el modelo de pose no confirma a nadie pero el modelo de EPP detecta algo (un
  falso positivo, un EPP suelto en cámara), se trata como "evidencia débil de presencia" y se
  resetea el timer de ausencia — un falso positivo persistente sin nadie en cámara podría mantener
  el episodio `ACTIVE` indefinidamente. Heredado del prototipo original, no de esta integración.
- El hilo `main` (que muta `self.episode`/`self._last_persons`/`self.stats`) y el hilo `local-http`
  (que los lee en cada request a `/status`) no comparten ningún lock. Acotado al endpoint de debug
  en LAN, no afecta la ruta de datos hacia la nube.
- `storage.purge_old_snapshots` es puramente por antigüedad del archivo, sin mirar si ese snapshot
  todavía está pendiente en `outbox_v2`. Solo importa con una caída de red de ≥30 días
  (`keep_days` default) con un episodio aún sin subir; el evento igual se publicaría con
  `capture.status="missing"` (nunca se descarta el evento, solo la imagen).
- `CLASS_IDS` está definido dos veces de forma independiente (`state_machine.py` y `cloud_v2.py`,
  mismo valor). Vale la pena unificarlo en un solo lugar en algún momento.

## AS-IS (antes del cambio)

En `_step_active` (y en `_step_person_check`), las llamadas a los modelos (`self.pose(...)`,
`self.epp(...)`) no estaban protegidas con `try/except`. Un fallo de inferencia (frame corrupto,
error del runtime NCNN) se propagaba sin capturar hasta `main.py` y mataba el proceso.

En la práctica esto estaba mitigado: el `finally` de `main.py` llama a `fsm.flush()` antes de
salir (no se pierde el episodio abierto) y `epp-edge.service` tiene `Restart=always` +
`RestartSec=5`. Pero el costo era un **reinicio completo del servicio por un solo frame malo**, en
vez de perder solo esa inferencia y seguir — que es lo que ya hace
`raspberrypi/epp_edge/inference.py` en este mismo repositorio.

```python
def _step_active(self, frame: np.ndarray, now: float) -> None:
    ep = self.episode
    assert ep is not None
    if now - self._last_epp_ts < self.epp_period:
        return
    ...
    self._cycle += 1

    # 1) personas + ids (pose sobre el frame completo, cada `pose_every` ciclos)
    if self._cycle % self.pose_every == 0 or not self._last_persons:
        persons = [p for p in self.pose(frame, track=True) if self._in_roi(p)]
        ...
    # 2) recorte por persona ...
    ...
    dets_now, mode = self.epp(frame), "full"
    ...
    # (todo el resto del ciclo: evaluate_frame, estadísticas, snapshot, muestra)
    ...
    if now - ep.last_person_ts >= self.absence_timeout:
        self._close_episode(now)
        self._set_state(State.IDLE)
```

## TO-BE (después del cambio)

Separé el cuerpo de `_step_active` en un método nuevo, `_active_cycle`, y lo envolví en
`try/except`: si una inferencia falla, se registra el error (`log.exception`) y se descarta **ese
ciclo** — no ese episodio, no el proceso. El chequeo de ausencia (que puede cerrar el episodio)
sigue corriendo siempre en `_step_active`, incluso si `_active_cycle` falló, así que un episodio
sigue cerrándose a tiempo aunque haya habido fallos de inferencia mientras tanto.

```python
def _step_active(self, frame: np.ndarray, now: float) -> None:
    ep = self.episode
    assert ep is not None
    if now - self._last_epp_ts < self.epp_period:
        return
    ...
    self._cycle += 1

    try:
        self._active_cycle(frame, now, ep)
    except Exception:  # frame corrupto, error del runtime NCNN, etc.
        log.exception("Fallo de inferencia en el ciclo %d; se descarta este frame y se sigue", self._cycle)

    if now - ep.last_person_ts >= self.absence_timeout:
        self._close_episode(now)
        self._set_state(State.IDLE)

def _active_cycle(self, frame: np.ndarray, now: float, ep: Episode) -> None:
    """Un ciclo de pose + EPP + colocación. Mismo cuerpo que antes, sin el chequeo de ausencia
    (ahora vive en `_step_active`, para que corra siempre aunque este método falle)."""
    # 1) personas + ids ...
    # 2) recorte por persona ...
    # (resto del cuerpo original, idéntico)
```

Mismo criterio aplicado en `_step_person_check`: si `self.pose.has_person(frame)` falla, el frame
se trata como "sin persona" (se registra el error y `_person_hits` se resetea, igual que si de
verdad no hubiera nadie) en vez de propagar la excepción.

**Cuando todo funciona bien, el comportamiento es idéntico al original** — no se tocó ninguna
regla de negocio, ningún umbral, ningún contrato de evento, ni `cloud_v2.py`.

## Cómo lo probé

`python -m pytest edge_pose/tests -q`: 13/13 en los tests que no dependen de escribir JPEG con
`cv2.imwrite` en una ruta con tilde. Los 2 tests de `test_cloud_v2.py` que sí lo hacen
(`test_contrato_compatible_con_la_api`, `test_publicacion_completa_e_idempotente`) fallan en mi
laptop por un problema conocido de OpenCV en Windows con rutas no-ASCII (mi usuario de Windows es
"César Pérez"; confirmé reproduciéndolo aparte con `cv2.imwrite`/`cv2.imread` directo sobre esa
misma ruta) — no tiene nada que ver con este cambio ni con `cloud_v2.py`, y no aplica en la Pi
(Linux) ni en CI.
