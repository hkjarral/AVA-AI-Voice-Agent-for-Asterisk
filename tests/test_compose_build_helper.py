"""Execute the manual/rollback helper with a deterministic Docker CLI shim."""

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("failure", ["none", "entitlement", "unrelated", "export", "bake"])
def test_compose_build_helper_scopes_permission_and_never_starts_services(tmp_path, failure):
    log, definition = tmp_path / "commands", tmp_path / "definition.json"
    docker = tmp_path / "docker"
    docker.write_text(
        """#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_COMMANDS"
if [ "$1 $2 $3" = 'compose build --print' ]; then
  [ "$TEST_FAILURE" = export ] && exit 3
  echo warning >&2
  echo '{"group":{"default":{"targets":["ai_engine"]}},"target":{"ai_engine":{"network":"host","tags":["custom:latest"]}}}'
elif [ "$1 $2" = 'compose build' ]; then
  [ "$TEST_FAILURE" = none ] && exit 0
  [ "$TEST_FAILURE" = unrelated ] && { echo 'package download failed' >&2; exit 1; }
  echo 'additional privileges requested: pass "--allow=network.host" to grant requested privileges' >&2
  exit 1
elif [ "$1 $2" = 'buildx bake' ]; then
  cat > "$TEST_DEFINITION"
  [ "$TEST_FAILURE" = bake ] && exit 4
  exit 0
else
  exit 9
fi
"""
    )
    docker.chmod(0o700)
    environment = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
                       TMPDIR=str(tmp_path), TEST_COMMANDS=str(log), TEST_DEFINITION=str(definition),
                       TEST_FAILURE=failure, COMPOSE_PROJECT_NAME="synthetic-project")
    result = subprocess.run(["bash", str(ROOT / "scripts/compose-build.sh"), "ai_engine", "admin_ui"],
                            env=environment, capture_output=True, text=True)
    assert (result.returncode == 0) == (failure in {"none", "entitlement"}), result.stdout + result.stderr
    commands = log.read_text().splitlines()
    assert commands[0] == "compose build ai_engine admin_ui"
    assert not any(command.startswith("compose up") for command in commands)
    if failure in {"none", "unrelated"}:
        assert len(commands) == 1
    if failure in {"entitlement", "bake"}:
        assert commands[-1] == "buildx bake --file - --allow=network.host --load"
        assert json.loads(definition.read_text())["target"]["ai_engine"]["tags"] == ["custom:latest"]
    assert not list(tmp_path.glob("aava-compose-build.*")), "private temporary build logs must be removed"


def test_compose_build_requires_explicit_service_names(tmp_path):
    result = subprocess.run(["bash", str(ROOT / "scripts/compose-build.sh"), "--push"], capture_output=True)
    assert result.returncode == 2
