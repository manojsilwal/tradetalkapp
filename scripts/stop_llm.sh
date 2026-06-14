#!/usr/bin/env bash
# stop_llm.sh — Stop LM Studio and Cloudflare tunnel before putting laptop to sleep.

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "Stopping Cloudflare Tunnel..."
pkill cloudflared || echo "No running cloudflared daemon found."

echo "Stopping LM Studio Server..."
/Users/manojsilwal/.lmstudio/bin/lms unload google/gemma-4-e4b || true
/Users/manojsilwal/.lmstudio/bin/lms server stop || true

echo "Stopping LM Studio App..."
osascript -e 'quit app "LM Studio"' 2>/dev/null || pkill -f "LM Studio" || echo "No running LM Studio app found."

echo "LLM services stopped successfully. You can now close your laptop."
