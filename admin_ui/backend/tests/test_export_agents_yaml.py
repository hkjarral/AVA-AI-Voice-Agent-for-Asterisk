import yaml
from agents_store import AgentsStore
from export_agents_yaml import export_yaml

def test_roundtrip(tmp_path):
    store = AgentsStore(db_path=str(tmp_path / "agents.db"))
    store.create(display_name="Sales", provider="p", prompt="sys", greeting="hi",
                 extra_json='{"pipeline":"local_hybrid"}',
                 tool_configs_json='{"transfer":{"destination_policy":"none","destination_keys":[]}}')
    out = export_yaml(store)
    doc = yaml.safe_load(out)
    assert doc["contexts"]["sales"]["prompt"] == "sys"
    assert doc["contexts"]["sales"]["pipeline"] == "local_hybrid"
    assert doc["contexts"]["sales"]["tool_configs"]["transfer"]["destination_policy"] == "none"


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


def test_canonical_tool_configs_win_over_stale_extra_payload(tmp_path):
    store = AgentsStore(db_path=str(tmp_path / "agents.db"))
    store.create(
        display_name="Scoped",
        provider="p",
        prompt="sys",
        tool_configs_json='{"voicemail":{"mailbox_policy":"selected","mailbox_key":"sales"}}',
        extra_json='{"tool_configs":{"voicemail":{"mailbox_policy":"none","mailbox_key":null}}}',
    )

    doc = yaml.safe_load(export_yaml(store))

    assert doc["contexts"]["scoped"]["tool_configs"] == {
        "voicemail": {"mailbox_policy": "selected", "mailbox_key": "sales"}
    }
