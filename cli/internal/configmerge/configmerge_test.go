package configmerge

import (
	"reflect"
	"testing"
)

func TestDeepMergeNilDeletesKey(t *testing.T) {
	base := map[string]any{"a": 1, "b": map[string]any{"c": 2, "d": 3}}
	ov := map[string]any{"a": nil, "b": map[string]any{"c": nil, "e": 4}}
	got := DeepMerge(base, ov)
	want := map[string]any{"b": map[string]any{"d": 3, "e": 4}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v want %#v", got, want)
	}
}

func TestComputeOverrideNoDeletesKeepsNewUpstreamKeys(t *testing.T) {
	baseAfter := map[string]any{
		"providers": map[string]any{
			"google_live": map[string]any{
				"ws_keepalive_enabled": false,
				"foo":                 1,
			},
		},
	}
	desiredEffective := map[string]any{
		"providers": map[string]any{
			"google_live": map[string]any{
				"foo": 2,
			},
		},
	}
	ov := ComputeOverrideNoDeletes(baseAfter, desiredEffective)
	merged := DeepMerge(baseAfter, ov)

	wantMerged := map[string]any{
		"providers": map[string]any{
			"google_live": map[string]any{
				"ws_keepalive_enabled": false,
				"foo":                 2,
			},
		},
	}
	if !reflect.DeepEqual(merged, wantMerged) {
		t.Fatalf("merged got %#v want %#v (override=%#v)", merged, wantMerged, ov)
	}
}

