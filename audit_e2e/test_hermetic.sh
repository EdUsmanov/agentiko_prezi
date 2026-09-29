#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image=${E2E_IMAGE:-presentation-eval-hermetic:1}
run_id=$(date -u +%Y%m%dT%H%M%SZ)-$$
run_dir="audit_e2e/results/hermetic/$run_id"

cd "$repo_root"
mkdir -p "$run_dir"

docker build --pull=false -f audit_e2e/Dockerfile -t "$image" .
docker image inspect --format '{{.Id}}' "$image" > "$run_dir/image-id.txt"
docker run --rm \
  --network none \
  --volume "$repo_root/$run_dir:/app/audit_e2e/results" \
  --workdir /app \
  --env TZ=UTC \
  "$image" \
  /bin/sh -eu -c '
    python -m pytest -q \
      audit_e2e/tests/test_eval_runtime.py \
      audit_e2e/e2e/test_evaluation_workflows.py \
      --basetemp=/app/audit_e2e/results/pytest-tmp \
      --junitxml=/app/audit_e2e/results/e2e.xml
    python -m audit_e2e run \
      --mode replay \
      --suite core \
      --generation-only \
      --timeout 900 \
      --output /app/audit_e2e/results/core-run
  '
