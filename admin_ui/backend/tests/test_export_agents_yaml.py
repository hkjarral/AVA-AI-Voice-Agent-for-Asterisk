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
