# Trust7 Gatekeeper backend — container image with Tesseract available.
#
# ADDITIVE. This changes nothing about the current deploy. render.yaml still says
# `runtime: python` with a pip buildCommand, and the live service keeps using it.
# This file exists so the OCR coverage proxy in `ingestion/fidelity.py` CAN run,
# because Render's native Python runtime installs from requirements.txt and has no
# way to `apt-get install tesseract-ocr`. Without the binary that metric reports
# itself unavailable — which is correct behaviour, just not useful.
#
# ## Build context is the REPOSITORY ROOT, not backend/
#
#     docker build -f backend/Dockerfile -t trust7-gatekeeper-api .
#
# `docker build backend/` FAILS, and not for a trivial reason. `backend/rubric.py`
# resolves the rubric as `<its own directory>/../rubric/rubric.json`, so the file it
# needs lives at the repository root, OUTSIDE backend/. A build context of backend/
# cannot see it, and the app would start and then fail every review with
# FileNotFoundError.
#
# So the image mirrors the repository layout — /app/backend and /app/rubric — and
# that relative lookup resolves unchanged, with no RUBRIC_PATH override and no code
# change. On Render's native runtime `rootDir: backend` achieves the same thing by
# checking out the whole repo; this reproduces it rather than working around it.
#
# ## If this is ever wired into render.yaml
#
# Deliberately NOT done here. It would need `runtime: docker`,
# `dockerfilePath: ./backend/Dockerfile` and `dockerContext: .` — and note that
# `rootDir: backend` must be REMOVED, or the context is scoped to backend/ and the
# rubric goes missing exactly as above. Also add `pytesseract` to requirements.txt
# at that point, or keep the explicit install below.

FROM python:3.13-slim

# render.yaml pins PYTHON_VERSION 3.13.5. The `3.13-slim` tag tracks the latest
# 3.13.x patch, so this is the same minor line but not byte-identical on patch
# level. Pin to `python:3.13.5-slim` if lockstep matters more than security
# updates; the app has no 3.13.x-specific behaviour either way.

# Flushed logs and no .pyc files. The first matters on Render: buffered stdout
# means a crash loop shows an empty log, which is the worst way to debug one.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# --------------------------------------------------------------------------- #
# System packages
#
# `tesseract-ocr-eng` is named EXPLICITLY rather than left to come in as a
# Recommends of `tesseract-ocr`. Without the language data the binary installs and
# `tesseract --version` succeeds, then every call fails at runtime with "Error
# opening data file ... eng.traineddata" — a failure that looks like our code and
# is not. Naming it costs one word and removes that whole class of confusion.
#
# Nothing else is needed: every other runtime dependency (pillow via reportlab,
# lxml via python-docx, cryptography via pypdf) ships manylinux wheels for
# CPython 3.13, so there is no compiler in this image and no build-essential.
# --------------------------------------------------------------------------- #
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# Dependencies before application code, so an edit to a .py file does not
# re-resolve and re-download every wheel.
COPY backend/requirements.txt ./

# Two things happen in this one layer.
#
# ## The optional build-time CA
#
# A no-op on an ordinary network, and REQUIRED on one that re-terminates TLS —
# a corporate MITM proxy, or the sandbox this image was first verified in. Without
# it pip fails with "self-signed certificate in certificate chain" and the build
# stops at this line.
#
#     docker build -f backend/Dockerfile \
#       --secret id=extra_ca,src=/path/to/ca-bundle.crt \
#       -t trust7-gatekeeper-api .
#
# `required=false`, so omitting the secret skips the branch entirely and adds
# nothing to the image — a normal build is byte-identical to one without this.
# The CA is trusted EXPLICITLY and only for this layer; nothing here disables
# certificate verification, and no `--trusted-host` or `PYTHONHTTPSVERIFY` appears
# anywhere in this file. `PIP_CERT` is set rather than relying on
# `update-ca-certificates` alone because pip ships its own certifi bundle and does
# not read the system trust store.
#
# ## pytesseract, and why it is not in requirements.txt
#
# It is the Python binding for the binary installed above, so it is useless outside
# this image — and putting it in requirements.txt would make the NATIVE deploy
# install a package whose binary can never be present. Keeping it here means
# requirements.txt continues to describe exactly what the live service needs, and
# this image adds exactly what it adds.
#
# The version tracks `requirements-dev.txt`, where the harness and test suite pin
# it. That is a second place to remember on an upgrade; the alternative was a third
# requirements file for one line.
RUN --mount=type=secret,id=extra_ca,required=false \
    set -eu; \
    if [ -s /run/secrets/extra_ca ]; then \
        echo "Trusting a build-time CA for package downloads."; \
        cp /run/secrets/extra_ca /usr/local/share/ca-certificates/build-proxy-ca.crt; \
        update-ca-certificates; \
        export PIP_CERT=/etc/ssl/certs/ca-certificates.crt; \
    fi; \
    pip install -r requirements.txt; \
    pip install pytesseract==0.3.13

# Application code, and the rubric it reads from the parent directory. Both are
# copied explicitly rather than with a bare `COPY . .`, so nothing else in the
# repository can arrive in the image by accident.
COPY backend/ ./
COPY rubric/ /app/rubric/

# Runs unprivileged, and owns the directory it writes to. `config.DATA_DIR`
# defaults to ./local-data relative to the working directory, and the app creates
# uploads, reviews and status files there on the first request — as root that would
# leave root-owned files behind on any mounted volume.
RUN useradd --create-home --shell /bin/bash trust7 \
    && mkdir -p /app/backend/local-data \
    && chown -R trust7:trust7 /app
USER trust7

# Documented, not enforced: Render reads `healthCheckPath` from render.yaml and
# ignores this. It is here so `docker run` alone reports whether the app is
# actually serving, and it uses stdlib rather than adding curl to the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request;\
urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/health\").read()"

EXPOSE 8000

# The same command render.yaml's `startCommand` runs, with two deliberate
# differences, both about the container rather than the app:
#
#   * `exec` — so uvicorn REPLACES the shell and becomes PID 1. Without it uvicorn
#     is a child of `sh`, SIGTERM goes to the shell, and the container is killed
#     after the grace period instead of shutting down cleanly on a redeploy.
#   * `${PORT:-8000}` — Render always sets PORT, so this is identical there. The
#     default is what lets the image run under a plain `docker run` with no
#     environment at all; an unset PORT would otherwise pass an empty --port and
#     fail at startup.
#
# Shell form is required for either: the exec form of CMD does no variable
# expansion, so `--port $PORT` would reach uvicorn as the literal string.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
