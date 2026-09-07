from pathlib import Path
import subprocess

import pytest


PREFLIGHT = (Path(__file__).resolve().parents[1] / "preflight.sh").read_text(
    encoding="utf-8"
)


def test_not_required_transport_modules_are_not_reported_as_failures():
    """Modules for an unselected transport must not render as red checks."""
    assert 'mod_audiosocket_ok=true mod_audiosocket_detail="not required"' in PREFLIGHT
    assert 'mod_chan_websocket_ok=true mod_chan_websocket_detail="not required"' in PREFLIGHT
    # Selected-transport probes still start from false before checking.
    assert 'mod_audiosocket_ok=false mod_audiosocket_detail="Not loaded"' in PREFLIGHT


@pytest.mark.parametrize("base,local,expected", [
    ('websocket # media transport', None, 'websocket'),
    ('"websocket" # quoted', None, 'websocket'),
    ("'audiosocket' # quoted", None, 'audiosocket'),
    ('externalmedia', 'websocket # local override', 'websocket'),
    ('websocket # base', '# no override', 'websocket'),
    ('websocket', 'audiosocket # rollback', 'audiosocket'),
])
def test_transport_selection_ignores_inline_comments(tmp_path, base, local, expected):
    config = tmp_path / "config"
    config.mkdir()
    (config / "ai-agent.yaml").write_text(f"audio_transport: {base}\n")
    if local is not None:
        (config / "ai-agent.local.yaml").write_text(f"audio_transport: {local}\n")
    # Execute the actual extraction block, not a second implementation.
    block = PREFLIGHT.split('    local selected_transport="externalmedia"', 1)[1]
    block = block.split('    local mod_audiosocket_ok=', 1)[0]
    script = 'SCRIPT_DIR="$1"\nselect_transport() {\nlocal selected_transport="externalmedia"\n' + block
    script += '\nprintf "%s" "$selected_transport"\n}\nselect_transport\n'
    result = subprocess.run(["bash", "-c", script, "test", str(tmp_path)], capture_output=True, text=True, check=True)
    assert result.stdout == expected
