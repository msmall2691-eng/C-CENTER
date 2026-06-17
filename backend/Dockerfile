# Command Center backend — for Railway (or any container host).
#
# Needs BOTH runtimes: Python for FastAPI, and Node.js because the Claude Agent
# SDK bundles the Claude Code runtime, which runs on Node. A plain Python image
# would start but every agent call would fail.

FROM python:3.11-slim

# Node.js 20 for the Agent SDK runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects $PORT; default to 8000 for local `docker run`.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
