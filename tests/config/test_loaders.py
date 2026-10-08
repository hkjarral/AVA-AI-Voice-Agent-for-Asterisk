"""
Unit tests for config.loaders module.

Tests cover:
- Path resolution (relative vs absolute)
- YAML loading with environment variable expansion
- Error handling (missing files, invalid YAML)
"""

import os
import pytest
import tempfile
import yaml
from pathlib import Path

from src.config.loaders import resolve_config_path, load_yaml_with_env_expansion, deep_merge_dicts


class TestResolveConfigPath:
    """Tests for resolve_config_path function."""
    
    def test_absolute_path_unchanged(self):
        """Absolute paths should be returned unchanged."""
        abs_path = "/etc/config/test.yaml"
        result = resolve_config_path(abs_path)
        assert result == abs_path
    
    def test_relative_path_resolved(self):
        """Relative paths should be resolved to an absolute path."""
        rel_path = "config/ai-agent.yaml"
        result = resolve_config_path(rel_path)
        
        # Should be absolute
        assert os.path.isabs(result)
        
        # Should end with the relative path
        assert result.endswith(rel_path)
    
    def test_current_dir_relative_path(self):
        """Paths starting with ./ should be resolved."""
        rel_path = "./config/test.yaml"
        result = resolve_config_path(rel_path)
        assert os.path.isabs(result)


class TestLoadYamlWithEnvExpansion:
    """Tests for load_yaml_with_env_expansion function."""
    
    def test_load_simple_yaml(self, tmp_path):
        """Should load simple YAML without env vars."""
        config_file = tmp_path / "test.yaml"
        config_content = """
app_name: test-app
version: 1.0
settings:
  enabled: true
  count: 42
"""
        config_file.write_text(config_content)
        
        result = load_yaml_with_env_expansion(str(config_file))
        
        assert result['app_name'] == 'test-app'
        assert result['version'] == 1.0
        assert result['settings']['enabled'] is True
        assert result['settings']['count'] == 42
    
    def test_env_var_expansion_dollar_brace(self, tmp_path, monkeypatch):
        """Should expand ${VAR} style environment variables."""
        monkeypatch.setenv("TEST_HOST", "127.0.0.1")
        monkeypatch.setenv("TEST_PORT", "8080")
        
        config_file = tmp_path / "test.yaml"
        config_content = """
server:
  host: ${TEST_HOST}
  port: ${TEST_PORT}
"""
        config_file.write_text(config_content)
        
        result = load_yaml_with_env_expansion(str(config_file))
        
        assert result['server']['host'] == '127.0.0.1'
        # YAML parser converts numeric strings to int
        assert result['server']['port'] == 8080
    
    def test_env_var_expansion_dollar_only(self, tmp_path, monkeypatch):
        """Should expand $VAR style environment variables."""
        monkeypatch.setenv("TEST_NAME", "test-value")
        
        config_file = tmp_path / "test.yaml"
        config_content = """
name: $TEST_NAME
"""
        config_file.write_text(config_content)
        
        result = load_yaml_with_env_expansion(str(config_file))
        
        assert result['name'] == 'test-value'
    
    def test_missing_env_var_left_unchanged(self, tmp_path):
        """Missing env vars should be left unchanged (not expanded)."""
        config_file = tmp_path / "test.yaml"
        config_content = """
missing: ${NONEXISTENT_VAR}
"""
        config_file.write_text(config_content)
        
        result = load_yaml_with_env_expansion(str(config_file))
        
        # os.expandvars leaves undefined vars unchanged (this is correct behavior)
        assert result['missing'] == '${NONEXISTENT_VAR}'

    def test_mcp_header_template_remains_unexpanded_until_client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AVA_MCP_SECRET", "private-sentinel")
        monkeypatch.setenv("AVA_MCP_HOST", "mcp.example")
        config_file = tmp_path / "test.yaml"
        config_file.write_text(
            'mcp:\n  servers:\n    remote:\n      url: "https://${AVA_MCP_HOST}/mcp"\n'
            '      headers:\n        Authorization: "Bearer ${AVA_MCP_SECRET}"\n'
        )
        result = load_yaml_with_env_expansion(str(config_file))
        assert result["mcp"]["servers"]["remote"]["url"] == "https://mcp.example/mcp"
        assert result["mcp"]["servers"]["remote"]["headers"] == {
            "Authorization": "Bearer ${AVA_MCP_SECRET}"
        }
    
    def test_mixed_env_and_literal(self, tmp_path, monkeypatch):
        """Should handle mix of env vars and literal values."""
        monkeypatch.setenv("DB_HOST", "localhost")
        
        config_file = tmp_path / "test.yaml"
        config_content = """
database:
  host: ${DB_HOST}
  port: 5432
  name: mydb
"""
        config_file.write_text(config_content)
        
        result = load_yaml_with_env_expansion(str(config_file))
        
        assert result['database']['host'] == 'localhost'
        assert result['database']['port'] == 5432
        assert result['database']['name'] == 'mydb'
    
    def test_file_not_found_raises_error(self):
        """Should raise FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_yaml_with_env_expansion("/nonexistent/path/config.yaml")
        
        assert "not found" in str(exc_info.value).lower()
    
    def test_invalid_yaml_raises_error(self, tmp_path):
        """Should raise YAMLError for invalid YAML syntax."""
        config_file = tmp_path / "invalid.yaml"
        # Invalid YAML: mismatched indentation
        config_content = """
key1: value1
  key2: value2
    key3: value3
"""
        config_file.write_text(config_content)
        
        with pytest.raises(yaml.YAMLError) as exc_info:
            load_yaml_with_env_expansion(str(config_file))
        
        assert "parsing" in str(exc_info.value).lower()
    
    def test_empty_file_returns_empty_dict(self, tmp_path):
        """Empty YAML file should return empty dict."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")
        
        result = load_yaml_with_env_expansion(str(config_file))
        
        assert result == {}
    
    def test_comments_ignored(self, tmp_path):
        """YAML comments should be ignored."""
        config_file = tmp_path / "test.yaml"
        config_content = """
# This is a comment
app_name: test  # inline comment
# Another comment
version: 1.0
"""
        config_file.write_text(config_content)
        
        result = load_yaml_with_env_expansion(str(config_file))
        
        assert result['app_name'] == 'test'
        assert result['version'] == 1.0
        assert len(result) == 2  # Only 2 keys, no comments


class TestDeepMergeDicts:
    def test_merge_preserves_base_keys(self):
        base = {"a": 1, "nested": {"x": 1, "y": 2}}
        override = {"nested": {"x": 9}}
        merged = deep_merge_dicts(base, override)
        assert merged["a"] == 1
        assert merged["nested"]["x"] == 9
        assert merged["nested"]["y"] == 2

    def test_merge_deletes_keys_with_none_tombstone(self):
        base = {"tools": {"transfer": {"destinations": {"support_queue": {"type": "queue", "target": "301"}}}}}
        override = {"tools": {"transfer": {"destinations": {"support_queue": None}}}}
        merged = deep_merge_dicts(base, override)
        assert "support_queue" not in merged["tools"]["transfer"]["destinations"]
