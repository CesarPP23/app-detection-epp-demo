#!/usr/bin/env bash
# Instalador del nodo de borde en Raspberry Pi 5 (Raspberry Pi OS Bookworm 64-bit).
#
# Uso (desde el propio Pi, como el usuario normal, NO root):
#   bash /boot/firmware/epp-edge/install.sh          # si el paquete se copió a la partición boot
#   bash ~/epp-edge/install.sh                       # si se copió por scp
#
# Qué hace:
#   1. Instala dependencias del sistema (libcamera/v4l2, OpenCV runtime, etc.)
#   2. Copia el paquete a ~/epp-edge (si se ejecuta desde otra ruta)
#   3. Crea un venv con acceso a paquetes del sistema y pip-instala requirements-pi.txt
#   4. Prueba que los modelos NCNN cargan
#   5. Instala y habilita el servicio systemd epp-edge@<usuario>
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
DEST="/home/$USER_NAME/epp-edge"

echo "== [1/5] Paquetes del sistema =="
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-pip python3-dev git v4l-utils \
  libatlas-base-dev libopenblas0 libjpeg62-turbo libgl1 libglib2.0-0 libgomp1 \
  libavcodec59 libavformat59 libswscale6 2>/dev/null || \
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-pip python3-dev git v4l-utils libopenblas0 libjpeg62-turbo libgl1 libglib2.0-0 libgomp1

echo "== [2/5] Copiando paquete a $DEST =="
if [ "$SRC_DIR" != "$DEST" ]; then
  mkdir -p "$DEST"
  rsync -a --delete --exclude '.venv' --exclude 'data' --exclude 'secrets' "$SRC_DIR/" "$DEST/"
fi
mkdir -p "$DEST/data" "$DEST/secrets" "$DEST/data/.yolo"
cd "$DEST"

echo "== [3/5] Entorno virtual =="
if [ ! -x .venv/bin/python ]; then
  python3 -m venv --system-site-packages .venv
fi
.venv/bin/pip install --upgrade pip wheel -q
# torch no es necesario para inferencia NCNN, pero ultralytics lo importa: se instala la rueda CPU (ARM64)
.venv/bin/pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -q -r requirements-pi.txt pytest

echo "== [4/5] Prueba de carga de modelos (los que indica config.yaml) =="
YOLO_CONFIG_DIR="$DEST/data/.yolo" .venv/bin/python - <<'PY'
import time, yaml, numpy as np
from pathlib import Path
from ultralytics import YOLO
cfg = yaml.safe_load(open("epp_edge/config.yaml", encoding="utf-8"))
base = Path("epp_edge")
checks = [(base / cfg["person"]["model"], cfg["person"]["imgsz"], "pose"), (base / cfg["epp"]["model"], cfg["epp"]["imgsz"], "detect")]
img = np.zeros((480, 640, 3), np.uint8)
for path, sz, task in checks:
    y = YOLO(str(path.resolve()), task=task)
    y.predict(img, imgsz=sz, verbose=False)
    t = time.perf_counter(); [y.predict(img, imgsz=sz, verbose=False) for _ in range(5)]
    print(f"  {path.name}: {(time.perf_counter()-t)/5*1000:.0f} ms/inferencia ({task}, {sz} px)")
try:
    import lap  # noqa: F401  (ByteTrack)
    print("  lap OK (ByteTrack disponible)")
except Exception:
    print("  AVISO: 'lap' no instalado; el seguimiento usará ids por orden (sin ByteTrack)")
PY
cd "$DEST" && .venv/bin/python -m pytest tests -q 2>/dev/null | tail -1 || true

# Nube 2.0.0 (cloud_v2): clave de la cuenta de servicio raspberry-pi-worker y DEVICE_TOKEN de Secret Manager
if [ ! -s secrets/gcp-key.json ]; then
  echo "AVISO: falta secrets/gcp-key.json (clave de la cuenta raspberry-pi-worker). Sin ella los episodios quedan en la base local."
fi
if [ ! -s secrets/device_token.txt ]; then
  echo "AVISO: falta secrets/device_token.txt (DEVICE_TOKEN de Secret Manager). Sin él no hay video en vivo."
fi
chmod 600 secrets/* 2>/dev/null || true

echo "== [5/5] Servicio systemd =="
sudo cp epp-edge.service /etc/systemd/system/epp-edge@.service
# libcamera/v4l2: comprobar que la webcam existe
ls /dev/video0 >/dev/null 2>&1 || echo "AVISO: no se detecta /dev/video0 (¿webcam USB conectada?)"
sudo systemctl daemon-reload
sudo systemctl enable "epp-edge@$USER_NAME"
sudo systemctl restart "epp-edge@$USER_NAME"
sleep 3
sudo systemctl --no-pager status "epp-edge@$USER_NAME" | head -12

IP=$(hostname -I | awk '{print $1}')
echo
echo "Instalación completa."
echo "  Vista local : http://$IP:8080/"
echo "  Logs        : journalctl -u epp-edge@$USER_NAME -f"
echo "  Config      : $DEST/epp_edge/config.yaml"
