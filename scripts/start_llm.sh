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

# Setup shutdown handler to run on launchd exit / logoff / restart / shutdown
cleanup() {
  echo "Termination signal received. Running stop_llm.sh..."
  bash "/Users/manojsilwal/tradetalk-bin/stop_llm.sh"
  exit 0
}
trap cleanup SIGINT SIGTERM

echo "Starting Cloudflare Quick Tunnel..."
mkdir -p ~/.cloudflared
cloudflared tunnel --url http://localhost:11434 --http-host-header localhost > ~/.cloudflared/quick_tunnel.log 2>&1 &
TUNNEL_PID=$!
sleep 5

# Extract the new trycloudflare URL
TUNNEL_URL=$(grep -oE "https://[a-zA-Z0-9.-]+\.trycloudflare\.com" ~/.cloudflared/quick_tunnel.log | head -n 1)

if [ -n "$TUNNEL_URL" ]; then
  echo "Tunnel started successfully!"
  echo "New URL: $TUNNEL_URL"
  
  # Auto-update environment files with the new URL (in ~/.cloudflared to bypass TCC permissions)
  TUNNEL_HOST=$(echo "$TUNNEL_URL" | sed -E 's|https://||')
  
  update_guardrails_allowed_hosts() {
    local file=$1
    if grep -q "GUARDRAILS_ALLOWED_HOSTS=" "$file"; then
      sed -i '' -E 's|GUARDRAILS_ALLOWED_HOSTS=.*|GUARDRAILS_ALLOWED_HOSTS='"$TUNNEL_HOST"'|g' "$file"
    else
      echo "GUARDRAILS_ALLOWED_HOSTS=$TUNNEL_HOST" >> "$file"
    fi
  }

  if [ -f "/Users/manojsilwal/.cloudflared/env.local" ]; then
    sed -i '' -E 's|NVIDIA_LLM_BASE_URL=https://.*\.trycloudflare\.com/v1|NVIDIA_LLM_BASE_URL='"$TUNNEL_URL"'/v1|g' /Users/manojsilwal/.cloudflared/env.local
    update_guardrails_allowed_hosts "/Users/manojsilwal/.cloudflared/env.local"
    echo "Updated env.local in ~/.cloudflared with the new tunnel URL and allowed hosts."
  fi
  if [ -f "/Users/manojsilwal/.cloudflared/env.gcp" ]; then
    sed -i '' -E 's|NVIDIA_LLM_BASE_URL=https://.*\.trycloudflare\.com/v1|NVIDIA_LLM_BASE_URL='"$TUNNEL_URL"'/v1|g' /Users/manojsilwal/.cloudflared/env.gcp
    update_guardrails_allowed_hosts "/Users/manojsilwal/.cloudflared/env.gcp"
    echo "Updated env.gcp in ~/.cloudflared with the new tunnel URL and allowed hosts."
  else
    echo "Warning: env.gcp in ~/.cloudflared not found. Please update it manually."
  fi
else
  echo "Error: Could not retrieve Cloudflare Tunnel URL. Check ~/.cloudflared/quick_tunnel.log"
fi

# Keep script running and wait on the cloudflared background process to handle SIGTERM on logout
if [ -n "$TUNNEL_PID" ]; then
  echo "Waiting on Cloudflare Tunnel (PID: $TUNNEL_PID) for termination..."
  wait $TUNNEL_PID
fi

