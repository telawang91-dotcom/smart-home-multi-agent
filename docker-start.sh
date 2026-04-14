#!/bin/bash
# ============================================================
# 全屋智能 Multi-Agent System - Docker Startup Script
# Starts both Python Agent Engine and Node.js Gateway
# ============================================================

echo "============================================================"
echo "  Smart Home Multi-Agent System"
echo "  Starting services..."
echo "============================================================"

# Start Python Agent Engine in background
echo "[1/2] Starting Python Agent Engine (port 8081)..."
cd /app/agent_engine
python main.py &
AGENT_PID=$!

# Wait for Agent Engine to be ready
sleep 3

# Start Node.js Gateway
echo "[2/2] Starting Node.js Gateway (port 8080)..."
cd /app
node sync-server.js &
NODE_PID=$!

echo "============================================================"
echo "  All services started!"
echo "  Frontend:     http://localhost:8080"
echo "  Agent Engine: http://localhost:8081/docs"
echo "============================================================"

# Wait for either process to exit
wait -n $AGENT_PID $NODE_PID
EXIT_CODE=$?

# If one exits, kill the other
kill $AGENT_PID $NODE_PID 2>/dev/null
exit $EXIT_CODE
