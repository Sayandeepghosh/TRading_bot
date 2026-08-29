# ---------------------------------------------------------------------------
#  One image, every free host.
#
#  Works unchanged on Render, Hugging Face Spaces, Google Cloud Run, Fly.io and
#  a plain Oracle Cloud VM. Each of those sets $PORT; run.py reads it.
#
#  Build:  docker build -t equity-analyser .
#  Run:    docker run -p 8000:8000 -e PORT=8000 -v analyser-cache:/app/.cache equity-analyser
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# Python 3.12 rather than 3.14: every wheel this project needs is prebuilt for
# 3.12 on both amd64 and arm64, so the image builds without a compiler on the
# ARM instances that the free tiers hand out.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Kolkata \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

# tzdata so IST session detection is correct; curl for the health check.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY run.py ./
COPY config ./config
COPY src ./src

# Non-root, and the cache directory must be writable by that user.
RUN useradd --create-home --uid 10001 analyser \
    && mkdir -p /app/.cache \
    && chown -R analyser:analyser /app
USER analyser

VOLUME ["/app/.cache"]

EXPOSE 8000

# Generous start period: the first boot runs a full scan in the background.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

CMD ["python", "run.py"]
