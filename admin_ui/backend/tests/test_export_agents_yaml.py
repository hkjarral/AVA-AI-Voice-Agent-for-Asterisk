import yaml
from agents_store import AgentsStore
from export_agents_yaml import export_yaml

def test_roundtrip(tmp_path):
    store = AgentsStore(db_path=str(tmp_path / "agents.db"))
    store.create(display_name="Sales", provider="p", prompt="sys", greeting="hi",
                 extra_json='{"pipeline":"local_hybrid"}')
    out = export_yaml(store)
    doc = yaml.safe_load(out)
    assert doc["contexts"]["sales"]["prompt"] == "sys"
    assert doc["contexts"]["sales"]["pipeline"] == "local_hybrid"


def test_malformed_json_fields_skipped_not_crashed(tmp_path):
    """An agent with bad tools_json or extra_json must not abort the whole export."""
    store = AgentsStore(db_path=str(tmp_path / "agents.db"))
    store.create(display_name="Good", provider="p", prompt="good")
    store.create(display_name="Bad", provider="p", prompt="bad",
                 tools_json="not-json", extra_json="{broken")
    out = export_yaml(store)
    doc = yaml.safe_load(out)
    # Both agents appear in the output
    assert "good" in doc["contexts"]
    assert "bad" in doc["contexts"]
    # Malformed fields are simply absent (skipped), not raised
    assert "tools" not in doc["contexts"]["bad"]
