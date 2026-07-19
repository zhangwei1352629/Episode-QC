#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_NODE="${PROJECT_ROOT}/.node/node-v22.23.1-linux-x64/bin"
UV_BIN="${UV_BIN:-${HOME}/.local/bin/uv}"

if [[ -d "${LOCAL_NODE}" ]]; then
  export PATH="${LOCAL_NODE}:${PATH}"
fi

export UV_CACHE_DIR="${PROJECT_ROOT}/.uv-cache"
export npm_config_cache="${PROJECT_ROOT}/.npm-cache"

"${UV_BIN}" run pytest -q
npm test
