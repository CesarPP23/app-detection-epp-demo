# 0008 — Login con Google Identity Services + cookie de sesión propia

**Para quién es:** quien evalúe el mecanismo de autenticación del dashboard.
**Última verificación:** 2026-10-11.

## Contexto

El dashboard anterior no tenía autenticación alguna (hallazgo S3): cualquiera que conociera la URL
podía ver video y capturas de trabajadores, y pausar la detección. La decisión del usuario fue
exponer el dashboard por internet (decisión U3), lo que hace la autenticación un prerrequisito
antes de cualquier otra cosa en la Fase 3. El proyecto ya usa cuentas de Google para administrar
GCP, así que pedirle a cada usuario una cuenta y contraseña nueva (propia del sistema) habría sido
una fricción adicional sin necesidad.

## Decisión

Login con **Google Identity Services** (GIS): la SPA muestra el botón oficial de Google, recibe
un **ID token** firmado por Google y lo envía al backend (`POST /api/auth/google`), que lo
verifica contra `GOOGLE_OAUTH_CLIENT_ID` con la librería oficial de Google
(`google.oauth2.id_token.verify_oauth2_token`). Si el correo está en la lista blanca
(`dashboard_users`, activo), el backend emite su **propia cookie de sesión**
(`epp_session`, firmada con `itsdangerous`, `HttpOnly; Secure; SameSite=Strict`) — no se guarda
ningún token de Google en el navegador, y no hace falta un *client secret* porque el flujo de ID
token no lo requiere.

## Alternativas consideradas

- **OAuth 2.0 con client secret y sesión del lado del servidor vía `Authorization Code Flow`**: más
  apropiado cuando la aplicación necesita llamar a APIs de Google en nombre del usuario (Gmail,
  Calendar, etc.). Aquí no se necesita nada de eso — solo verificar la identidad una vez al iniciar
  sesión — así que el flujo de ID token de GIS es más simple y no requiere guardar un client
  secret en ningún lugar.
- **Guardar el token de Google en el navegador y reenviarlo en cada request**: expondría el token
  de Google a cualquier XSS y atarían la sesión a la vigencia del token de Google (más corta y
  fuera de nuestro control). Una cookie de sesión propia, firmada por el backend, permite fijar la
  vida de la sesión (`SESSION_MAX_HOURS`) y revocarla revisando `dashboard_users`, no revocando
  nada del lado de Google.
- **Credenciales propias del sistema (usuario + contraseña)**: implica guardar y rotar contraseñas,
  recuperación de cuenta, etc. — trabajo de seguridad adicional sin beneficio claro cuando todos
  los usuarios esperados ya tienen cuenta de Google.
- **IAM de Cloud Run con `--no-allow-unauthenticated`**: exigiría que cada usuario del dashboard
  tuviera una identidad de Google **dentro del proyecto de GCP** (o federada con IAM), lo que no
  escala a usuarios externos a la organización y tampoco resuelve la autenticación de la Pi
  (que no es una identidad de Google). Se optó por `--allow-unauthenticated` + autenticación en la
  capa de aplicación (ver `docs/SECURITY.md`, "Por qué `--allow-unauthenticated` es aceptable").

## Consecuencias

- Revocar el acceso de alguien (desactivarlo en `dashboard_users`) tarda como máximo 60 s en
  reflejarse, porque el rol se revalida contra Firestore con esa caché — no hace falta esperar a
  que expire la cookie.
- La app de OAuth quedó en Google Auth Platform en modo **Testing** (no Production), porque
  publicarla exige un dominio propio y el servicio usa la URL `*.run.app`; cada usuario nuevo debe
  añadirse también a mano como "Test user" en la consola de Google, además de en
  `dashboard_users` — documentado como limitación en `docs/SECURITY.md`.
- CSRF se resuelve sin tokens adicionales: al no haber CORS habilitado, exigir la cabecera
  `X-Requested-With: epp-web` en toda request que muta es suficiente (un origen cruzado no puede
  agregar esa cabecera a una request simple).
- La Raspberry Pi **no** pasa por este flujo: se autentica con `DEVICE_TOKEN` (un secreto
  compartido), porque no tiene una cuenta de Google ni una interfaz para iniciar sesión.
