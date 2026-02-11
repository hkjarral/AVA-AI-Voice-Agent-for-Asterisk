package configmerge

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"

	"gopkg.in/yaml.v3"
)

// ReadYAMLFile reads a YAML mapping file into map[string]any.
func ReadYAMLFile(path string) (map[string]any, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return ParseYAML(b)
}

// ParseYAML parses YAML bytes into a map[string]any. Non-mapping documents return an error.
func ParseYAML(b []byte) (map[string]any, error) {
	var raw any
	if err := yaml.Unmarshal(b, &raw); err != nil {
		return nil, err
	}
	norm := normalizeYAMLValue(raw)
	m, ok := norm.(map[string]any)
	if !ok {
		return nil, errors.New("YAML top-level must be a mapping")
	}
	return m, nil
}

// DeepMerge merges override on top of base.
// If override contains a key with a nil value, that key is deleted from the result.
func DeepMerge(base map[string]any, override map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range base {
		out[k] = v
	}
	for k, ov := range override {
		if ov == nil {
			delete(out, k)
			continue
		}
		if bm, ok1 := out[k].(map[string]any); ok1 {
			if om, ok2 := ov.(map[string]any); ok2 {
				out[k] = DeepMerge(bm, om)
				continue
			}
		}
		out[k] = ov
	}
	return out
}

// ComputeOverrideNoDeletes computes a minimal override mapping that makes base match desired,
// without encoding deletions (i.e. it never emits nil tombstones for keys missing in desired).
// This is safer for updates: new upstream keys remain inherited from base.
func ComputeOverrideNoDeletes(base map[string]any, desired map[string]any) map[string]any {
	if base == nil || desired == nil {
		return map[string]any{}
	}
	override := map[string]any{}
	for k, dv := range desired {
		bv, ok := base[k]
		if !ok {
			override[k] = dv
			continue
		}
		bm, ok1 := bv.(map[string]any)
		dm, ok2 := dv.(map[string]any)
		if ok1 && ok2 {
			child := ComputeOverrideNoDeletes(bm, dm)
			if len(child) > 0 {
				override[k] = child
			}
			continue
		}
		if !reflect.DeepEqual(bv, dv) {
			override[k] = dv
		}
	}
	return override
}

// WriteYAMLFileAtomic writes data to path atomically (temp file + rename). If the file already
// exists, we preserve its permissions; otherwise we default to 0644.
func WriteYAMLFileAtomic(path string, data map[string]any) error {
	dir := filepath.Dir(path)
	if dir != "." && dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
	}
	mode := os.FileMode(0o644)
	if st, err := os.Stat(path); err == nil {
		mode = st.Mode()
	}
	b, err := yaml.Marshal(data)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, filepath.Base(path)+".tmp.*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() {
		_ = os.Remove(tmpName)
	}()
	if _, err := tmp.Write(b); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmpName, mode); err != nil {
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		return fmt.Errorf("rename temp file: %w", err)
	}
	return nil
}

func normalizeYAMLValue(v any) any {
	switch t := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, vv := range t {
			out[k] = normalizeYAMLValue(vv)
		}
		return out
	case map[any]any:
		out := map[string]any{}
		for k, vv := range t {
			out[fmt.Sprint(k)] = normalizeYAMLValue(vv)
		}
		return out
	case []any:
		out := make([]any, 0, len(t))
		for _, vv := range t {
			out = append(out, normalizeYAMLValue(vv))
		}
		return out
	default:
		return v
	}
}

