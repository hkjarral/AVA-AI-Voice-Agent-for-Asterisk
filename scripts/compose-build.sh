#!/usr/bin/env bash
# Build explicit Compose services without starting/recreating containers. This
# helper is also baked into the updater so rollback to an older ref can use it.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: compose-build.sh SERVICE [SERVICE ...] (honors COMPOSE_FILE and COMPOSE_PROJECT_NAME)" >&2
  exit 2
fi
for service in "$@"; do
  if ! [[ "$service" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
    echo "ERR: expected explicit service names, not options" >&2
    exit 2
  fi
done

build_dir="$(mktemp -d "${TMPDIR:-/tmp}/aava-compose-build.XXXXXXXX")"
trap 'rm -rf -- "$build_dir"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if docker compose build "$@" 2>&1 | tee "$build_dir/build.log"; then
  exit 0
fi
if ! grep -Fq 'additional privileges requested' "$build_dir/build.log" ||
   ! grep -Fq -- '--allow=network.host' "$build_dir/build.log"; then
  echo "ERR: docker compose build failed; no entitlement workaround applied" >&2
  exit 1
fi

echo "==> Retrying the selected builds with an explicit network.host grant" >&2
# stderr must remain separate: Compose can emit warnings with valid JSON stdout.
if ! docker compose build --print "$@" > "$build_dir/bake.json"; then
  echo "ERR: cannot export the Compose build; check docker compose/buildx versions" >&2
  exit 1
fi
docker buildx bake --file - --allow=network.host --load < "$build_dir/bake.json"
