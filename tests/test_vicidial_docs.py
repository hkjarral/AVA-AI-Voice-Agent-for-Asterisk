from pathlib import Path


def test_vicidial_setup_docs_include_required_h_extension():
    text = Path("docs/Vicidial-Setup.md").read_text()

    assert "exten => h,1,AGI(agi://127.0.0.1:4577/call_log)" in text
    assert "every context on a ViciDial server must include the `h` extension" in text
