#!/bin/bash
set -e

OLLAMA_URL="${OLLAMA_HOST:-http://host.docker.internal:11434}"

echo "Waiting for Ollama at $OLLAMA_URL ..."
until curl -sf "${OLLAMA_URL}/api/tags" > /dev/null; do
    sleep 2
done
echo "Ollama is up."

MODEL="${OLLAMA_MODEL:-hf.co/bartowski/Hermes-3-Llama-3.1-8B-GGUF:Q2_K}"

echo "Pulling model: $MODEL ..."
curl -sf -X POST "${OLLAMA_URL}/api/pull" \
     -H "Content-Type: application/json" \
     -d "{\"name\": \"$MODEL\"}" \
     --no-buffer | tail -1
echo "Model ready."

exec python orchestrator.py
