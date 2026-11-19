#!/usr/bin/env bash
# Wrapper de cron para el job nocturno de TrackFlow.
#
# cron arranca con un entorno mínimo (sin PATH de usuario ni variables de BD),
# así que este script:
#   1. se sitúa en la raíz del monorepo (deducida de su propia ruta);
#   2. carga las URLs de BD desde un archivo de entorno FUERA del repo
#      (NIGHTLY_EXPORT_ENV_FILE, por defecto ~/.config/trackflow/nightly-export.env,
#      permisos 600);
#   3. ejecuta el job con el entorno uv reproducible del pyproject raíz.
#
# Variables requeridas en el archivo de entorno:
#   DATABASE_URL                   job_runs (fallback de lectura/escritura del job)
#   REPORTING_READ_DATABASE_URL    pipeline: lectura de public.telemetry_events
#   REPORTING_WRITE_DATABASE_URL   pipeline: escritura en reporting.*
# TARGET_DATE es opcional (por defecto: ayer en UTC).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${NIGHTLY_EXPORT_ENV_FILE:-$HOME/.config/trackflow/nightly-export.env}"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

if [[ ! -r "$ENV_FILE" ]]; then
  echo "$(date -u +%FT%TZ) job=nightly_export status=failed error=missing_env_file path=$ENV_FILE" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

for variable in DATABASE_URL REPORTING_READ_DATABASE_URL REPORTING_WRITE_DATABASE_URL; do
  if [[ -z "${!variable:-}" ]]; then
    echo "$(date -u +%FT%TZ) job=nightly_export status=failed error=missing_variable name=$variable" >&2
    exit 1
  fi
done

cd "$ROOT"
# flock evita solapes en este host; el lock de negocio entre hosts es la fila
# 'processing' de public.job_runs (services/job_runner.py).
exec flock -n /tmp/trackflow-nightly-export.lock uv run --frozen python scripts/nightly_export.py
