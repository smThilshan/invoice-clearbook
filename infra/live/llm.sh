#!/bin/bash
# At login: start LM Studio's server and load the live model (decision 46). One model serves the
# live copy and development; if it is already loaded, it is left as it is.
set -euo pipefail
source "$(dirname "$0")/common.sh"

MODEL="$(env_value LLM_MODEL)"
CONTEXT="$(env_value LLM_CONTEXT_TOKENS)"
MODEL="${MODEL:-qwen/qwen3-vl-4b}"
CONTEXT="${CONTEXT:-16384}"

"$LMS" server start
if "$LMS" ps | grep -qF "$MODEL"; then
    echo "$(date '+%F %T') $MODEL already loaded"
else
    "$LMS" load "$MODEL" --context-length "$CONTEXT" --yes
    echo "$(date '+%F %T') $MODEL loaded"
fi
