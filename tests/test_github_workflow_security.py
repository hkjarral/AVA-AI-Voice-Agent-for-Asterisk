from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


def _workflow_steps() -> list[tuple[Path, dict]]:
    steps: list[tuple[Path, dict]] = []
    for path in sorted(WORKFLOW_ROOT.glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (workflow.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                steps.append((path, step))
    return steps


def test_read_only_checkouts_do_not_persist_credentials() -> None:
    checkouts = [
        (path, step)
        for path, step in _workflow_steps()
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]

    assert checkouts
    for path, step in checkouts:
        assert (step.get("with") or {}).get("persist-credentials") is False, path


def test_release_go_setup_disables_shared_cache() -> None:
    path = WORKFLOW_ROOT / "release-cli.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    setup_go_steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps") or []
        if str(step.get("uses", "")).startswith("actions/setup-go@")
    ]

    assert setup_go_steps
    for step in setup_go_steps:
        assert (step.get("with") or {}).get("cache") is False
