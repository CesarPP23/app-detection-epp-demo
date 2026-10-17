# Runbook de operaciones

**Para quién es:** quien opere el sistema día a día: reinicie el dispositivo, rote credenciales,
dé de alta o de baja usuarios del dashboard, revise costos o investigue un incidente.

**Última verificación:** 2026-10-11. Los comandos de este documento se verificaron contra el
código real (`raspberrypi/epp_edge/`, `backend/epp_api/`, `infra/`) pero **ninguno se ejecutó**:
este repo no ejecuta `gcloud`/`bq`/`firebase` ni nada que mute GCP sin el visto bueno explícito del
usuario en la sesión donde se vaya a correr, y no hubo una Raspberry Pi física disponible durante
esta fase. Trátalos como instrucciones para el operador humano, no como algo ya probado.

---

## 1. Logs y reinicio

```bash
journalctl -u epp-edge -f                 # logs en vivo (Ctrl+C para salir)
journalctl -u epp-edge --since "1 hour ago"
journalctl -u epp-edge -p err             # solo errores/críticos
sudo systemctl status epp-edge            # estado, reintentos recientes, si el watchdog está activo
sudo systemctl restart epp-edge           # reinicio manual
```

`journald` tiene un tope de `SystemMaxUse=200M` (lo instala `raspberrypi/deploy/install.sh`, paso
9): la ventana de historial disponible con `journalctl` es finita, no infinita.

Un `systemctl restart` (o un `SIGTERM`/`SIGINT`) deja que el pipeline cierre cualquier sesión de
detección abierta y la mande al outbox con `closed_reason="shutdown"` antes de salir — no se
pierde el evento en curso (ver `raspberrypi/epp_edge/app.py`, `Pipeline.stop` /
`main().finally`).

Para un apagón físico limpio (antes de desconectar la fuente): `sudo systemctl stop epp-edge &&
sudo poweroff`. El apagado abrupto (cortar la corriente sin avisar) no pierde eventos confirmados
porque el outbox persiste en SQLite con `journal_mode=WAL` + `synchronous=FULL`, pero sí puede
perder un evento que estuviera a medio confirmar (frames aún agregándose, sin cerrar sesión).

---

## 2. Rotación de credenciales

### 2.1 Key de la cuenta de servicio de la Pi (`raspberry-pi-worker@...`)

Rota esta key si: se sospecha que se filtró, cambia el responsable del dispositivo, o como
práctica periódica. El proyecto ya tuvo un incidente de key filtrada en el historial público (ver
`docs/HANDOFF_CLAUDE_CODE.md` sección 6): la respuesta correcta es **rotar, nunca reescribir el
historial de git**.

1. En la consola de GCP (o `gcloud iam service-accounts keys create`, a mano, nunca desde este
   repo): crea una key nueva para `raspberry-pi-worker@epp-detection-v1.iam.gserviceaccount.com`.
2. Cópiala a la Pi **sin pasar por el repo ni por ningún commit**:
   ```bash
   scp nueva-key.json usuario@<host-de-la-pi>:/tmp/gcp-key.json
   ```
3. En la Pi:
   ```bash
   sudo install -o root -g epp -m 0640 /tmp/gcp-key.json /etc/epp-edge/gcp-key.json
   rm /tmp/gcp-key.json
   sudo systemctl restart epp-edge
   ```
   (equivalente a volver a correr `install.sh`, que deja los mismos permisos de forma idempotente
   si la key ya está en su sitio).
4. Confirma que sigue subiendo: `journalctl -u epp-edge -f` sin errores de autenticación, y que
   `outbox_pending`/`outbox_bytes` en `device_health` no se quedan creciendo (ver §4).
5. Solo después de confirmar que la key nueva funciona, **revoca/borra la key vieja** desde la
   consola de GCP (IAM → cuentas de servicio → `raspberry-pi-worker@...` → Claves).

### 2.2 `DEVICE_TOKEN`

1. Crea una nueva versión del secreto `device-token` en Secret Manager (consola, o a mano con
   `gcloud secrets versions add device-token --data-file=...`; nunca desde este repo ni impreso en
   ningún log).
2. El backend (`orchestrator-service`) lee `DEVICE_TOKEN` como variable de entorno resuelta desde
   ese secreto al arrancar la instancia: una versión nueva normalmente exige volver a desplegar o
   reiniciar la revisión para que la recoja (ver `docs/CLOUD_DEPLOYMENT.md` para el comando exacto
   de `infra/03_deploy_cloud.sh` / `deploy_cloud.sh`; no es parte de esta fase).
3. En la Pi, edita `/etc/epp-edge/edge.env` con el nuevo valor de `DEVICE_TOKEN` (cópialo del
   secreto, nunca lo escribas en un archivo que pueda llegar al repo) y:
   ```bash
   sudo systemctl restart epp-edge
   ```
4. Verifica que el streaming vuelve a conectar (abre "En vivo" en el dashboard) y que no aparecen
   errores de autenticación (401/403) en `journalctl -u epp-edge`.
5. Si la rotación fue por sospecha de filtración, revisa Cloud Logging por conexiones a
   `/ws/video/<device_id>` con el token viejo después de la rotación (deberían empezar a fallar).

---

## 3. Restauración desde backup

Firestore tiene backups automáticos semanales (domingo, retención de 4 semanas) en **ambas**
bases (`(default)` y `dbraspberry`), creados por `infra/04_firestore.sh`. Para restaurar (desde tu
propio equipo, nunca desde este repo sin el OK explícito del usuario en esa sesión):

```bash
# 1. Ver los backups disponibles (revisa --help: los nombres de flags de gcloud cambian con el tiempo)
gcloud firestore backups list --project=epp-detection-v1

# 2. Restaurar: normalmente crea una base NUEVA a partir del backup, no sobrescribe la base viva.
#    Confirma el comportamiento exacto con --help antes de correrlo.
gcloud firestore databases restore \
  --source-backup=<BACKUP_ID_DEL_PASO_1> \
  --destination-database=<id-de-la-base-restaurada> \
  --project=epp-detection-v1
```

Después de restaurar: compara la base restaurada contra la viva (conteos de `detecciones_confirmadas`,
`daily_stats`) antes de decidir si migrar algo de vuelta; **no** apuntes `CONTROL_DATABASE_ID` ni
`DETECTIONS_DATABASE_ID` (en `edge.env` ni en el backend) a la base restaurada sin haber entendido
qué se perdió y por qué se restauró.

El data lake en GCS (`gs://epp-detection-v1-lake`) no tiene un backup separado: los objetos son
inmutables (`if_generation_match=0`, nunca se sobreescriben ni la Pi puede borrarlos) y viven en
Standard/Nearline según antigüedad. Su "backup" es la redundancia del propio GCS; para recuperar
un objeto borrado por error (solo un admin humano puede borrar, nunca la Pi) revisa si el bucket
tiene versionado de objetos habilitado antes de asumir que se puede recuperar — ver
`docs/DATA_LAKE.md`.

---

## 4. Revisión del outbox (`sqlite3`)

El outbox vive en `/var/lib/epp-edge/outbox.db` (SQLite, `journal_mode=WAL`). Es seguro leerlo con
`sqlite3` mientras el servicio está corriendo (WAL permite lectores concurrentes); **no lo
edites a mano** (`UPDATE`/`DELETE` manuales rompen la idempotencia que mantiene `Outbox` en
`raspberrypi/epp_edge/outbox.py`).

```bash
# Si falta el cliente (no lo instala install.sh, es de solo lectura/diagnóstico):
sudo apt install -y sqlite3

# Cuántas filas hay en cada etapa (images -> sidecar -> firestore; una fila sale del outbox
# recién cuando las tres etapas terminan):
sqlite3 /var/lib/epp-edge/outbox.db "select stage, count(*) from outbox group by 1"

# Las más antiguas, con sus reintentos y el último error (si lo hay):
sqlite3 /var/lib/epp-edge/outbox.db \
  "select event_id, stage, attempts, last_error from outbox order by created_at limit 20"

# Tamaño total de las capturas aún pendientes en disco (lo mismo que outbox_bytes en device_health):
sqlite3 /var/lib/epp-edge/outbox.db "select count(*) from outbox where pending_dir is not null"
```

Lectura de las columnas: `stage` avanza solo cuando ese paso de subida terminó (ver el orden
imágenes → sidecar JSON → Firestore en `outbox.py`); `attempts`/`next_attempt_at` reflejan el
backoff exponencial (tope 300 s) tras un fallo; `last_error` es la excepción del último intento.

Si el outbox crece sin bajar, ver la fila "El outbox crece" en la sección 8.

---

## 5. Reconciliación semanal del lake

`raspberrypi/tools/reconcile_lake.py` compara, para un día local (`SITE_TIMEZONE`): los eventos
contados en `daily_stats`, los documentos de `detecciones_confirmadas`, los sidecars JSON y las
imágenes del lake, y (de forma solo informativa) las filas ya cargadas en BigQuery. Solo necesita
permisos de **lectura** y se puede correr desde cualquier equipo con credenciales de aplicación
por defecto (`gcloud auth application-default login`), no hace falta entrar a la Pi.

```bash
# Correr cada semana (p. ej. todos los lunes) por el día de ayer (default) o uno puntual:
python raspberrypi/tools/reconcile_lake.py --device raspberry-pi-01
python raspberrypi/tools/reconcile_lake.py --date 2026-10-10 --device raspberry-pi-01

# Sin tocar GCP, para probar que el script en sí funciona:
python raspberrypi/tools/reconcile_lake.py --dry-run
```

Código de salida `0` = coherente. Código `1` = hay diferencias reales en las capas 1-4 (hay que
investigarlas: ver "Faltan capturas" en la sección 8). Las diferencias de la capa 5 (BigQuery)
solo son avisos: esa tabla se pone al día con el `MERGE` diario programado a las 06:00 UTC
(`infra/06_bigquery.sh`, consulta `curate_events`), así que un desfase del mismo día ahí es
normal.

---

## 6. Alta y baja de usuarios del dashboard

El alta/baja real (rol, activo/inactivo) se hace desde la pantalla de Admin del dashboard
(un usuario con rol `admin`), que llama a `POST/PATCH/DELETE /api/admin/users` (`backend/epp_api/routers/admin.py`).
No hay un script de línea de comandos para esto: es intencional, cada cambio queda auditado en
`audit_log` con quién lo hizo.

**Paso que se olvida seguido (mientras la app de Google Auth Platform esté en modo Testing):**
agregar un usuario en `dashboard_users` **no alcanza** para que pueda entrar. Google exige que
cada cuenta nueva esté *también* en **Audience → Test users** del proyecto (consola de GCP →
APIs & Services → OAuth consent screen), porque la app "EPP Monitor" no se puede publicar a
producción sin un dominio propio (`run.app` es un sufijo público que Google no acepta en
"Authorized domains"; ver `docs/HANDOFF_CLAUDE_CODE.md` sección 7). Tope de 100 test users
mientras dure esto. Hazlo en este orden:

1. Agrega el correo en Audience → Test users (consola de GCP).
2. Dalo de alta en el dashboard (Admin → Usuarios → Nuevo), con el rol que corresponda
   (`viewer`/`operator`/`admin`).

Para dar de baja: desactívalo o bórralo desde Admin → Usuarios. El sistema no permite que un
admin se desactive o se borre a sí mismo, ni que la última cuenta `admin` activa se quede sin
reemplazo (lo bloquea `_guard_last_admin` en `admin.py`): necesitas otra cuenta admin activa para
hacer el cambio. Opcionalmente, quítalo también de Test users en Google si quieres que ni siquiera
llegue a la pantalla de login (el alta en `dashboard_users` ya lo bloquea a nivel de la app).

---

## 7. Cierre de una sesión de pruebas (con verificación de costos)

1. Deja el dispositivo en `stopped` (dashboard, o `python raspberrypi/tools/set_state.py stopped
   --device-id raspberry-pi-01`) y confirma que no queda nadie en "En vivo" (`device_health.streaming`
   debe quedar en `false`; si alguien dejó una pestaña abierta, ciérrala: a los 15 min el frontend
   la pausa solo, pero no hay que esperar para el cierre de una prueba).
2. Revisa en la consola: Cloud Run (sin instancias corriendo fuera de request, métricas de
   WebSocket en 0), Firestore (uso del día), Cloud Storage (objetos nuevos del día, tamaño).
3. **No confíes en un "$0" de Billing el mismo día**: la facturación tiene rezago. Repite la
   revisión en Billing → Reports **al día siguiente** y recién ahí anota el resultado real en
   `docs/COSTS.md`.
4. Si la sesión fue de carga/estrés (mucho tiempo en "En vivo"), contrasta contra las fórmulas de
   `docs/PLAN_REMEDIACION.md` sección 14 (≈ 0.7-1.0 GB/h de egress de video, ≈ US\$3/mes por 1h/día
   mirando) para decidir si el costo observado es razonable.

---

## 8. Troubleshooting

### El stream no arranca

Síntoma: al abrir "En vivo" en el dashboard no llega video (o tarda mucho más de lo esperado).

- Revisa `journalctl -u epp-edge -f` en la Pi: busca líneas `Streaming caído: <error> (reintento
  en Ns)` (`raspberrypi/epp_edge/streamer.py`). Causas típicas detrás de ese error:
  - `DEVICE_TOKEN` en `edge.env` no coincide con el secreto `device-token` (el handshake del
    WebSocket responde 401/403).
  - El `DEVICE_ID` del dispositivo no está en `ALLOWED_DEVICES` del backend.
  - `ORCHESTRATOR_HOST` apunta a una URL vieja o mal escrita (sin esquema, sin ruta: solo el host).
  - Sin salida a internet desde la Pi (revisa `ufw status`, aunque por defecto permite todo el
    tráfico saliente; más probable es un firewall del router).
- Si no hay errores en los logs de la Pi, el problema puede ser que el *lease* nunca se activó:
  confirma con `python raspberrypi/tools/check_firestore.py --device-id raspberry-pi-01` que
  `device_control.stream_requested` se puso en `true` al abrir "En vivo" (si no, revisa los logs
  del backend por "Lease: no se pudo encender stream_requested").
- Si ya hay `MAX_LIVE_VIEWERS` (10 por defecto) espectadores simultáneos en cualquier dispositivo,
  uno nuevo no podrá entrar hasta que alguien salga.

### La Pi aparece offline

Síntoma: la pantalla de dispositivos del dashboard marca el estado "offline".

- Definición exacta: `device_health.last_seen` tiene más de ~11 minutos
  (`OFFLINE_AFTER_SECONDS` en `backend/epp_api/routers/devices.py`, pensado como 2 latidos de
  `HEARTBEAT_SECONDS` + margen). `last_seen` es un timestamp puesto por el **servidor** de
  Firestore, no el reloj de la Pi: un desajuste de NTP en la Pi no causa un "offline" falso.
- Diagnóstico sin tocar la Pi: `python raspberrypi/tools/check_firestore.py --device-id
  raspberry-pi-01` desde tu propio equipo. Si nunca hay `device_health`, el dispositivo nunca
  pudo escribir en Firestore (credenciales) o nunca llegó a arrancar.
- Si puedes entrar por SSH: `systemctl status epp-edge` (¿está corriendo? ¿cuántos reinicios
  recientes?) y `journalctl -u epp-edge -n 200` por errores "No se pudo escribir device_health"
  (revisa `GOOGLE_APPLICATION_CREDENTIALS` / permisos de la key / que no esté revocada).
- Si no es alcanzable por red ni por SSH: revisión física (alimentación, cable de red/Wi-Fi,
  router).
- Para enterarte sin tener que abrir el dashboard: pon `HEALTHCHECK_URL` en `edge.env` apuntando a
  un servicio tipo healthchecks.io; `HealthReporter` le hace ping en cada escritura de salud y
  avisa (correo/Telegram/lo que configures en ese servicio) si el latido deja de llegar. Es la
  versión real del "Riesgo 3" de `riesgos.txt` (antes solo una idea, ahora implementada).

### El outbox crece

Síntoma: `device_health.outbox_pending` / `outbox_bytes` suben y no bajan.

- `sqlite3 /var/lib/epp-edge/outbox.db "select stage, count(*) from outbox group by 1"` (ver
  sección 4). Si casi todo está atascado en `images`: revisa permisos de escritura de
  `raspberry-pi-worker@...` sobre `gs://epp-detection-v1-lake` y la conectividad. Si está atascado
  en `sidecar` o `firestore`: revisa la conectividad/credenciales hacia Firestore.
- Revisa `last_error` de las filas más antiguas (sección 4) para el motivo exacto.
- Si `OUTBOX_MAX_BYTES` (5 GB por defecto) se supera, el propio `Outbox.enforce_cap()` borra solo
  las capturas pendientes más viejas (nunca el evento) y las marca
  `capture.status="dropped_disk_full"`, con un `CRITICAL` en los logs — búscalo con
  `journalctl -u epp-edge | grep -i outbox_max_bytes`.
- Si la Pi estuvo desconectada un rato, un outbox que crece y luego se vacía solo al reconectar es
  el comportamiento esperado (resiliencia offline), no un problema.
- Revisa espacio en disco: `df -h /var/lib/epp-edge`.

### Faltan capturas

Síntoma: eventos sin miniatura en el dashboard, o conteos que no cuadran entre Firestore/BigQuery.

- Corre `python raspberrypi/tools/reconcile_lake.py --date <día> --device raspberry-pi-01`
  (sección 5) para el día en cuestión.
- Si el evento tiene `capture.status` distinto de `"ok"` (p. ej. `missing`,
  `dropped_disk_full`, `missing_legacy`): no es un bug de reconciliación, es un evento que
  legítimamente perdió su imagen (el evento en sí nunca se descarta por eso, por diseño) — revisa
  cuál de los dos pasó (disco lleno vs. archivos locales perdidos) en los logs de ese momento.
- Si la diferencia es real (p. ej. un sidecar en el lake sin su documento en Firestore, algo que
  el orden de etapas `images -> sidecar -> firestore` hace casi imposible salvo interferencia
  externa): revisa el outbox de la Pi por si ese `event_id` sigue encolado, y los logs alrededor
  de la hora del evento.
- Un desfase solo en la capa 5 (BigQuery) del mismo día es normal: espera al `MERGE` diario
  (06:00 UTC) y vuelve a correr la reconciliación.

### Subió la factura

Síntoma: el costo del mes se disparó respecto a `docs/COSTS.md`.

- Causa más probable (según el modelo de costos, plan sección 14): alguien dejó abierta una
  pestaña en "En vivo". El video por WebSocket es el único costo que crece de forma continua
  (≈ US\$3/mes por 1h/día mirando, escala lineal con las horas). Revisa cuánto tiempo
  `device_health.streaming` estuvo en `true` y si hay sesiones de "En vivo" largas sin explicación
  en los logs del backend.
- Revisa que no haya un `DEVICE_TOKEN` filtrado siendo usado por algo que no es la Pi real (busca
  en los logs de Cloud Run conexiones a `/ws/video/<device_id>` desde IPs inesperadas). Si hay
  sospecha, rota el token (sección 2.2).
- Revisa el historial de consultas de BigQuery por si alguna consulta ad hoc sin filtro de fecha
  escaneó mucho más de lo esperado (se cobra por TiB escaneado, no por fila devuelta).
- Acción: cierra cualquier "En vivo" abierto, pon el dispositivo en `stopped` si ya no se necesita,
  y sigue el cierre de pruebas de la sección 7 (sin confiar en un "$0" del mismo día).

### Un usuario no puede entrar

Síntoma: al elegir su cuenta de Google, el login falla o la app lo rechaza.

- **La causa más común mientras la app de Google esté en modo Testing:** el usuario está en
  `dashboard_users` pero no en **Audience → Test users** de Google (o viceversa). Son dos listas
  independientes — hace falta estar en **ambas** (ver sección 6). Tope de 100 test users.
- El usuario existe en `dashboard_users` pero con `active=false`, o le cambiaron el rol:
  revísalo en Admin → Usuarios (o `GET /api/admin/users` con una sesión de admin).
- Está entrando desde un origen no autorizado para el cliente OAuth (solo la URL de Cloud Run y
  `http://localhost:5173` están permitidos).
- Alcanzó el límite de 10 intentos/minuto por IP (comparte el límite con cualquier otra persona
  detrás de la misma IP, p. ej. toda una oficina con NAT): esperar un minuto y reintentar.
- Diagnóstico: `GET /api/admin/audit?email=<correo>` (como admin) para ver sus últimas acciones
  registradas; logs de Cloud Run por 401/403 cerca de la hora del intento.

---

## 9. Riesgos conocidos y mitigaciones (migrado de `riesgos.txt`)

El archivo `riesgos.txt` (eliminado del repo con este cambio) describía tres riesgos sobre una
versión mucho más simple del dispositivo de borde. Quedan resueltos así en el diseño actual; ver
también `docs/EDGE_DEPLOYMENT.md` sección 7 y 1 para el detalle de hardware:

1. **Corrupción de la tarjeta SD por escrituras constantes de SQLite.** `DATA_DIR` (donde vive
   `outbox.db`) es configurable; se recomienda un SSD/NVMe por USB en vez de la microSD del
   sistema (`docs/EDGE_DEPLOYMENT.md` sección 1).
2. **El proceso se cuelga y deja de procesar.** Ya no es un simple `Restart=always` de systemd:
   `epp-edge.service` usa `Type=notify` + `WatchdogSec=60`. Si el hilo de inferencia deja de
   avanzar (`InferenceWorker.last_progress`) por más de 30 s, el proceso deja de mandar
   `WATCHDOG=1` y systemd lo reinicia solo. Además, a nivel de hardware, `install.sh` activa el
   watchdog del SoC (`dtparam=watchdog=on` + `RuntimeWatchdogSec=15`) para el caso extremo de que
   el propio systemd no responda.
3. **Corte de luz o fallo de hardware sin aviso.** El latido ya no es un tema de Pub/Sub aparte
   (Pub/Sub se eliminó del diseño): es el documento `device_health/{device_id}`, escrito cada
   `HEARTBEAT_SECONDS` y en cada transición de estado. El dashboard lo traduce a "offline" después
   de ~11 minutos sin latido (sección 8, "La Pi aparece offline"). Para una alerta activa (correo/
   mensaje) sin depender de que alguien abra el dashboard, `edge.env` tiene `HEALTHCHECK_URL` para
   enchufar un servicio externo tipo healthchecks.io.
