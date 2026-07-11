from agents_migration import merged_effective_contexts, contexts_hash

def _write(p, s): p.write_text(s)

def test_inline_wins_over_external(tmp_path):
    _write(tmp_path / "ai-agent.yaml", "contexts:\n  demo:\n    provider: a\n    prompt: inline\n")
    ext = tmp_path / "contexts"; ext.mkdir()
    _write(ext / "demo.yaml", "name: demo\nprovider: b\nsystem_prompt: external\n")
    merged = merged_effective_contexts(str(tmp_path / "ai-agent.yaml"), str(ext))
    assert merged["demo"]["prompt"] == "inline"

def test_system_prompt_maps_to_prompt(tmp_path):
    _write(tmp_path / "ai-agent.yaml", "contexts: {}\n")
    ext = tmp_path / "contexts"; ext.mkdir()
    _write(ext / "x.yaml", "name: expert\nprovider: a\nsystem_prompt: hello\n")
    merged = merged_effective_contexts(str(tmp_path / "ai-agent.yaml"), str(ext))
    assert merged["expert"]["prompt"] == "hello"

def test_hash_ignores_non_context_sections(tmp_path):
    y1 = "providers:\n  a: {x: 1}\ncontexts:\n  d: {provider: a, prompt: p}\n"
    y2 = "providers:\n  a: {x: 2}\ncontexts:\n  d: {provider: a, prompt: p}\n"
    ext = tmp_path / "contexts"; ext.mkdir()
    f = tmp_path / "ai-agent.yaml"
    _write(f, y1); h1 = contexts_hash(merged_effective_contexts(str(f), str(ext)))
    _write(f, y2); h2 = contexts_hash(merged_effective_contexts(str(f), str(ext)))
    assert h1 == h2

def test_hash_changes_on_context_edit(tmp_path):
    ext = tmp_path / "contexts"; ext.mkdir()
    f = tmp_path / "ai-agent.yaml"
    _write(f, "contexts:\n  d: {provider: a, prompt: one}\n")
    h1 = contexts_hash(merged_effective_contexts(str(f), str(ext)))
    _write(f, "contexts:\n  d: {provider: a, prompt: two}\n")
    h2 = contexts_hash(merged_effective_contexts(str(f), str(ext)))
    assert h1 != h2

def test_yml_extension_also_merged(tmp_path):
    _write(tmp_path / "ai-agent.yaml", "contexts: {}\n")
    ext = tmp_path / "contexts"; ext.mkdir()
    _write(ext / "y.yml", "name: from_yml\nprovider: a\nsystem_prompt: hi\n")
    merged = merged_effective_contexts(str(tmp_path / "ai-agent.yaml"), str(ext))
    assert merged["from_yml"]["prompt"] == "hi"


def test_local_override_adds_context_and_deep_merges_existing(tmp_path):
    base = tmp_path / "ai-agent.yaml"
    _write(base, "contexts:\n  base:\n    provider: local\n    prompt: base prompt\n    no_input:\n      enabled: true\n      initial_timeout_sec: 30\n")
    _write(tmp_path / "ai-agent.local.yaml", "contexts:\n  base:\n    greeting: local greeting\n    no_input:\n      initial_timeout_sec: 10\n  demo_local_full:\n    provider: local\n    prompt: full local prompt\n")
    ext = tmp_path / "contexts"; ext.mkdir()

    merged = merged_effective_contexts(str(base), str(ext))

    assert merged["base"]["provider"] == "local"
    assert merged["base"]["prompt"] == "base prompt"
    assert merged["base"]["greeting"] == "local greeting"
    assert merged["base"]["no_input"] == {"enabled": True, "initial_timeout_sec": 10}
    assert merged["base"]["_source_file"] == "ai-agent.local.yaml"
    assert merged["demo_local_full"]["prompt"] == "full local prompt"


def test_hash_changes_when_local_context_changes(tmp_path):
    base = tmp_path / "ai-agent.yaml"
    local = tmp_path / "ai-agent.local.yaml"
    ext = tmp_path / "contexts"; ext.mkdir()
    _write(base, "contexts: {}\n")
    _write(local, "contexts:\n  local_only: {provider: local, prompt: one}\n")
    h1 = contexts_hash(merged_effective_contexts(str(base), str(ext)))
    _write(local, "contexts:\n  local_only: {provider: local, prompt: two}\n")
    h2 = contexts_hash(merged_effective_contexts(str(base), str(ext)))
    assert h1 != h2


def test_external_context_returns_after_local_override_deletes_inline_name(tmp_path):
    base = tmp_path / "ai-agent.yaml"
    local = tmp_path / "ai-agent.local.yaml"
    ext = tmp_path / "contexts"; ext.mkdir()
    _write(base, "contexts:\n  demo: {provider: openai_realtime, prompt: inline}\n")
    _write(local, "contexts:\n  demo: null\n")
    _write(ext / "demo.yaml", "name: demo\nprovider: local\nprompt: external\n")

    merged = merged_effective_contexts(str(base), str(ext))

    assert merged["demo"]["provider"] == "local"
    assert merged["demo"]["prompt"] == "external"
    assert merged["demo"]["_source_file"] == "contexts/demo.yaml"


def test_non_mapping_local_context_override_is_skipped_without_breaking_hash(tmp_path):
    base = tmp_path / "ai-agent.yaml"
    local = tmp_path / "ai-agent.local.yaml"
    ext = tmp_path / "contexts"; ext.mkdir()
    _write(base, "contexts:\n  demo: {provider: local, prompt: base}\n")
    _write(local, "contexts:\n  demo: invalid-scalar\n  broken: [not, a, mapping]\n")

    merged = merged_effective_contexts(str(base), str(ext))

    assert merged["demo"]["prompt"] == "base"
    assert "broken" not in merged
    assert len(contexts_hash(merged)) == 64
