"""Exercise the CPU Dockerfile's actual apt RUN commands with a stale base package."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest


DOCKERFILE = Path(__file__).resolve().parents[1] / "local_ai_server" / "Dockerfile"
FIXED_VERSION = "1.5.7+dfsg-1+deb13u1"


@pytest.mark.parametrize("stage_index", [0, 1], ids=["builder", "runtime"])
@pytest.mark.parametrize(
    "candidate,query_status,success",
    [
        (FIXED_VERSION, 0, True),
        ("1.5.7+dfsg-1+deb13u2", 0, True),
        ("1.5.7+dfsg-1", 0, False),
        (FIXED_VERSION, 1, False),
    ],
    ids=["fixed", "newer", "stale-mirror", "query-failed"],
)
def test_zstd_floor(tmp_path, stage_index, candidate, query_status, success):
    dpkg = shutil.which("dpkg")
    if dpkg is None:
        pytest.skip("Debian version comparison requires dpkg")

    stages = re.split(r"^FROM ", DOCKERFILE.read_text(), flags=re.MULTILINE)[1:]
    (install,) = re.findall(
        r"^RUN (apt-get [^\n]*)", stages[stage_index].replace("\\\n", " "), re.MULTILINE
    )

    # Only an explicit apt install replaces the stale base version. Never run
    # host apt or remove host files. dpkg itself performs Debian version ordering.
    (tmp_path / "installed").write_text("1.5.7+dfsg-1\n")
    (tmp_path / "dpkg").symlink_to(dpkg)
    commands = {
        "apt-get": """
case " $* " in
    *" install "*" libzstd1 "*) echo "$CANDIDATE" > installed ;;
esac
""",
        "dpkg-query": """
case "$3" in
    libzstd1) read -r version < installed; printf '%s' "$version"; exit "$QUERY_STATUS" ;;
    libc6|libc-bin) echo '2.41-12+deb13u5' ;;
    zlib1g) echo '1:1.3.dfsg+really1.3.1-1+deb13u1' ;;
    *) exit 1 ;;
esac
""",
        "rm": "exit 0",
    }
    for name, body in commands.items():
        command = tmp_path / name
        command.write_text("#!/bin/sh\n" + body + "\n")
        command.chmod(0o755)

    result = subprocess.run(
        ["/bin/sh", "-c", install],
        cwd=tmp_path,
        env={"PATH": str(tmp_path), "CANDIDATE": candidate, "QUERY_STATUS": str(query_status)},
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    if success:
        assert f"libzstd1={candidate}" in result.stdout
