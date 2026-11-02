#!/usr/bin/env bash
# Build explicit Compose services without starting/recreating containers. This
# helper is also baked into the updater so rollback to an older ref can use it.
set -euo pipefail

# A standalone Compose v2 binary can be selected by preflight/installer.
# Keep this as one argv element; never evaluate a command string.
if [ -n "${AAVA_COMPOSE_BIN:-}" ]; then
  compose_options=("$AAVA_COMPOSE_BIN")
else
  compose_options=(docker compose)
fi
build_options=(build)
services=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -f|--file|-p|--project-name|--build-arg)
      option="$1"
      if [ "$#" -lt 2 ] || [ -z "$2" ] || [[ "$2" == -* ]]; then
        echo "ERR: $option requires a value" >&2
        exit 2
      fi
      if [ "$option" = --build-arg ]; then
        build_options+=("$option" "$2")
      else
        compose_options+=("$option" "$2")
      fi
      shift 2
      ;;
    --no-cache)
      build_options+=("$1")
      shift
      ;;
    *)
      if ! [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
        echo "ERR: expected explicit service names or supported build options" >&2
        exit 2
      fi
      services+=("$1")
      shift
      ;;
  esac
done
if [ "${#services[@]}" -eq 0 ]; then
  echo "Usage: compose-build.sh [-f FILE] [-p PROJECT] [--no-cache] [--build-arg KEY=VALUE] SERVICE [SERVICE ...]" >&2
  echo "Honors COMPOSE_FILE and COMPOSE_PROJECT_NAME." >&2
  exit 2
fi

build_dir="$(mktemp -d "${TMPDIR:-/tmp}/aava-compose-build.XXXXXXXX")"
trap 'rm -rf -- "$build_dir"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if "${compose_options[@]}" "${build_options[@]}" "${services[@]}" 2>&1 | tee "$build_dir/build.log"; then
  exit 0
fi
if ! grep -Fq 'additional privileges requested' "$build_dir/build.log" ||
   ! grep -Fq -- '--allow=network.host' "$build_dir/build.log"; then
  echo "ERR: docker compose build failed; no entitlement workaround applied" >&2
  exit 1
fi

echo "==> Retrying the selected builds with an explicit network.host grant" >&2
# stderr must remain separate: Compose can emit warnings with valid JSON stdout.
if ! "${compose_options[@]}" "${build_options[@]}" --print "${services[@]}" > "$build_dir/bake.json"; then
  echo "ERR: cannot export the Compose build; check docker compose/buildx versions" >&2
  exit 1
fi
docker buildx bake --file - --allow=network.host --load < "$build_dir/bake.json"
