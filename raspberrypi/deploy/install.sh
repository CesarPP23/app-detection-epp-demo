#!/usr/bin/env bash
# Fase 6.2 - Instalacion idempotente de epp-edge en una Raspberry Pi (Raspberry Pi OS Lite 64-bit).
#
# Uso:
#   sudo bash raspberrypi/deploy/install.sh
#
# Puede ejecutarse varias veces: cada paso comprueba el estado actual antes de cambiar algo.
# No escribe ni genera secretos: la key de la cuenta de servicio y el DEVICE_TOKEN los coloca
# el operador a mano (ver docs/EDGE_DEPLOYMENT.md).
#
# Variables de entorno opcionales (usar `sudo -E` para que `apt`/`git`/etc. las vean):
#   REPO_URL            URL del repo a clonar en /opt/epp-edge/app.
#                        Por defecto: https://github.com/CesarPP23/app-detection-epp-demo.git
#   DEPLOY_BRANCH        Rama a clonar. Por defecto: main
#   SSH_ALLOW_FROM       CIDR desde el que ufw permite SSH (p. ej. 192.168.1.0/24).
#                        Por defecto: se intenta detectar la subred LAN de la Pi.
#   FORCE_SSH_KEYONLY=1  Desactiva la autenticacion por contrasena en sshd aunque no se haya
#                        encontrado ningun ~/.ssh/authorized_keys. Usar solo si ya existe otro
#                        acceso (p. ej. consola serie) para no quedar bloqueado.
#   INSTALL_TAILSCALE=1  Instala el cliente de Tailscale (opcional; no se activa solo, hay que
#                        correr `tailscale up` a mano).
#
# Pasos (numerados como en docs/PLAN_REMEDIACION.md Fase 6.2):
#   1. Paquetes del sistema.
#   2. Usuario de sistema `epp`.
#   3. Directorios de datos y de configuracion.
#   4. Codigo en /opt/epp-edge/app + venv con --system-site-packages.
#   5. /etc/epp-edge/edge.env a partir de la plantilla (solo si no existe).
#   6. Permisos de la key de la cuenta de servicio (no la crea: eso lo hace el operador).
#   7. Verificacion del modelo exportado (no lo genera: eso es la Fase 7, fuera de este script).
#   8. Unit de systemd.
#   9. journald con SystemMaxUse=200M.
#  10. Watchdog de hardware.
#  11. SSH solo con llave + ufw.
#  12. NTP.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/CesarPP23/app-detection-epp-demo.git}"
DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
APP_DIR=/opt/epp-edge/app
VENV=/opt/epp-edge/venv
SERVICE_NAME=epp-edge
UNIT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/epp-edge.service"
ENV_FILE=/etc/epp-edge/edge.env
ENV_TEMPLATE="$(dirname "$UNIT_SRC")/edge.env.template"
KEY_PATH=/etc/epp-edge/gcp-key.json
MODEL_DIR=/var/lib/epp-edge/models

export DEBIAN_FRONTEND=noninteractive

log() { printf '== %s\n' "$*"; }
warn() { printf 'ATENCION: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Este script instala paquetes del sistema y crea un usuario: ejecutalo con sudo."

# --- Paso 1: paquetes del sistema ------------------------------------------------------------
log "Paso 1/12: paquetes del sistema"
apt-get update -y
apt-get install -y python3-venv python3-picamera2 git unattended-upgrades

# --- Paso 2: usuario de sistema 'epp' --------------------------------------------------------
log "Paso 2/12: usuario de sistema 'epp'"
if id -u epp >/dev/null 2>&1; then
  log "El usuario 'epp' ya existe"
else
  useradd --system --home-dir /var/lib/epp-edge --shell /usr/sbin/nologin --groups video epp
  log "Usuario de sistema 'epp' creado (grupo video, sin shell)"
fi

# --- Paso 3: directorios de datos y de configuracion ---------------------------------------
log "Paso 3/12: directorios de datos y de configuracion"
install -d -o epp -g epp -m 0750 /var/lib/epp-edge /var/lib/epp-edge/models /var/lib/epp-edge/pending
install -d -o root -g epp -m 0750 /etc/epp-edge

# --- Paso 4: codigo + entorno virtual --------------------------------------------------------
log "Paso 4/12: codigo en $APP_DIR y entorno virtual"
if [[ -d "$APP_DIR/.git" ]]; then
  log "$APP_DIR ya es un clon de git; no se vuelve a clonar (usa update.sh para actualizarlo)"
else
  mkdir -p "$APP_DIR"
  if [[ -z "$(ls -A "$APP_DIR" 2>/dev/null)" ]]; then
    log "Clonando $REPO_URL (rama $DEPLOY_BRANCH) en $APP_DIR"
    git clone --branch "$DEPLOY_BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
  else
    die "$APP_DIR existe, no esta vacio y no es un repositorio git. Revisalo a mano antes de continuar."
  fi
fi
chown -R root:root /opt/epp-edge
chmod -R a+rX /opt/epp-edge/app

if [[ ! -x "$VENV/bin/python" ]]; then
  log "Creando el entorno virtual en $VENV (--system-site-packages, para usar el picamera2 de apt)"
  python3 -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$APP_DIR/raspberrypi/requirements.txt"
chown -R root:root "$VENV"
chmod -R a+rX "$VENV"

# --- Paso 5: edge.env a partir de la plantilla ----------------------------------------------
log "Paso 5/12: $ENV_FILE"
if [[ -f "$ENV_FILE" ]]; then
  log "$ENV_FILE ya existe; no se sobrescribe"
else
  install -o root -g epp -m 0640 "$ENV_TEMPLATE" "$ENV_FILE"
  warn "Se creo $ENV_FILE a partir de la plantilla con valores ficticios." \
    "Edita DEVICE_TOKEN, ORCHESTRATOR_HOST y el resto de variables antes de iniciar el servicio."
fi

# --- Paso 6: key de la cuenta de servicio ----------------------------------------------------
log "Paso 6/12: key de la cuenta de servicio ($KEY_PATH)"
if [[ -f "$KEY_PATH" ]]; then
  chown root:epp "$KEY_PATH"
  chmod 0640 "$KEY_PATH"
  log "Permisos de $KEY_PATH verificados (root:epp, 0640)"
else
  warn "Falta $KEY_PATH. Copia ahi la key de la cuenta de servicio de la Pi (scp desde tu equipo," \
    "NUNCA la subas al repo) y luego 'sudo systemctl restart $SERVICE_NAME'." \
    "Esa key NUNCA debe quedar dentro de $APP_DIR."
fi

# --- Paso 7: modelo exportado -----------------------------------------------------------------
log "Paso 7/12: modelo exportado en $MODEL_DIR"
if [[ -n "$(find "$MODEL_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]]; then
  chown -R epp:epp "$MODEL_DIR"
  log "Hay contenido en $MODEL_DIR (permisos fijados a epp:epp)"
else
  warn "$MODEL_DIR esta vacio. Copia ahi el modelo exportado por raspberrypi/tools/export_model.py" \
    "(Fase 7; por defecto MODEL_PATH=/var/lib/epp-edge/models/best_ncnn_model) antes de iniciar el servicio."
fi

# --- Paso 8: unit de systemd -------------------------------------------------------------------
log "Paso 8/12: unit de systemd"
install -o root -g root -m 0644 "$UNIT_SRC" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
log "Servicio '$SERVICE_NAME' habilitado. Si edge.env/la key/el modelo aun faltan, el servicio" \
  "reintentara solo (Restart=always) hasta que completes esos pasos."

# --- Paso 9: journald con tope de 200M ----------------------------------------------------------
log "Paso 9/12: journald (SystemMaxUse=200M)"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/epp.conf <<'EOF'
[Journal]
SystemMaxUse=200M
EOF
systemctl restart systemd-journald

# --- Paso 10: watchdog de hardware --------------------------------------------------------------
log "Paso 10/12: watchdog de hardware"
CONFIG_TXT=/boot/firmware/config.txt
if [[ ! -f "$CONFIG_TXT" ]]; then
  CONFIG_TXT=/boot/config.txt
fi
if [[ -f "$CONFIG_TXT" ]]; then
  if grep -qxF 'dtparam=watchdog=on' "$CONFIG_TXT"; then
    log "$CONFIG_TXT ya tiene dtparam=watchdog=on"
  else
    printf '\n# Habilitado por raspberrypi/deploy/install.sh (Fase 6.2)\ndtparam=watchdog=on\n' >> "$CONFIG_TXT"
    warn "Se agrego dtparam=watchdog=on a $CONFIG_TXT. Hace falta REINICIAR la Pi para que tome efecto."
  fi
else
  warn "No se encontro $CONFIG_TXT (ni /boot/config.txt); activa 'dtparam=watchdog=on' a mano."
fi

mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=15
EOF
log "RuntimeWatchdogSec=15 escrito en /etc/systemd/system.conf.d/watchdog.conf (tambien requiere reinicio" \
  "para que systemd-system lo recargue por completo)"

# --- Paso 11: SSH solo con llave + ufw ----------------------------------------------------------
log "Paso 11/12: SSH solo con llave + ufw"
apt-get install -y ufw

SSHD_CONFIG=/etc/ssh/sshd_config
has_authorized_keys=0
for f in /home/*/.ssh/authorized_keys /root/.ssh/authorized_keys; do
  if [[ -s "$f" ]]; then
    has_authorized_keys=1
    break
  fi
done

if [[ "$has_authorized_keys" -eq 1 || "${FORCE_SSH_KEYONLY:-0}" == "1" ]]; then
  cp "$SSHD_CONFIG" "${SSHD_CONFIG}.bak-epp-edge"
  sed -i \
    -e 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' \
    -e 's/^#\?PubkeyAuthentication .*/PubkeyAuthentication yes/' \
    -e 's/^#\?KbdInteractiveAuthentication .*/KbdInteractiveAuthentication no/' \
    "$SSHD_CONFIG"
  grep -q '^PasswordAuthentication no' "$SSHD_CONFIG" || echo 'PasswordAuthentication no' >> "$SSHD_CONFIG"
  grep -q '^PubkeyAuthentication yes' "$SSHD_CONFIG" || echo 'PubkeyAuthentication yes' >> "$SSHD_CONFIG"
  # Sin esto, UsePAM yes (el valor por defecto) puede seguir aceptando contrasena via PAM aunque
  # PasswordAuthentication este en no.
  grep -q '^KbdInteractiveAuthentication no' "$SSHD_CONFIG" || echo 'KbdInteractiveAuthentication no' >> "$SSHD_CONFIG"
  if sshd -t; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    log "sshd configurado solo con llave (PasswordAuthentication no)"
  else
    cp "${SSHD_CONFIG}.bak-epp-edge" "$SSHD_CONFIG"
    warn "El cambio a $SSHD_CONFIG quedo invalido (sshd -t fallo); se revirtio." \
      "Revisalo a mano, no se desactivo la autenticacion por contrasena."
  fi
else
  warn "No se encontro ningun ~/.ssh/authorized_keys; NO se desactiva la autenticacion por" \
    "contrasena (te dejaria sin acceso). Copia tu llave publica y vuelve a correr install.sh," \
    "o define FORCE_SSH_KEYONLY=1 si ya tienes acceso por otro medio."
fi

ufw default deny incoming
ufw default allow outgoing

lan_cidr() {
  python3 - <<'PY' 2>/dev/null
import ipaddress
import subprocess

out = subprocess.run(
    ["ip", "-4", "-o", "addr", "show", "scope", "global", "up"],
    capture_output=True, text=True,
).stdout
for line in out.splitlines():
    for tok in line.split():
        if "/" in tok and tok.count(".") == 3:
            try:
                print(ipaddress.ip_interface(tok).network)
            except ValueError:
                continue
            raise SystemExit(0)
PY
}

ssh_allow_from="${SSH_ALLOW_FROM:-}"
if [[ -z "$ssh_allow_from" ]]; then
  ssh_allow_from="$(lan_cidr || true)"
fi

if [[ -n "$ssh_allow_from" ]]; then
  ufw allow from "$ssh_allow_from" to any port 22 proto tcp
  log "SSH permitido solo desde $ssh_allow_from (ufw)"
else
  ufw allow 22/tcp
  warn "No se pudo detectar la subred LAN; se permitio SSH (22/tcp) desde cualquier origen." \
    "Define SSH_ALLOW_FROM=<CIDR> y vuelve a correr install.sh para restringirlo."
fi

ufw --force enable
log "ufw: $(ufw status | head -n1)"

if [[ "${INSTALL_TAILSCALE:-0}" == "1" ]]; then
  log "Instalando Tailscale (INSTALL_TAILSCALE=1)"
  curl -fsSL https://tailscale.com/install.sh | sh
  log "Tailscale instalado. Corre 'tailscale up' a mano para unir esta Pi a tu tailnet."
else
  log "Tailscale no instalado (opcional, administracion remota). Para instalarlo:" \
    "INSTALL_TAILSCALE=1 sudo -E bash raspberrypi/deploy/install.sh"
fi

# --- Paso 12: NTP ---------------------------------------------------------------------------
log "Paso 12/12: NTP"
timedatectl set-ntp true
systemctl enable --now systemd-timesyncd 2>/dev/null || true
log "NTP activado. Verifica en unos minutos con: timedatectl show -p NTPSynchronized --value"

# --- Resumen -----------------------------------------------------------------------------------
log "Instalacion terminada. Pendiente de revisar a mano:"
if [[ -f "$ENV_FILE" ]] && grep -q 'REEMPLAZAR_CON_EL_DEVICE_TOKEN_REAL' "$ENV_FILE" 2>/dev/null; then
  warn "  - $ENV_FILE todavia tiene el DEVICE_TOKEN de la plantilla; complétalo."
fi
[[ -f "$KEY_PATH" ]] || warn "  - Falta copiar la key de la cuenta de servicio en $KEY_PATH."
[[ -n "$(find "$MODEL_DIR" -mindepth 1 -print -quit 2>/dev/null)" ]] || \
  warn "  - Falta copiar el modelo exportado en $MODEL_DIR."
warn "  - Reinicia la Pi para que tomen efecto el watchdog de hardware (config.txt) y RuntimeWatchdogSec."
log "Despues de completar lo anterior: sudo systemctl restart $SERVICE_NAME"
log "Verificacion: sudo systemctl status $SERVICE_NAME ; journalctl -u $SERVICE_NAME -f"
