# syntax=docker/dockerfile:1@sha256:2780b5c3bab67f1f76c781860de469442999ed1a0d7992a5efdf2cffc0e3d769

FROM python:3.14@sha256:89a288a9a9e9141b9f0c51744c358138da6369897792f1af3f5425e407d9529a AS builder

# Set working directory
WORKDIR /app

# Copy dependency files for layer caching
COPY constraints.txt requirements.txt ./

# Install pinned pip first (CVE-2026-1703 fix), then dependencies
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r constraints.txt && \
    pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856

# Create non-root user
RUN useradd -m -u 1000 stampbot && \
    mkdir -p /app && \
    chown -R stampbot:stampbot /app

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=stampbot:stampbot stampbot/ ./stampbot/
COPY --chown=stampbot:stampbot pyproject.toml settings.toml ./

# Switch to non-root user
USER stampbot

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Run application
CMD ["python", "-m", "stampbot"]
