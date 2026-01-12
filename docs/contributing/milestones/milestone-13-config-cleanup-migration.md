# Milestone 13 — Config Cleanup & Migration

Note (CLI v5.0): The public CLI surface is now `agent setup`, `agent check`, `agent rca`, and `agent version`. Legacy command names used below (`agent doctor`, etc.) remain available as hidden aliases for compatibility.

## Objective

Simplify configuration and reduce operator footguns by establishing clear separation between production and diagnostic settings. Move diagnostic knobs to environment variables, deprecate legacy settings, and provide automated migration path.

## Success Criteria

- 21% cleaner config (374 → 294 lines)
- 49% smaller file (16K → 8.1K)
- Diagnostic settings only in `.env` (not YAML)
- Deprecated knobs removed from YAML schema with warnings
- Zero-change upgrade path (backward compatible)
- Migration script with dry-run mode

## Dependencies

- Milestones 8-12 complete (all v4.0 features operational)
- Existing deployments use various config versions
- Need to preserve backward compatibility during transition

## Work Breakdown

### 13.1 Diagnostic Settings Migration

**Objective**: Move diagnostic-only settings from YAML to environment variables.

**Settings Moved to `.env`**:
1. `DIAG_EGRESS_SWAP_MODE` - Byte swap diagnostics
2. `DIAG_ENABLE_TAPS` - Audio tap capture
3. `DIAG_TAP_PRE_SECS` - Pre-event capture duration
4. `DIAG_TAP_POST_SECS` - Post-event capture duration
5. `DIAG_TAP_OUTPUT_DIR` - Tap output directory
6. `STREAMING_LOG_LEVEL` - Streaming component log level
7. `LOG_LEVEL` - Global log level
8. `LOG_FORMAT` - Log format (json/text)
9. `LOG_TO_FILE` - File logging enable

**Rationale**:
- Diagnostic settings don't affect call behavior (operator tools only)
- Environment variables better for secrets and deployment-specific config
- YAML remains pure application configuration
- Follows 12-factor app principles

### 13.2 Config Version 4 Schema

**New Schema Features**:
- `config_version: 4` field required
- Validation on startup with helpful error messages
- Forward compatibility markers
- Deprecation warnings for old-style configs

**Validation**:
```python
if config.get('config_version', 1) < 4:
    logger.warning(
        "Configuration version < 4 detected. "
        "Run: scripts/migrate_config_v4.py --apply"
    )
```

### 13.3 Migration Script

**Tool**: `scripts/migrate_config_v4.py`

**Modes**:
- `--dry-run`: Show what would change (default)
- `--apply`: Actually migrate the file
- `--backup`: Create `.bak` before changes

**Features**:
- Detects current config version
- Moves diagnostic settings to `.env`
- Updates schema to version 4
- Preserves comments where possible
- Validates output YAML
- Creates backup automatically

**Usage**:
```bash
# Preview changes
$ ./scripts/migrate_config_v4.py --dry-run

MIGRATION PREVIEW (config/ai-agent.yaml)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Changes to YAML:
  - Remove: diagnostics.egress_swap_mode
  - Remove: diagnostics.enable_taps
  - Remove: diagnostics.tap_pre_secs
  - Remove: diagnostics.tap_post_secs
  - Remove: diagnostics.tap_output_dir
  - Remove: streaming.log_level
  - Add: config_version: 4

Settings moved to .env:
  + DIAG_EGRESS_SWAP_MODE=none
  + DIAG_ENABLE_TAPS=false
  + DIAG_TAP_PRE_SECS=3
  + DIAG_TAP_POST_SECS=5
  + DIAG_TAP_OUTPUT_DIR=diagnostics/taps
  + STREAMING_LOG_LEVEL=INFO

File size: 16K → 8.1K (49% smaller)
Lines: 374 → 294 (21% fewer)

# Apply migration
$ ./scripts/migrate_config_v4.py --apply
✅ Backup created: config/ai-agent.yaml.bak
✅ YAML updated: config/ai-agent.yaml
✅ .env updated with diagnostic settings
✅ Migration complete!
```

### 13.4 Deprecated Settings Handling

**9 Settings Deprecated**:
1. `diagnostics.egress_swap_mode` → `DIAG_EGRESS_SWAP_MODE`
2. `diagnostics.enable_taps` → `DIAG_ENABLE_TAPS`
3. `diagnostics.tap_pre_secs` → `DIAG_TAP_PRE_SECS`
4. `diagnostics.tap_post_secs` → `DIAG_TAP_POST_SECS`
5. `diagnostics.tap_output_dir` → `DIAG_TAP_OUTPUT_DIR`
6. `streaming.log_level` → `STREAMING_LOG_LEVEL`
7. `log_level` (YAML) → `LOG_LEVEL` (env)
8. `log_format` (YAML) → `LOG_FORMAT` (env)
9. `log_to_file` (YAML) → `LOG_TO_FILE` (env)

**Deprecation Strategy**:
- Phase 1 (v4.0): Both YAML and env work, warnings logged
- Phase 2 (v4.1): YAML settings removed from schema, env required
- Phase 3 (v5.0): Old config versions rejected

**Warning Example**:
```
[WARN] Deprecated setting 'diagnostics.enable_taps' in YAML.
       Use environment variable DIAG_ENABLE_TAPS instead.
       Run: scripts/migrate_config_v4.py --apply
```

### 13.5 Safer Defaults

**Production-Safe Defaults**:
- Diagnostics OFF by default (opt-in only)
- Audio taps disabled (privacy/performance)
- Egress swap disabled (not needed after Milestone 8)
- Log level INFO (not DEBUG)

**Environment Variable Defaults**:
```bash
# .env.example
DIAG_EGRESS_SWAP_MODE=none
DIAG_ENABLE_TAPS=false
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_TO_FILE=true
```

## Deliverables

- Migration script: `scripts/migrate_config_v4.py`
- Updated config schema with version 4 validation
- 8 diagnostic settings moved to `.env`
- Deprecation warnings for old-style configs
- Documentation: migration guide
- `.env.example` with all diagnostic variables

## Verification Checklist

### Pre-Deployment
- [ ] Migration script tested on multiple config versions
- [ ] Schema validation enforces config_version field
- [ ] Environment variables properly loaded
- [ ] Deprecation warnings clear and actionable
- [ ] Backup functionality working

### Migration Test: v3 → v4
- [ ] Start with v3 config (no config_version field)
- [ ] Run migration script in dry-run mode
- [ ] Preview shows correct changes
- [ ] Apply migration
- [ ] Backup created automatically
- [ ] YAML smaller and cleaner
- [ ] .env contains diagnostic settings
- [ ] Container restart succeeds
- [ ] Test call works perfectly

### Backward Compatibility Test
- [ ] Old config with diagnostic settings in YAML
- [ ] Container starts successfully
- [ ] Warnings logged about deprecated settings
- [ ] Call functionality unchanged
- [ ] `agent doctor` suggests migration

### Fresh Install Test
- [ ] New operator runs `agent init`
- [ ] Generated config is version 4
- [ ] No diagnostic settings in YAML
- [ ] .env has correct defaults
- [ ] No deprecation warnings
- [ ] Clean, modern configuration

## Impact Metrics

### Config Complexity Reduction

**Before (v3)**:
```yaml
# config/ai-agent.yaml (16K, 374 lines)
app:
  name: "AI Voice Agent"
  log_level: DEBUG        # Mixed concerns
  log_format: json
  
diagnostics:
  egress_swap_mode: auto  # Diagnostic in YAML
  enable_taps: true
  tap_pre_secs: 3
  tap_post_secs: 5
  tap_output_dir: diagnostics/taps
  
streaming:
  log_level: DEBUG        # Diagnostic in YAML
  min_start_ms: 800
  ...
```

**After (v4)**:
```yaml
# config/ai-agent.yaml (8.1K, 294 lines)
config_version: 4

app:
  name: "AI Voice Agent"
  # Logging in .env
  
# No diagnostics section
  
streaming:
  # No log_level here
  min_start_ms: 800
  ...
```

```bash
# .env (diagnostic settings)
LOG_LEVEL=INFO
LOG_FORMAT=json
DIAG_ENABLE_TAPS=false
DIAG_EGRESS_SWAP_MODE=none
STREAMING_LOG_LEVEL=INFO
```

**Results**:
- File size: 16K → 8.1K (49% smaller)
- Lines: 374 → 294 (21% fewer)
- Clearer separation of concerns
- Easier to understand for new operators

## Handover Notes

- This migration establishes clean config patterns for future development.
- All new diagnostic features should use environment variables, not YAML.
- Config version field allows future migrations with automated detection.
- Operators can migrate at their convenience (backward compatible).
- Consider similar cleanup for other mixed-concern settings in future versions.

## Related Issues

- **Feature**: Config version system (implemented)
- **Feature**: Automated migration script (implemented)
- **Enhancement**: Diagnostic settings separation (implemented)
- **Enhancement**: Safer production defaults (implemented)

## Migration Timeline

### Immediate (v4.0 GA - October 2025)
- Migration script available
- Both old and new configs work
- Deprecation warnings logged
- Documentation updated

### Short-term (v4.1 - Q1 2026)
- Remove deprecated settings from schema
- Environment variables required
- Migration script mandatory for v3 configs

### Long-term (v5.0 - Q2 2026)
- Reject configs with version < 4
- Clean schema with no legacy support
- All deployments on v4+ configs

## Configuration Best Practices

### Production Settings (YAML)
- Application behavior (pipelines, providers)
- Audio profiles
- Contexts and prompts
- Transport settings
- Streaming parameters

### Diagnostic Settings (.env)
- Log levels and formats
- Audio tap capture
- Debug features
- Development tools
- Byte swap diagnostics

### Secrets (.env)
- API keys (OPENAI_API_KEY, DEEPGRAM_API_KEY)
- Passwords (ASTERISK_ARI_PASSWORD)
- Credentials (ASTERISK_ARI_USERNAME)
- Sensitive URLs

### Never in YAML
- API keys or passwords
- Diagnostic-only settings
- Deployment-specific values
- Secrets of any kind

---

**Status**: ✅ Completed October 26, 2025  
**Impact**: 49% smaller configs, clearer separation of concerns
