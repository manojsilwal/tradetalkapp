#!/usr/bin/env bash
# stop_llm.sh — Stop Ollama and Cloudflare tunnel before putting laptop to sleep.

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "Stopping Cloudflare Tunnel..."
pkill cloudflared || echo "No running cloudflared daemon found."

echo "Stopping Ollama..."
osascript -e 'quit app "Ollama"' 2>/dev/null || pkill ollama || echo "No running Ollama app found."

echo "LLM services stopped successfully. You can now close your laptop."
