# edge_pose — nodo de borde con estimación de pose (tesis)

Nodo de la tesis «Diseño e implementación de sistema basado en visión artificial para la detección de uso de equipos de
protección personal en la industria de alimentos y bebidas» (UDEP, 2026). Usa YOLOv8n (3 clases: Cofia, Guante,
Mascarilla) + YOLOv8n-pose con ByteTrack, una máquina de estados con activación por persona, reglas de colocación por
persona y por mano, y registra cada presencia como un **episodio** con veredicto.

Publica en la nube 2.0.0 de este repositorio con `epp_edge/cloud_v2.py` (contrato v2 con UUIDv7, outbox idempotente,
lago de datos, Firestore, control remoto, salud y video bajo demanda).

- Guía completa: [`docs/INTEGRACION_NODO_POSE.md`](../docs/INTEGRACION_NODO_POSE.md).
- Revisión de Cesar antes de fusionar: [`CAMBIOS_CESAR.md`](CAMBIOS_CESAR.md) (qué se revisó y el
  único ajuste de código aplicado, con AS-IS/TO-BE).
- Entorno local (PC/Mac, no la Pi): `pip install -r edge_pose/requirements-dev.txt` en el venv `.venv-edge`.
- Pruebas: `python -m pytest tests -q` (15 pruebas; no necesitan Google Cloud ni modelos).
- Prueba de extremo a extremo sin nube: `cloud_v2.emulate_dir` en `epp_edge/config.yaml`.
- Instalación en la Pi: `bash install.sh` (systemd `epp-edge@<usuario>`, watchdog de 120 s).
