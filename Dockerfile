# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32

FROM python:3.14@sha256:8edbf9e42c7fb168b9c523718ed907117e6d2e60f5889c0c499bbda3a787da53 AS builder

# Set working directory
WORKDIR /app

# Copy dependency files for layer caching
COPY constraints.txt requirements.txt ./

# Install security-patched, pinned pip first, then dependencies. The runtime
# stage copies this upgraded pip installation from the builder.
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    pip install --no-cache-dir --require-hashes -r constraints.txt && \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    pip install --no-cache-dir --require-hashes -r requirements.txt

# Production stage
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

# The release workflow injects its computed version once for every runtime
# surface. Unversioned local builds deliberately fall back to package metadata
# or 0.0.0+unknown instead of claiming to be a published release.
ARG STAMPBOT_VERSION
ENV STAMPBOT_VERSION=${STAMPBOT_VERSION}

# Apply current Debian security updates, then create the non-root user
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -m -u 1000 stampbot && \
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
