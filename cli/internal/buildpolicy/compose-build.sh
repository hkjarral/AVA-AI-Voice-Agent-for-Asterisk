#!/usr/bin/env bash
# Build explicit Compose services without starting/recreating containers. This
# helper is also baked into the updater so rollback to an older ref can use it.
set -euo pipefail

compose_options=(compose)
build_options=(build)
services=()
check_only=false
json_check=false
docker_build=false
docker_args=(build)
if [ "${1:-}" = --docker-build ]; then
  docker_build=true
  shift
  [ "$#" -gt 0 ] || { echo "ERR: Docker build arguments required" >&2; exit 2; }
  docker_args+=("$@")
  set --
fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    --check|--check-json)
      check_only=true
      [ "$1" != --check-json ] || json_check=true
      shift
      ;;
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
if [ "${#services[@]}" -eq 0 ] && [ "$check_only" = false ] && [ "$docker_build" = false ]; then
  echo "Usage: compose-build.sh [-f FILE] [-p PROJECT] [--no-cache] [--build-arg KEY=VALUE] SERVICE [SERVICE ...]" >&2
  echo "Honors COMPOSE_FILE and COMPOSE_PROJECT_NAME." >&2
  exit 2
fi

# A standalone Compose v2 binary can be selected by preflight/installer.
# Keep this as one argv element; never evaluate a command string.
if [ -n "${AAVA_COMPOSE_BIN:-}" ]; then
  compose_options[0]="$AAVA_COMPOSE_BIN"
elif docker compose version >/dev/null 2>&1; then
  compose_options=(docker "${compose_options[@]}")
elif command -v docker-compose >/dev/null 2>&1; then
  compose_options[0]=docker-compose
else
  echo "ERR: Docker Compose plugin or standalone Compose 2+ is required" >&2
  exit 1
fi

build_dir="$(mktemp -d "${TMPDIR:-/tmp}/aava-compose-build.XXXXXXXX")"
trap 'rm -rf -- "$build_dir"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Resolve once per invocation, never alter the globally selected builder/context.
export BUILDX_BUILDER="${BUILDX_BUILDER:-default}"
if ! [[ "$BUILDX_BUILDER" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
  echo "ERR: invalid BUILDX_BUILDER name" >&2
  exit 2
fi
if ! command -v python3 >/dev/null; then
  echo "ERR: python3 is required to validate Docker build metadata; install it and rerun preflight" >&2
  exit 1
fi
# Pin a named context for the duration of the operation. DOCKER_HOST already
# pins an endpoint and takes its existing precedence when no context was set.
if [ -z "${DOCKER_HOST:-}" ]; then
  export DOCKER_CONTEXT="${DOCKER_CONTEXT:-$(docker context show)}"
fi
if ! docker info --format '{{.ID}}' > "$build_dir/engine" 2> "$build_dir/error"; then
  echo "ERR: Docker daemon unavailable to UID $(id -u). Check socket permissions and Docker context with this same user; sudo uses a different Docker configuration." >&2
  exit 1
fi
if ! docker context show > "$build_dir/context" ||
   ! docker buildx inspect "$BUILDX_BUILDER" > "$build_dir/builder.txt" 2> "$build_dir/error"; then
  echo "ERR: builder '$BUILDX_BUILDER' cannot be inspected in this Docker context. Run docker buildx inspect with the same user/context. An explicit BUILDX_BUILDER override is never silently replaced." >&2
  exit 1
fi
if ! "${compose_options[@]}" build --help > "$build_dir/compose-help" ||
   ! docker buildx bake --help > "$build_dir/bake-help" ||
   ! grep -Fq -- '--print' "$build_dir/compose-help" ||
   ! grep -Fq -- '--allow' "$build_dir/bake-help" ||
   ! grep -Fq -- '--load' "$build_dir/bake-help"; then
  echo "ERR: Compose build --print and Buildx Bake --allow/--load are required; upgrade Docker Compose/Buildx and rerun preflight" >&2
  exit 1
fi
if ! python3 - "$build_dir" "$BUILDX_BUILDER" "$json_check" <<'PYCHECK'
import json, os, pathlib, re, sys
p = pathlib.Path(sys.argv[1])
try:
    # inspect --format is absent even in Buildx 0.31. Parse only the labelled
    # fields of its text report, without exposing endpoint/proxy credentials.
    raw = (p / "builder.txt").read_text()
    driver = re.search(r"^Driver:\s+(\S+)", raw, re.M)
    statuses = re.findall(r"^Status:\s+(\S+)", raw, re.M)
    if not driver or not statuses or any(s not in {"running", "stopped", "inactive"} for s in statuses) or re.search(r"^Error:", raw, re.M):
        raise ValueError("incomplete/error builder description; run docker buildx inspect")
    # A stopped container builder is bootstrapped by the build, never this check.
    report = {"builder": sys.argv[2], "driver": driver.group(1),
              "context": (p / "context").read_text().strip(), "uid": os.getuid(),
              "engine_id": (p / "engine").read_text().strip(), "status": statuses,
              "connectivity": "not-probed"}
    if sys.argv[3] == "true":
        print(json.dumps(report))
    else:
        print("AVA build: builder=%s driver=%s context=%s uid=%s status=%s" %
              (report["builder"], report["driver"], report["context"], report["uid"], ",".join(statuses)), file=sys.stderr)
except (ValueError, TypeError, KeyError) as e:
    print("ERR: invalid/unavailable builder metadata: %s" % e, file=sys.stderr)
    sys.exit(1)
PYCHECK
then
  exit 1
fi
if [ "$check_only" = true ]; then
  exit 0
fi

explain_failure() {
  if grep -Eqi 'no space left|disk quota' "$build_dir/build.log"; then
    echo "ERR: Docker build storage is full. Inspect docker system df; remove only operator-approved unused resources." >&2
  elif grep -Eqi 'unauthorized|authentication required|toomanyrequests|pull rate limit|429 Too Many' "$build_dir/build.log"; then
    echo "ERR: registry authentication/rate limit failure. Check credentials or registry limits for this Docker identity." >&2
  elif grep -Eqi 'lookup .*timeout|no such host|could not resolve|temporary failure.*resol|failed to resolve source metadata|resolve host|dial tcp.*timeout' "$build_dir/build.log"; then
    echo "ERR: build DNS/egress failure on builder '$BUILDX_BUILDER'. Metadata/FROM requests use the BuildKit daemon's network; RUN package downloads use the build-step network. A successful host lookup or docker pull does not validate a custom builder. Inspect its DNS/proxy/egress; network.host cannot repair daemon DNS. No daemon configuration was changed. To test the built-in builder, explicitly retry with BUILDX_BUILDER=default." >&2
  else
    echo "ERR: build failed; inspect the build output above. No additional entitlement or builder change applied." >&2
  fi
}
if [ "$docker_build" = true ]; then
  if docker "${docker_args[@]}" --builder "$BUILDX_BUILDER" --network=host 2>&1 | tee "$build_dir/build.log"; then
    exit 0
  fi
  explain_failure
  exit 1
fi
if "${compose_options[@]}" "${build_options[@]}" "${services[@]}" 2>&1 | tee "$build_dir/build.log"; then
  exit 0
fi
if ! grep -Fq 'additional privileges requested' "$build_dir/build.log" ||
   ! grep -Fq -- '--allow=network.host' "$build_dir/build.log"; then
  explain_failure
  exit 1
fi

echo "==> Retrying the selected builds with an explicit network.host grant (builder=$BUILDX_BUILDER)" >&2
# Keep warnings out of the JSON document; preserve all Compose/build options.
if ! "${compose_options[@]}" "${build_options[@]}" --print "${services[@]}" > "$build_dir/bake.json"; then
  echo "ERR: cannot export the Compose build; check docker compose/buildx versions" >&2
  exit 1
fi
# Reject empty/unrelated/extra targets before granting any privilege. Use explicit
# target argv as well, so a malformed default group cannot widen the selection.
if ! python3 - "$build_dir/bake.json" "${services[@]}" <<'PYBAKE'
import json, sys
try:
    b = json.load(open(sys.argv[1]))
    targets = b.get("target")
    if not isinstance(targets, dict) or set(targets) != set(sys.argv[2:]):
        raise ValueError("Bake targets must exactly match the requested services")
    for name, target in targets.items():
        if not isinstance(target, dict) or not target:
            raise ValueError("empty or invalid build target: " + name)
        if target.get("inherits") or target.get("contexts"):
            raise ValueError("indirect Bake targets are unsupported by scoped recovery")
except (ValueError, TypeError, AttributeError) as e:
    print("ERR: invalid Compose Bake export: " + str(e), file=sys.stderr)
    sys.exit(1)
PYBAKE
then
  exit 1
fi
if docker buildx bake --file - --allow=network.host --load "${services[@]}" < "$build_dir/bake.json" 2>&1 | tee "$build_dir/build.log"; then
  exit 0
fi
explain_failure
exit 1
