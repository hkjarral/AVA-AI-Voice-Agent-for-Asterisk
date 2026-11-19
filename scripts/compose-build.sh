#!/usr/bin/env bash
# The CLI embeds this same runner; updater images package it standalone.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$root/cli/internal/buildpolicy/compose-build.sh" "$@"
