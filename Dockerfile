# ============================================================
# 全屋智能 Multi-Agent System - Docker Image
# Multi-stage: Python Agent Engine + Node.js Gateway
# ============================================================

FROM python:3.13-slim AS agent-engine

WORKDIR /app/agent_engine
COPY agent_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent_engine/ .

# ============================================================

FROM node:20-slim AS node-gateway

WORKDIR /app
RUN npm init -y && npm install ws

COPY sync-server.js .
COPY gai.html .
COPY mobile-control.html* ./

# ============================================================

FROM python:3.13-slim

# Install Node.js
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python Agent Engine
COPY --from=agent-engine /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY agent_engine/ /app/agent_engine/

# Copy Node.js Gateway
COPY sync-server.js /app/
COPY gai.html /app/
COPY mobile-control.html* /app/
RUN cd /app && npm init -y && npm install ws

# Copy startup script
COPY docker-start.sh /app/
RUN chmod +x /app/docker-start.sh

EXPOSE 8080 8081

ENV AGENT_ENGINE_HOST=0.0.0.0
ENV AGENT_ENGINE_PORT=8081
ENV NODE_SERVER_URL=http://localhost:8081
ENV LLM_PROVIDER=mock

CMD ["/app/docker-start.sh"]
