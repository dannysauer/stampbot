# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32

FROM python:3.14@sha256:be8ccd085666c34273c9dc5607c9842f8b2e3116128aae45148ce164c07ce09d AS builder

# Set working directory
WORKDIR /app

# Copy dependency files for layer caching
COPY constraints.txt requirements.txt ./

# Build into a virtual environment so the runtime stage copies exactly one
# directory holding exactly the declared dependencies, and never the builder's
# system site-packages. Pin the venv's pip to constraints.txt first, install the
# hash-locked requirements with it, then remove pip, setuptools and
# wheel from the venv through their own RECORD files. Nothing installs packages
# at runtime, and shipping the installers only adds scanner findings for code
# that never runs.
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv && \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    /opt/venv/bin/pip install --no-cache-dir --require-hashes -r constraints.txt && \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.txt && \
    /opt/venv/bin/pip uninstall --yes pip setuptools wheel

# Production stage
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

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

# Copy the dependency environment. This is an allowlist: only /opt/venv crosses
# the stage boundary, the runtime's own site-packages is never overlaid, and
# nothing arrives that requirements.txt did not declare. The image-contents CI
# job compares the venv's installed distributions with that file on every pull
# request, so a stray package fails a named check rather than a build.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# The base image still ships its own pip, with a bundled wheel under ensurepip.
# Nothing in the container installs packages, so remove it. Address the base
# interpreter by path: the venv on PATH has no pip and does not see the system
# one. The uninstall follows pip's own RECORD and the wheel directory is
# located through the ensurepip module, so neither depends on a hardcoded
# site-packages layout. The base image adds a `pip -> pip3` symlink outside
# pip's RECORD, so dangling links are swept by what they are rather than by
# name. The checks fail the build if an installer is still
# importable from either interpreter that ships: the base one, and the venv
# one the application runs on.
RUN /usr/local/bin/python3 -m pip uninstall --yes --break-system-packages pip && \
    find /usr/local/bin -xtype l -delete && \
    /usr/local/bin/python3 -c "import ensurepip, pathlib, shutil; \
shutil.rmtree(pathlib.Path(ensurepip.__file__).parent / '_bundled', ignore_errors=True)" && \
    for py in /usr/local/bin/python3 /opt/venv/bin/python; do \
      "$py" -c "import importlib.util as u, sys; \
left = [m for m in ('pip', 'setuptools', 'pkg_resources') if u.find_spec(m)]; \
sys.exit(sys.executable + ' still imports: ' + repr(left)) if left else None" || exit 1; \
    done

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
