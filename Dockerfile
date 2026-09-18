# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32

FROM python:3.14@sha256:4fad23465a06cc5149a541fbec6f87e234a64dc0550f6bfdd2d290d8f03240df AS builder

# Set working directory
WORKDIR /app

# Copy dependency files for layer caching
COPY constraints.txt requirements.txt ./

# Install security-patched, pinned pip first, then dependencies. Only the
# installed dependencies reach the runtime stage; pip and setuptools are
# removed there.
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    pip install --no-cache-dir --require-hashes -r constraints.txt && \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    pip install --no-cache-dir --require-hashes -r requirements.txt

# Production stage
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4

# The release workflow injects its computed version once for every runtime
# surface. Unversioned local builds deliberately fall back to package metadata
# or 0.0.0+unknown instead of claiming to be a published release.
ARG STAMPBOT_VERSION
ENV STAMPBOT_VERSION=${STAMPBOT_VERSION}

# Apply current Debian security updates, then create the non-root user. This is
# what clears fixable base-image CVEs, so it must not become cache-stable: the
# per-commit STAMPBOT_VERSION above invalidates this layer on every build. Keep
# that ARG/ENV pair above this RUN.
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -m -u 1000 stampbot && \
    mkdir -p /app && \
    chown -R stampbot:stampbot /app

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages

# Nothing in the container runs pip, setuptools, or pkg_resources: the app
# starts with `python -m stampbot` and the health check uses urllib. Verified by
# importing the whole runtime stack with all three blocked.
#
# The scan of this image reports msgpack 1.1.2 (GHSA-6v7p-g79w-8964) and
# setuptools 70.3.0 (CVE-2025-47273) as PyPI packages, and a jaraco.context
# advisory already sits in .trivyignore. None of the three is declared in
# requirements.txt, so they arrive with the base image or the pip installation
# rather than with our dependencies. Remove the packaging tooling wholesale
# instead of guessing which path each finding takes. Deleting packages nothing
# uses beats asserting they are harmless: a VEX statement on
# `pkg:pypi/setuptools` would also mask a future finding in a genuine
# dependency of that name.
#
# Uninstall before rm so the RECORD-driven removal takes the console scripts in
# /usr/local/bin and setuptools' `distutils-precedence.pth`, which would
# otherwise error on every interpreter start. The rm then sweeps what layering
# the builder's pip over the base image's leaves behind, including the
# /usr/local/bin/pip symlink that only one of the two RECORDs lists. The final
# check fails the build if any of the three is still importable, so a change to
# the base image cannot quietly reintroduce them, and ensurepip's bundled wheel
# goes too so a scanner that reads inside archives cannot bring the same
# findings back.
RUN python -m pip uninstall --yes --break-system-packages setuptools wheel pip && \
    rm -rf /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.14/site-packages/setuptools \
           /usr/local/lib/python3.14/site-packages/setuptools-*.dist-info \
           /usr/local/lib/python3.14/site-packages/pkg_resources \
           /usr/local/lib/python3.14/site-packages/_distutils_hack \
           /usr/local/lib/python3.14/site-packages/distutils-precedence.pth \
           /usr/local/lib/python3.14/site-packages/wheel \
           /usr/local/lib/python3.14/site-packages/wheel-*.dist-info \
           /usr/local/lib/python3.14/ensurepip/_bundled \
           /usr/local/bin/pip* && \
    python -c "import importlib.util as u, sys; \
left = [m for m in ('pip', 'setuptools', 'pkg_resources') if u.find_spec(m)]; \
sys.exit('still present: ' + repr(left)) if left else None"

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
