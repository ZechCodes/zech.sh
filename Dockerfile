FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates openssl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /uvx /bin/

# Copy dependency files first for caching
COPY pyproject.toml uv.lock ./
COPY scripts/verify_ca_trust.py scripts/impacted-intermediates.json scripts/

# Install dependencies
RUN uv sync --frozen --no-dev && \
    .venv/bin/python scripts/verify_ca_trust.py

# Copy application code
COPY . .

# Expose port
EXPOSE 8080

# Run the application
CMD [".venv/bin/skrift", "serve", "--host", "0.0.0.0", "--port", "8080"]
