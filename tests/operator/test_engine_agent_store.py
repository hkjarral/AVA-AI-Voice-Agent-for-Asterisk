import json, sqlite3, pytest
from src.core.agent_store import EngineAgentStore, AgentStoreReadError

SCHEMA_MIN = """CREATE TABLE agents (id TEXT PRIMARY KEY, slug TEXT UNIQUE, display_name TEXT,
extension TEXT, role_label TEXT, provider TEXT, voice TEXT, greeting TEXT, prompt TEXT,
tools_json TEXT, mcp_json TEXT, audio_profile TEXT, extra_json TEXT,
is_operator_managed INTEGER, is_active INTEGER, is_default INTEGER, source_file TEXT,
created_at TEXT, updated_at TEXT, notes TEXT);"""

@pytest.fixture
def db(tmp_path):
    p = tmp_path / "agents.db"
    c = sqlite3.connect(p); c.executescript(SCHEMA_MIN)
    c.execute("""INSERT INTO agents (id,slug,display_name,provider,prompt,greeting,audio_profile,
                 tools_json,extra_json,is_active,is_default,is_operator_managed,created_at,updated_at)
                 VALUES ('1','sales','Sales','openai_realtime','sys prompt','hi','ulaw8k',
                 '["transfer"]','{"pipeline":"local_hybrid","pre_call_tools":["enrich"]}',1,1,1,'t','t')""")
    c.commit(); c.close()
    return str(p)

def test_resolve_builds_context_config(db):
    s = EngineAgentStore(db_path=db)
    cc = s.resolve("sales")
    assert cc.prompt == "sys prompt" and cc.provider == "openai_realtime"
    assert cc.profile == "ulaw8k" and cc.tools == ["transfer"]
    assert cc.pipeline == "local_hybrid" and cc.pre_call_tools == ["enrich"]

def test_resolve_unknown_returns_none(db):
    assert EngineAgentStore(db_path=db).resolve("nope") is None

def test_inactive_agent_not_resolved(db):
    c = sqlite3.connect(db); c.execute("UPDATE agents SET is_active=0"); c.commit()
    assert EngineAgentStore(db_path=db).resolve("sales") is None

def test_default_slug(db):
    assert EngineAgentStore(db_path=db).default_slug() == "sales"

def test_db_absent_means_unavailable(tmp_path):
    s = EngineAgentStore(db_path=str(tmp_path / "missing.db"))
    assert not s.available()

def test_corrupt_json_raises_read_error(db):
    # HIGH-9: corrupt extra_json (manual edit / bad backup) is a DB read error, not a
    # clean not-found. resolve() raises AgentStoreReadError so the orchestrator falls
    # back to YAML — without resurrecting deleted agents. The call is not crashed:
    # the exception is controlled and handled one level up.
    c = sqlite3.connect(db)
    c.execute("UPDATE agents SET extra_json='{not valid json' WHERE slug='sales'")
    c.commit(); c.close()
    with pytest.raises(AgentStoreReadError):
        EngineAgentStore(db_path=db).resolve("sales")

def test_corrupt_tools_json_raises_read_error(db):
    c = sqlite3.connect(db)
    c.execute("UPDATE agents SET tools_json='[oops' WHERE slug='sales'")
    c.commit(); c.close()
    with pytest.raises(AgentStoreReadError):
        EngineAgentStore(db_path=db).resolve("sales")

def test_no_arg_honors_agents_db_path_env(db, monkeypatch):
    # MED-C2: EngineAgentStore() (constructed with no arg by the orchestrator)
    # must pick up AGENTS_DB_PATH so a relocated DB is read at runtime.
    monkeypatch.setenv("AGENTS_DB_PATH", db)
    s = EngineAgentStore()
    assert s.db_path == db
    assert s.resolve("sales").provider == "openai_realtime"

def test_no_arg_default_unchanged_when_env_unset(monkeypatch):
    # MED-C2: with the env unset, the historical default is preserved exactly
    # (no behavior change for existing installs).
    monkeypatch.delenv("AGENTS_DB_PATH", raising=False)
    assert EngineAgentStore().db_path == "/app/data/operator/agents.db"
