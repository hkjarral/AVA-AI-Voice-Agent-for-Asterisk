from pathlib import Path


PREFLIGHT = (Path(__file__).resolve().parents[1] / "preflight.sh").read_text(
    encoding="utf-8"
)


def test_gpu_layers_require_verified_container_passthrough():
    assert 'local passthrough_verified="${2:-false}"' in PREFLIGHT
    assert (
        '[ "$APPLY_FIXES" = true ] && [ "$passthrough_verified" = true ]'
        in PREFLIGHT
    )
    assert 'update_env_gpu "true" "false"' in PREFLIGHT
    assert 'update_env_gpu "true" "true"' in PREFLIGHT


def test_fresh_media_path_checks_existing_ancestor():
    assert 'local media_traversal_probe="$MEDIA_DIR"' in PREFLIGHT
    assert 'sudo -u asterisk test -x "$media_traversal_probe"' in PREFLIGHT
