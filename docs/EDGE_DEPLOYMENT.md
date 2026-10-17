# Despliegue en el borde (Raspberry Pi)

**Para quién es:** quien instale, actualice o retire físicamente el dispositivo `epp_edge` en una
Raspberry Pi (operador de planta o quien dé soporte remoto).

**Última verificación:** 2026-10-11. Los scripts de `raspberrypi/deploy/` se revisaron a mano
(sintaxis y coherencia con `raspberrypi/epp_edge/config.py`); **nada de este documento ni de los
scripts se probó en una Raspberry Pi física** (no hubo una disponible durante la Fase 6 de la
remediación). Antes de confiar en este documento para producción, sigue la sección 8
("Qué queda pendiente de validar con hardware real").

---

## 1. Hardware recomendado (plan 6.1)

| Componente | Recomendado | Mínimo |
|---|---|---|
| Placa | Raspberry Pi 5 (4-8 GB) | Raspberry Pi 4 (4 GB+) |
| Enfriamiento | Disipador activo (ventilador) | — |
| Fuente | Oficial de Raspberry Pi para el modelo | — |
| Almacenamiento de datos | SSD por USB 3 o NVMe (ver abajo) | microSD *High Endurance* |
| Cámara | CSI con `picamera2` o USB UVC con MJPG | — |
| UPS | HAT de batería (opcional) | — |

### Por qué el almacenamiento importa (riesgo migrado de `riesgos.txt`)

Las tarjetas microSD estándar no están pensadas para la escritura constante que genera una base
SQLite (el outbox de `epp_edge`, en `DATA_DIR/outbox.db`, con `journal_mode=WAL` y
`synchronous=FULL`: cada evento confirmado y cada avance de `stage` es una escritura). Con el
tiempo esto desgasta la tarjeta y puede corromper el sistema de archivos completo, no solo la base.

Mitigación, de peor a mejor:
1. **Mínimo aceptable:** microSD *High Endurance* o *Industrial* (pensada para cámaras de
   seguridad / dash cams).
2. **Recomendado:** mover `DATA_DIR` a un SSD/NVMe por USB 3. Con eso el sistema operativo vive en
   la microSD y los datos (outbox + capturas pendientes) en el SSD: si la microSD se corrompe, los
   datos en cola sobreviven. Esto ya es configurable sin tocar código: `DATA_DIR` es una variable
   de `/etc/epp-edge/edge.env` (ver `raspberrypi/epp_edge/config.py`); apunta el SSD, por ejemplo,
   a `/mnt/epp-ssd` y pon `DATA_DIR=/mnt/epp-ssd` antes de correr `install.sh` (o edítalo después y
   copia a mano lo que haya en el `DATA_DIR` viejo antes de reiniciar el servicio).

El riesgo de "proceso colgado" y "pérdida de energía / falta de heartbeat" del documento viejo ya
están resueltos por el diseño actual (systemd con watchdog real + `device_health` como latido) y
se explican en las secciones 3 y 8, y en `docs/OPERATIONS_RUNBOOK.md`.

---

## 2. Sistema operativo y flasheo

- **Raspberry Pi OS Lite 64-bit** (obligatorio: los wheels aarch64 de `torch`/`ultralytics` que
  usa `raspberrypi/requirements.txt` solo existen para 64-bit).
- Flashea con **Raspberry Pi Imager**:
  1. Elige "Raspberry Pi OS Lite (64-bit)".
  2. En el engranaje de opciones avanzadas: activa SSH **"Allow public-key authentication only"**
     y pega tu llave pública (si activas también usuario/contraseña, `install.sh` igual exigirá
     una llave en `~/.ssh/authorized_keys` antes de desactivar la contraseña — ver §3, paso 11).
  3. Configura el hostname, la zona horaria y, si aplica, el Wi-Fi.
- Primer arranque: conéctate por SSH, actualiza el sistema (`sudo apt update && sudo apt
  full-upgrade -y`) y reinicia.
- Si usas un SSD/NVMe para `DATA_DIR` (§1), móntalo y anótalo en `/etc/fstab` antes de instalar
  `epp-edge`.

---

## 3. Instalación: `raspberrypi/deploy/install.sh` paso a paso

El script es **idempotente**: se puede volver a correr cuantas veces haga falta, cada paso
comprueba el estado actual antes de cambiar algo. No escribe secretos: la key de la cuenta de
servicio y el `DEVICE_TOKEN` real los coloca el operador a mano.

```bash
# En la Pi, con el repo clonado (o solo con este script descargado, ver nota):
sudo bash raspberrypi/deploy/install.sh
```

> Nota: `install.sh` clona el repo él mismo en `/opt/epp-edge/app` si no lo encuentra ahí. Puedes
> ejecutarlo desde dentro de un clon ya existente (en cuyo caso no se vuelve a clonar) o copiar
> solo `raspberrypi/deploy/install.sh` a la Pi y correrlo: se encargará de clonar el repo completo.

Variables de entorno opcionales (usa `sudo -E` para que el script las vea):

| Variable | Para qué |
|---|---|
| `REPO_URL` | Repo a clonar (por defecto, `https://github.com/CesarPP23/app-detection-epp-demo.git`) |
| `DEPLOY_BRANCH` | Rama a clonar (por defecto `main`) |
| `SSH_ALLOW_FROM` | CIDR desde el que `ufw` permite SSH (por defecto intenta detectar la LAN) |
| `FORCE_SSH_KEYONLY=1` | Desactiva la contraseña de SSH aunque no se detecte ninguna llave autorizada (úsalo solo si ya tienes otro acceso, p. ej. consola serie) |
| `INSTALL_TAILSCALE=1` | Instala el cliente de Tailscale (opcional; no se activa solo) |

Los 12 pasos (sección 6.2 del plan de remediación) y lo que hace cada uno:

1. **Paquetes del sistema:** `python3-venv`, `python3-picamera2`, `git`, `unattended-upgrades`.
2. **Usuario de sistema `epp`:** sin shell (`/usr/sbin/nologin`), con home en `/var/lib/epp-edge`
   y en el grupo `video` (para acceder a la cámara).
3. **Directorios:** `/var/lib/epp-edge`, `/var/lib/epp-edge/models`, `/var/lib/epp-edge/pending`
   (dueño `epp:epp`, modo `0750`) y `/etc/epp-edge` (dueño `root:epp`, modo `0750`).
4. **Código y entorno virtual:** clona el repo en `/opt/epp-edge/app` (si no existe ya) y crea
   `/opt/epp-edge/venv` con `--system-site-packages` (para heredar el `picamera2` que instaló
   `apt`, que no se puede instalar con `pip` dentro del venv). Instala
   `raspberrypi/requirements.txt`.
5. **`/etc/epp-edge/edge.env`:** se copia desde `raspberrypi/deploy/edge.env.template`
   **solo si no existe ya** (para no pisar una configuración real), con permisos `root:epp 0640`.
   **Después de instalar, edítalo a mano**: como mínimo `DEVICE_TOKEN` (cópialo desde el secreto
   `device-token` de Secret Manager — nunca lo escribas en un archivo que vaya al repo) y
   `ORCHESTRATOR_HOST` (el host de Cloud Run, sin esquema ni ruta).
6. **Key de la cuenta de servicio:** el script **no la crea** (sería escribir un secreto). Si ya
   copiaste `gcp-key.json` a `/etc/epp-edge/gcp-key.json`, le fija los permisos correctos
   (`root:epp 0640`); si no está, te avisa. **Nunca** la copies dentro de `/opt/epp-edge/app`: ese
   directorio es del código versionado y `.gitignore` no lo protegería si algún día cambia.
7. **Modelo exportado:** tampoco lo genera (es la Fase 7, `raspberrypi/tools/export_model.py`,
   fuera de este alcance). Si `/var/lib/epp-edge/models` ya tiene contenido le fija el dueño
   (`epp:epp`); si está vacío, te avisa. Por defecto `MODEL_PATH` apunta a
   `/var/lib/epp-edge/models/best_ncnn_model`.
8. **Unit de systemd:** instala `raspberrypi/deploy/epp-edge.service` en
   `/etc/systemd/system/epp-edge.service` y hace `systemctl enable --now epp-edge`. Si a este
   punto todavía falta el `edge.env` real, la key o el modelo, el servicio reintentará solo
   (`Restart=always`) hasta que los completes y lo reinicies.
9. **journald:** `/etc/systemd/journald.conf.d/epp.conf` con `SystemMaxUse=200M`, para que los
   logs no llenen el disco con el tiempo.
10. **Watchdog de hardware:** agrega `dtparam=watchdog=on` a `/boot/firmware/config.txt` (o
    `/boot/config.txt` en imágenes viejas) y `RuntimeWatchdogSec=15` en
    `/etc/systemd/system.conf.d/watchdog.conf`. **Requiere reiniciar la Pi** para tomar efecto por
    completo (el `config.txt` siempre; el ajuste de `system.conf.d` a veces se recarga con
    `systemctl daemon-reexec`, pero reiniciar es más confiable).
11. **SSH solo con llave + `ufw`:** si encuentra al menos un `~/.ssh/authorized_keys` no vacío (de
    cualquier usuario, incluido `root`), desactiva `PasswordAuthentication` y
    `KbdInteractiveAuthentication` en `sshd` (valida la sintaxis con `sshd -t` antes de recargar;
    si quedara inválida, revierte el cambio automáticamente). Si no encuentra ninguna llave, **no**
    desactiva la contraseña (para no dejarte sin acceso) y lo dice explícitamente. Luego activa
    `ufw` con `default deny incoming` y permite el puerto 22 solo desde la subred LAN detectada
    (o la que pases en `SSH_ALLOW_FROM`; si no logra detectarla, deja 22 abierto a cualquier
    origen y avisa para que lo restrinjas a mano). Tailscale es opcional (`INSTALL_TAILSCALE=1`).
12. **NTP:** `timedatectl set-ntp true` + habilita `systemd-timesyncd`. Los timestamps que usa el
    lake para particionar por fecha (`dt=YYYY-MM-DD`, según `SITE_TIMEZONE`) dependen de que el
    reloj de la Pi esté sincronizado.

Al final el script imprime un resumen de lo que falta completar a mano (token, key, modelo,
reinicio pendiente) y los comandos para verificar el servicio.

---

## 4. Verificación tras la instalación

```bash
sudo systemctl status epp-edge          # active (running); revisa "Watchdog" en la salida
journalctl -u epp-edge -f               # logs en vivo (Ctrl+C para salir)
sudo ls -l /etc/epp-edge                # debe ser root:epp, 0750; edge.env y gcp-key.json en 0640
timedatectl show -p NTPSynchronized --value    # "yes" tras unos minutos

# Desde tu propia PC (no hace falta entrar a la Pi): solo lectura, necesita
# `gcloud auth application-default login` hecho una vez y el paquete google-cloud-firestore
# instalado localmente.
python raspberrypi/tools/check_firestore.py --device-id raspberry-pi-01
```

Con el servicio corriendo y la cámara conectada, en `device_health/<device_id>` (consulta con
`check_firestore.py` desde cualquier equipo con credenciales de aplicación por defecto, sin tocar
la Pi) deberías ver `reported_state=running`, `camera_ok=true` y `last_seen` actualizándose cada
`HEARTBEAT_SECONDS` (300 s por defecto) o antes si cambia algo.

Pruebas de resiliencia (de los criterios de aceptación de la Fase 6, **sin validar en hardware
real**, ver §8):

```bash
sudo systemctl kill -s SIGKILL epp-edge   # debe volver a "active (running)" en <= 10 s (Restart=always, RestartSec=5)
```

---

## 5. Actualización: `raspberrypi/deploy/update.sh`

```bash
sudo bash raspberrypi/deploy/update.sh
```

Secuencia (plan 6.4): `git pull --ff-only` → instala dependencias (incluye
`requirements-dev.txt`, necesario para el siguiente paso) → `pytest -q raspberrypi/tests` →
`systemctl restart epp-edge` → espera hasta `HEALTH_TIMEOUT` segundos (120 por defecto) a que
`device_health.edge_version` cambie al valor nuevo (si el commit actualizado no cambió
`raspberrypi/epp_edge/__init__.py`, en vez de eso espera solo una escritura fresca de
`device_health` tras el reinicio). Si las pruebas fallan o no llega esa confirmación, **revierte
solo** al commit anterior, reinstala sus dependencias y reinicia el servicio con el código viejo.

Recomendación de versión: sube `__version__` en `raspberrypi/epp_edge/__init__.py` en cada
despliegue real; si no cambia, `update.sh` solo puede confirmar que el proceso volvió a reportar
salud, no que corre el código nuevo.

```bash
HEALTH_TIMEOUT=240 sudo -E bash raspberrypi/deploy/update.sh   # más tiempo si el modelo tarda en cargar
```

---

## 6. Desinstalación

No hay un `uninstall.sh` (fuera del alcance de la Fase 6). Para retirar el dispositivo a mano:

```bash
sudo systemctl disable --now epp-edge
sudo rm /etc/systemd/system/epp-edge.service
sudo systemctl daemon-reload
sudo rm -rf /opt/epp-edge                 # código + venv
sudo rm -rf /var/lib/epp-edge             # outbox, capturas pendientes, modelo
sudo rm -rf /etc/epp-edge                 # edge.env y la key de la cuenta de servicio
sudo userdel epp
sudo rm /etc/systemd/journald.conf.d/epp.conf /etc/systemd/system.conf.d/watchdog.conf
```

Antes de borrar `/var/lib/epp-edge/outbox.db`, confirma que está vacío (ver
`docs/OPERATIONS_RUNBOOK.md`, "Revisión del outbox") para no perder eventos sin subir. Si la Pi
se reutiliza para otra cosa, revoca también la key de la cuenta de servicio
(`raspberry-pi-worker@...`) desde la consola de GCP y quita el dispositivo de `ALLOWED_DEVICES` en
Cloud Run si corresponde.

---

## 7. Mantenimiento preventivo (riesgos migrados de `riesgos.txt`)

El documento `riesgos.txt` original (eliminado del repo; su contenido vive aquí y en
`docs/OPERATIONS_RUNBOOK.md`) describía tres riesgos sobre una versión mucho más simple del
dispositivo (`worker.py`, sin outbox con capturas, sin systemd real, sin `device_health`). Así se
resuelve cada uno en el diseño actual:

| Riesgo original | Mitigación ya implementada |
|---|---|
| Corrupción de la microSD por escritura constante de SQLite | `DATA_DIR` configurable (§1); mover el outbox a un SSD/NVMe sin tocar código |
| El script se cuelga y deja de procesar | `epp-edge.service` con `Type=notify` + `WatchdogSec=60`: si `InferenceWorker` deja de avanzar más de 30 s, deja de llegar `WATCHDOG=1` a systemd y el servicio se reinicia solo (`Restart=always`, `RestartSec=5`) |
| Corte de luz / fallo de hardware sin aviso | `device_health` se escribe cada `HEARTBEAT_SECONDS` (latido real en Firestore, no un tema de Pub/Sub aparte) y el dashboard marca el dispositivo "offline" si `last_seen` tiene más de ~11 min; opcionalmente, `HEALTHCHECK_URL` en `edge.env` pega a un servicio externo tipo healthchecks.io que sí puede alertar por correo/Telegram cuando el latido deja de llegar (ver `docs/OPERATIONS_RUNBOOK.md`, "La Pi aparece offline") |

---

## 8. Qué queda pendiente de validar con hardware real

No hubo una Raspberry Pi física disponible durante esta fase. Lo siguiente está escrito según la
especificación y revisado a mano (sintaxis de los `.sh`, nombres de variables y rutas contra
`raspberrypi/epp_edge/config.py` y el resto del código), pero **nada de esto se ejecutó de
verdad**:

- Los 4 criterios de aceptación de la Fase 6 (ver `docs/PLAN_REMEDIACION.md`, sección 10): ninguno
  se puede confirmar sin hardware real (kill -9 y recuperación en <= 10 s; watchdog ante
  inferencia congelada en <= 90 s; arranque limpio tras reiniciar la Pi; permisos reales de
  `/etc/epp-edge` y ausencia de la key dentro de `/opt/epp-edge/app` — esto último sí se puede
  revisar por código/convención, pero confirmarlo en disco requiere la Pi).
- Que `apt install python3-picamera2` entregue una versión de `picamera2` compatible con el
  `numpy` fijado en `raspberrypi/requirements.txt` (el propio archivo lo marca como pendiente de
  validar en aarch64).
- Que `ultralytics==8.4.175` y `ncnn==1.0.20260526` tengan wheels aarch64 instalables sin
  compilar (el archivo de requisitos ya lo señala como no validado).
- Que el watchdog de hardware (`dtparam=watchdog=on`) realmente provoque un reinicio duro cuando
  el kernel deja de "alimentarlo" (esto depende del firmware/SoC exacto de cada modelo de Pi).
- Que la detección automática de la subred LAN en `install.sh` (`ip -4 -o addr show scope global
  up`) acierte en la topología de red real donde se instale.
- Que desactivar `PasswordAuthentication` no deje a nadie sin acceso en la práctica (el script
  tiene una verificación de seguridad — no desactiva la contraseña si no encuentra ninguna llave
  autorizada — pero es una red de seguridad, no una prueba de que el acceso por llave funciona).
- El rendimiento real de inferencia (FPS, temperatura, throttling térmico) en la Pi elegida: eso
  es contenido de la Fase 7 (`raspberrypi/tools/benchmark.py`, `docs/MODEL.md`), fuera de este
  documento.

El usuario debe copiar manualmente a la Pi: el `DEVICE_TOKEN` real (desde el secreto
`device-token` de Secret Manager), la key de `raspberry-pi-worker@...` y el modelo exportado de la
Fase 7. Ninguno de los tres se genera ni se commitea desde esta fase.
