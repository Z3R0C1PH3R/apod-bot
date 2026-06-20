#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${YOUTUBE_TOKEN_B64:-}" ]]; then
  echo "$YOUTUBE_TOKEN_B64" | base64 -d > token_youtube_v3.pickle
fi

exec python3 main.py "$@"
