# docker/Dockerfile
# Build context is the repo root (.), so all paths are relative to there.
FROM python:3.11-slim

WORKDIR /app

# 安裝系統依賴與 Node.js (供 LLM CLI 工具使用，如 Claude CLI)
RUN apt-get update && apt-get install -y \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 全域安裝 Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# 安裝 Python 依賴 — 使用 repo root 的釘版 requirements.txt
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
