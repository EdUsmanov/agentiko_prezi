#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Создайте окружение: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.lock'
  exit 1
fi
exec .venv/bin/python -m studio.cli serve "$@"
