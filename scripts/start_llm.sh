#!/usr/bin/env bash
# start_llm.sh — Start Ollama, start Cloudflare quick tunnel, and auto-update environment configuration.

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

cd "$(dirname "$0")/.."

echo "Starting Ollama..."
open -a Ollama

echo "Waiting for Ollama to start..."
until curl -s http://localhost:11434 > /dev/null; do
  sleep 1
done
echo "Ollama is ready!"

echo "Starting Cloudflare Quick Tunnel..."
mkdir -p ~/.cloudflared
cloudflared tunnel --url http://localhost:11434 > ~/.cloudflared/quick_tunnel.log 2>&1 &
sleep 5

# Extract the new trycloudflare URL
TUNNEL_URL=$(grep -oE "https://[a-zA-Z0-9.-]+\.trycloudflare\.com" ~/.cloudflared/quick_tunnel.log | head -n 1)

if [ -n "$TUNNEL_URL" ]; then
  echo "Tunnel started successfully!"
  echo "New URL: $TUNNEL_URL"
  
  # Auto-update environment files with the new URL
  if [ -f "backend/.env.local" ]; then
    sed -i '' -E 's|NVIDIA_LLM_BASE_URL=https://.*\.trycloudflare\.com/v1|NVIDIA_LLM_BASE_URL='"$TUNNEL_URL"'/v1|g' backend/.env.local
    echo "Updated backend/.env.local with the new tunnel URL."
  fi
  if [ -f ".env.gcp" ]; then
    sed -i '' -E 's|NVIDIA_LLM_BASE_URL=https://.*\.trycloudflare\.com/v1|NVIDIA_LLM_BASE_URL='"$TUNNEL_URL"'/v1|g' .env.gcp
    echo "Updated .env.gcp with the new tunnel URL."
  else
    echo "Warning: .env.gcp not found. Please update it manually."
  fi
else
  echo "Error: Could not retrieve Cloudflare Tunnel URL. Check ~/.cloudflared/quick_tunnel.log"
fi
