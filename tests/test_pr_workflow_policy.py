from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONCURRENT_WORKFLOWS = (
    "ci.yml",
    "codeql.yml",
    "block-dev-artifacts.yml",
    "catalog-url-check.yml",
    "trivy.yml",
    "local-ai-gpu-build.yml",
    "regression-hardening.yml",
)


def _load_yaml(path: Path) -> dict:
    # BaseLoader preserves GitHub's `on` key instead of treating it as a YAML
    # 1.1 boolean and is sufficient for these structural policy assertions.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_pr_workflows_cancel_superseded_runs() -> None:
    workflows = ROOT / ".github" / "workflows"
    for filename in CONCURRENT_WORKFLOWS:
        workflow = _load_yaml(workflows / filename)
        concurrency = workflow["concurrency"]
        assert "github.event_name" in concurrency["group"]
        assert "github.event.pull_request.number || github.ref" in concurrency["group"]
        assert concurrency["cancel-in-progress"] == "true"


def test_ci_has_fast_draft_and_final_validation_gate() -> None:
    workflow = _load_yaml(ROOT / ".github" / "workflows" / "ci.yml")

    pull_request = workflow["on"]["pull_request"]
    assert "labeled" in pull_request["types"]
    assert "ready_for_review" not in pull_request["types"]

    image_condition = workflow["jobs"]["image-size"]["if"]
    assert "full-ci" in image_condition
    assert "github.event.pull_request.draft == false" in image_condition
    assert "github.event.action != 'labeled'" not in image_condition

    gate = workflow["jobs"]["pr-gate"]
    assert gate["name"] == "PR gate"
    assert set(gate["needs"]) == {
        "build",
        "admin-backend-tests",
        "admin-frontend-tests",
        "image-size",
        "cli-cross-compile",
    }
    assert "full-ci" in gate["env"]["FINAL_VALIDATION_REQUIRED"]


def test_path_scoped_docker_workflows_wait_for_final_validation() -> None:
    workflows = ROOT / ".github" / "workflows"
    for filename, job_name in (
        ("trivy.yml", "scan"),
        ("local-ai-gpu-build.yml", "build-gpu-image"),
    ):
        workflow = _load_yaml(workflows / filename)
        pull_request = workflow["on"]["pull_request"]
        assert "labeled" in pull_request["types"]
        assert "ready_for_review" not in pull_request["types"]

        condition = workflow["jobs"][job_name]["if"]
        assert "full-ci" in condition
        assert "github.event.pull_request.draft == false" in condition
        assert "github.event.action != 'labeled'" not in condition


def test_coderabbit_reviews_one_draft_checkpoint_then_pauses() -> None:
    config = _load_yaml(ROOT / ".coderabbit.yaml")
    auto_review = config["reviews"]["auto_review"]

    assert auto_review["enabled"] == "true"
    assert auto_review["drafts"] == "true"
    assert auto_review["auto_incremental_review"] == "true"
    assert auto_review["auto_pause_after_reviewed_commits"] == "1"
