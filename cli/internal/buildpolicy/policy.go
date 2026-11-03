// Package buildpolicy embeds the build runner also used by installation and UI.
package buildpolicy

import _ "embed"

// Script remains available when update/rollback checks out an older source tree.
//
//go:embed compose-build.sh
var Script string
