from pathlib import Path


def test_vicidial_setup_docs_include_required_h_extension():
    text = Path("docs/Vicidial-Setup.md").read_text()

    assert "call_log--HVcauses--PRI-----NODEBUG-----${HANGUPCAUSE}" in text
    assert "every context on a ViciDial server must include the correct ViciDial `h` extension" in text
    assert "Remote AAVA Asterisk Server" in text
    assert "Same-Box ViciDial Asterisk" in text
