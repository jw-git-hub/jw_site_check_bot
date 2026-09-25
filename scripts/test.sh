#!/usr/bin/env bash
# Тесты без сети. Окружение — вне папки проекта: она синхронизируется Syncthing,
# поэтому ни venv, ни __pycache__, ни кэша pytest здесь быть не должно.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${HOME}/.cache/jw_site_check_bot/venv"
PYTHON_VERSION="3.12"
export PYTHONDONTWRITEBYTECODE=1
[ -x "$VENV/bin/python" ] || uv venv --quiet --python "$PYTHON_VERSION" "$VENV"
VIRTUAL_ENV="$VENV" uv pip install --quiet -r requirements.txt -r requirements-dev.txt
exec "$VENV/bin/python" -m pytest "$@"
