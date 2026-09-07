from pathlib import Path


PREFLIGHT = (Path(__file__).resolve().parents[1] / "preflight.sh").read_text(
    encoding="utf-8"
)


def test_not_required_transport_modules_are_not_reported_as_failures():
    """Modules for an unselected transport must not render as red checks."""
    assert 'mod_audiosocket_ok=true mod_audiosocket_detail="not required"' in PREFLIGHT
    assert 'mod_chan_websocket_ok=true mod_chan_websocket_detail="not required"' in PREFLIGHT
    # Selected-transport probes still start from false before checking.
    assert 'mod_audiosocket_ok=false mod_audiosocket_detail="Not loaded"' in PREFLIGHT
