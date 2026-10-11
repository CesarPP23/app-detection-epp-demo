#!/usr/bin/env bash
# Fase 4.3 - Atajo con el orden numérico de infra/: delega en deploy_cloud.sh (mismos argumentos).
# Uso: ./infra/03_deploy_cloud.sh <PUBLIC_BASE_URL> <GOOGLE_OAUTH_CLIENT_ID> [--build-sa]
set -euo pipefail
exec "$(dirname "$0")/deploy_cloud.sh" "$@"
