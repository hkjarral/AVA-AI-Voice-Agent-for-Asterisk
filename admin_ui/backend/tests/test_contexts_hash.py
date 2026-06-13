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
