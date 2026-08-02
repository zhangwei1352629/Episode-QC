#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UV_BIN="${UV_BIN:-${HOME}/.local/bin/uv}"

export UV_CACHE_DIR="${PROJECT_ROOT}/.uv-cache"
cd "${PROJECT_ROOT}"
exec "${UV_BIN}" run episode-qc web "$@"
