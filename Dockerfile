FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies before copying source so this layer is cached
COPY pyproject.toml uv.lock ./
RUN uv pip install --system -e ".[all,demo]"

# Copy source and data
COPY src/ ./src/
COPY data/runbooks/ ./data/runbooks/
COPY data/team-directory.md ./data/
COPY data/test-cases.yaml ./data/

# ChromaDB is populated at runtime via the ingestion step (see docker-compose.yml).
# The data/chroma_db directory is mounted as a volume so it persists across restarts.

EXPOSE 8000 8501
