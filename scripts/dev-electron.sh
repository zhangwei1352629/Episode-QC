#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_NODE="${PROJECT_ROOT}/.node/node-v22.23.1-linux-x64/bin"

if [[ -d "${LOCAL_NODE}" ]]; then
  export PATH="${LOCAL_NODE}:${PATH}"
fi

export UV_CACHE_DIR="${PROJECT_ROOT}/.uv-cache"
exec npm run dev
