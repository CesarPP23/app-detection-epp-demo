# Privacidad

**Para quién es:** quien evalúe el tratamiento de datos personales del sistema, atienda una
solicitud de un trabajador fotografiado, o decida si el sistema es apto para un contexto real
(no solo de tesis). **Última verificación:** 2026-10-11.

## 1. Datos personales tratados

El sistema procesa **imágenes de personas** (trabajadores frente a la cámara de la Pi) con el fin
de verificar visualmente el uso de EPP. Concretamente:

- **Imagen original** (`raw/images/.../{event_id}.jpg`): el frame completo, sin anotar, de mayor
  confianza de cada evento confirmado. Puede incluir el rostro y otros rasgos identificables de
  quien esté frente a la cámara.
- **Miniatura anotada** (`raw/thumbs/.../{event_id}.jpg`): la misma escena, redimensionada, con las
  cajas de detección dibujadas. Es lo que ve el dashboard por defecto.
- **Metadatos del evento** (JSON/Firestore): clase detectada, confianza, hora, duración y
  coordenadas normalizadas de las cajas. No incluyen nombre, identificador de empleado ni ningún
  otro dato que vincule la imagen con una identidad — esa asociación solo existe si alguien con
  acceso a las imágenes reconoce a la persona a simple vista.
- **Cuenta del dashboard**: email de Google de cada usuario autorizado (`dashboard_users`),
  registrado también en cada entrada de `audit_log` que genere.

El sistema **no** hace reconocimiento facial, no identifica personas por nombre, y no cruza las
imágenes con ningún otro sistema de recursos humanos.

## 2. Base legal

Este es un proyecto de **tesis académica**: no opera hoy sobre trabajadores reales en una empresa.
Si se desplegara en un entorno productivo con personas reales, la base legal aplicable en Perú es
la **Ley N.º 29733, Ley de Protección de Datos Personales**, que exige entre otras cosas:

- Consentimiento previo, informado y expreso del titular de los datos (o una base legal
  alternativa, como el interés legítimo del empleador en verificar seguridad laboral, que debe
  evaluarse caso por caso con asesoría legal antes de un despliegue real).
- Finalidad definida y no excesiva: aquí, exclusivamente verificar uso de EPP.
- Medidas de seguridad proporcionales al riesgo (ver `docs/SECURITY.md`).
- Derecho de acceso, rectificación, cancelación y oposición (ARCO) del titular sobre sus datos.

Este documento no sustituye una revisión legal formal; es la documentación técnica de qué hace el
sistema con esos datos, pensada para servir de insumo a esa revisión si el sistema se usa fuera
del contexto de tesis.

## 3. Retención

- **Imágenes** (original y miniatura): **60 días** (decisión U6). Pasan a almacenamiento Nearline
  a los 30 días y se borran automáticamente a los 60 (`infra/policies/lake-lifecycle.json`, ver
  `docs/DATA_LAKE.md`). Transcurrido ese plazo, la API responde `capture_status = "expired"` y el
  evento queda sin imagen, aunque sus metadatos estructurados permanezcan.
- **Metadatos del evento** (JSON en el lake, documento en Firestore, fila en BigQuery): **sin
  fecha de expiración**. Se consideran datos no personales (no identifican a nadie sin la imagen),
  y son la base de los reportes históricos de cumplimiento.
- **`audit_log`**: 365 días (TTL de Firestore sobre `expire_at`, `infra/04_firestore.sh`).
- **Cuentas del dashboard** (`dashboard_users`): sin expiración automática; un administrador las
  desactiva o elimina manualmente desde *Administración*.

## 4. Quién accede

| Rol | Acceso a imágenes | Acceso a metadatos | Notas |
|---|---|---|---|
| `viewer` | Miniatura siempre; imagen original mediante `GET /api/events/{id}/capture?kind=original` | Lectura completa de eventos, resumen, estado del dispositivo | Cualquier acceso a la imagen original queda en `audit_log` (`capture_original`) |
| `operator` | Igual que `viewer` | + puede pausar/reanudar la detección | |
| `admin` | Igual que `viewer` | + usuarios, auditoría, y es el único rol que ve `lake_uri` (la ruta `gs://` cruda) en el detalle de un evento | |
| La Raspberry Pi | Puede **crear** objetos en el lake, nunca leerlos ni borrarlos (`storage.objectCreator`) | Escribe eventos, no los lee de vuelta | |
| Cualquier visitante sin sesión | Ninguno | Ninguno | El servicio exige login de Google + lista blanca antes de cualquier dato (`docs/SECURITY.md`) |

Toda cuenta nueva del dashboard la crea un `admin` desde la pantalla de Administración; no hay
autorregistro.

## 5. Cómo atender una solicitud de eliminación

Si una persona identificada en una captura solicita su eliminación antes de que expire por
retención:

1. Un `admin` identifica el o los `event_id` relevantes (por fecha, dispositivo y clase, desde la
   pantalla *Eventos*, o consultando directamente Firestore/BigQuery por `event_id` si se conoce).
2. Borra los objetos `raw/images/.../{event_id}.jpg` y `raw/thumbs/.../{event_id}.jpg` del bucket
   con una cuenta con permiso de borrado (la SA de runtime del backend **no** tiene
   `storage.objectDeleter` hoy — solo `objectViewer`; esta acción requiere una cuenta con más
   privilegio, como la del propio administrador del proyecto en la consola o `gsutil`/`gcloud
   storage rm` con credenciales de usuario). **La Pi no puede hacerlo**: solo tiene
   `storage.objectCreator`.
3. Actualiza el documento de Firestore correspondiente para reflejar `capture.status = "deleted"`
   o similar, o simplemente deja que la API siga esperando `expired`/`missing` según el caso (el
   código actual no distingue "borrado por solicitud" de "borrado por retención"; es una mejora
   pendiente si esto se usa en producción real, ver sección 7 de trabajo futuro del `README.md`).
4. El JSON del evento y el documento de Firestore (metadatos estructurados, sin la imagen)
   normalmente **no** se eliminan, porque por sí solos no identifican a la persona — a menos que la
   solicitud de eliminación lo pida explícitamente, en cuyo caso se borran también.
5. Se registra la acción en `audit_log` a mano (hoy no hay un endpoint dedicado a "eliminación por
   solicitud"; es responsabilidad operativa del administrador documentarlo fuera del sistema hasta
   que exista esa función).

## 6. Riesgo de no difuminar rostros (Q7)

**Decisión consciente, no un descuido:** las imágenes del lake se guardan sin difuminar rostros ni
ningún otro rasgo identificable. Se decidió así (decisión Q7 de `docs/PLAN_REMEDIACION.md`) porque:

- El difuminado automático (detección de rostro + blur) agrega una dependencia y cómputo extra en
  el borde, y el proyecto es una tesis con alcance ya definido.
- La imagen original sin difuminar es más útil para el caso de uso previsto en "Trabajo futuro"
  (reentrenamiento, clases de ausencia de EPP, tracking por persona): difuminar de forma
  irreversible destruiría esa utilidad.

**Riesgo que esto implica:** cualquier persona con acceso al dashboard (rol `viewer` o superior) o
al bucket del lake puede ver el rostro de los trabajadores capturados. Mitigaciones existentes:
acceso restringido por lista blanca y roles, URLs firmadas de vida corta (10 minutos), retención de
60 días, y auditoría de cada descarga de imagen original. Si este sistema se usara con trabajadores
reales fuera de un contexto de tesis, difuminar rostros (o, mejor, obtener consentimiento informado
explícito) debería revisarse antes del despliegue — queda documentado aquí como la mejora de
privacidad más directa pendiente.
