"""Execute the shared build runner; no Docker daemon or external network needed."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/compose-build.sh"


@pytest.fixture
def docker_fixture(tmp_path):
    log = tmp_path / "commands.jsonl"
    docker = tmp_path / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["TEST_COMMANDS"], "a") as f:
    f.write(json.dumps({"args": args, "builder": os.environ.get("BUILDX_BUILDER"), "context": os.environ.get("DOCKER_CONTEXT")}) + "\\n")
failure = os.environ.get("TEST_FAILURE", "none")
if args[:2] == ["compose", "version"]: sys.exit(0)
elif args[:2] == ["context", "show"]: print("synthetic-context")
elif args[0] == "info": print("synthetic-engine")
elif args[:2] == ["buildx", "inspect"]:
    if failure == "builder": sys.exit(1)
    print("Driver: docker\\nStatus: running")
elif "--help" in args:
    print("unsupported" if failure == "capability" else "--print --allow --load")
elif "--print" in args:
    if failure == "export": sys.exit(3)
    print("Compose warning on stderr", file=sys.stderr)
    services = [a for a in args if a in ("ai_engine", "admin_ui", "local_ai_server")]
    b = {"target": {s: {"network": "host", "tags": ["synthetic:latest"]} for s in services}}
    if failure == "invalid": b = {"target": {}}
    if failure == "extra": b["target"]["unexpected"] = {"network": "host"}
    if failure == "indirect": b["target"][services[0]]["inherits"] = ["unexpected"]
    print(json.dumps(b))
elif args[:2] == ["buildx", "bake"]:
    b = json.load(sys.stdin)
    with open(os.environ["TEST_DEFINITION"], "w") as f: json.dump(b, f)
    if failure == "bake":
        print("lookup registry-1.docker.io: i/o timeout", file=sys.stderr); sys.exit(4)
elif "build" in args:
    if failure == "none": sys.exit(0)
    errors = {"unrelated": "package download failed", "dns": "failed to resolve source metadata: lookup registry-1.docker.io: i/o timeout", "disk": "no space left on device", "auth": "unauthorized: authentication required"}
    print(errors.get(failure, 'additional privileges requested: pass "--allow=network.host"'), file=sys.stderr)
    sys.exit(1)
else: sys.exit(9)
''')
    docker.chmod(0o700)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
               TMPDIR=str(tmp_path), TEST_COMMANDS=str(log),
               TEST_DEFINITION=str(tmp_path / "definition.json"))
    for key in ("BUILDX_BUILDER", "DOCKER_CONTEXT", "DOCKER_HOST"):
        env.pop(key, None)
    return env, log


@pytest.mark.parametrize("failure", ["none", "entitlement", "unrelated", "export", "bake", "invalid", "extra", "indirect", "builder", "capability", "dns", "disk", "auth"])
@pytest.mark.parametrize("builder", [None, "explicit-custom"])
def test_shared_policy_scopes_builder_privilege_and_targets(tmp_path, docker_fixture, failure, builder):
    env, log = docker_fixture
    env["TEST_FAILURE"] = failure
    if builder:
        env["BUILDX_BUILDER"] = builder
    result = subprocess.run(["bash", str(RUNNER), "-p", "synthetic", "--no-cache", "--build-arg", "SAFE=value with spaces;$(touch injected)", "ai_engine", "admin_ui"], env=env, capture_output=True, text=True)
    assert (result.returncode == 0) == (failure in {"none", "entitlement"}), result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(c["builder"] == (builder or "default") for c in calls if c["args"][:2] != ["compose", "version"])
    assert all(c["context"] == "synthetic-context" for c in calls if c["args"][:2] not in (["context", "show"], ["compose", "version"]))
    assert not any("up" in c["args"] or "use" in c["args"] or "create" in c["args"] for c in calls)
    bakes = [c["args"] for c in calls if c["args"][:2] == ["buildx", "bake"] and "--help" not in c["args"]]
    assert bool(bakes) == (failure in {"entitlement", "bake"})
    if bakes:
        assert bakes == [["buildx", "bake", "--file", "-", "--allow=network.host", "--load", "ai_engine", "admin_ui"]]
        definition = json.loads((tmp_path / "definition.json").read_text())
        assert set(definition["target"]) == {"ai_engine", "admin_ui"}
    exports = [c["args"] for c in calls if "--print" in c["args"]]
    if exports:
        assert "--no-cache" in exports[0] and "synthetic" in exports[0]
        assert "SAFE=value with spaces;$(touch injected)" in exports[0]
    if failure in {"dns", "bake"}: assert "BuildKit daemon" in result.stderr
    if failure == "disk": assert "storage is full" in result.stderr
    if failure == "auth": assert "authentication/rate limit" in result.stderr
    assert not (tmp_path / "injected").exists()
    assert not list(tmp_path.glob("aava-compose-build.*"))


def test_preflight_report_is_read_only_and_contains_no_endpoint_credentials(docker_fixture):
    env, log = docker_fixture
    env["DOCKER_HOST"] = "tcp://secret@synthetic:2376"
    result = subprocess.run(["bash", str(RUNNER), "--check-json"], env=env, capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["builder"] == "default" and report["connectivity"] == "not-probed"
    assert "secret" not in result.stdout
    calls = [json.loads(line)["args"] for line in log.read_text().splitlines()]
    assert not any("build" in c and "--help" not in c for c in calls)
    assert not any("--bootstrap" in c for c in calls)


def test_direct_updater_image_build_uses_same_builder(docker_fixture):
    env, log = docker_fixture
    env["BUILDX_BUILDER"] = "explicit-custom"
    subprocess.run(["bash", str(RUNNER), "--docker-build", "-f", "updater/Dockerfile", "-t", "synthetic:latest", "."], env=env, check=True, capture_output=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[-1]["args"] == ["build", "-f", "updater/Dockerfile", "-t", "synthetic:latest", ".", "--builder", "explicit-custom", "--network=host", "--load"]


def test_compose_build_requires_explicit_service_names(tmp_path):
    result = subprocess.run(["bash", str(RUNNER), "--push"], capture_output=True)
    assert result.returncode == 2
